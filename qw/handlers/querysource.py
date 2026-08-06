"""QuerySource remote query handler for QWorker.

Provides ``query_handler`` — the function registered as
``"querysource.remote.query_handler"`` in the qworker handler registry.

Registered automatically via the entry_points declared in pyproject.toml:
    [project.entry-points."qworker.handlers"]
    "querysource.remote.query_handler" = "qw.handlers.querysource:query_handler"

QuerySource is imported **lazily** (inside the function body) so that qworker
starts and operates normally even when querysource is not installed.

Interface contract (from querysource sdd/contracts/qworker-query-handler.md):
    async def query_handler(
        slug: str = None,
        conditions: dict = None,
        **options,
    ) -> pd.DataFrame
"""
import asyncio

from navconfig.logging import logging

from qw.conf import WORKER_TASK_TIMEOUT

logger = logging.getLogger('QW.Handler.QuerySource')


async def query_handler(
    slug: str = None,
    conditions: dict = None,
    **options,
):
    """Execute a QuerySource query and return the result DataFrame.

    Dispatched by QWorker when a client calls::

        await client.run("querysource.remote.query_handler", slug, conditions=conditions)

    Two query modes are supported:

    **Slug-based** (most common)::

        await query_handler("my_report", {"store_id": 42})

    **Raw SQL** (slug is None, conditions contains a "query" key)::

        await query_handler(None, {"query": "SELECT * FROM sales", "driver": "pg"})

    Args:
        slug: The query slug defined in QuerySource's slug table. Pass ``None``
              for raw SQL queries.
        conditions: Key-value filter conditions merged into the query dict.
                    For raw SQL queries this dict must contain a ``"query"`` key.
        **options: Reserved for future use; ignored in v1.

    Returns:
        pd.DataFrame: The raw result of the executed query.

    Raises:
        ImportError: If the ``querysource`` package is not installed.
        SlugNotFound: If the slug is not found in the local slug table.
        QueryException: If query execution fails.
        DriverError: If data source connection fails.
        DataNotFound: If the query returns no data.
    """
    try:
        from querysource.queries.obj import QueryObject  # lazy import — avoids heavy startup
    except ImportError as exc:
        raise ImportError(
            "Handler 'querysource.remote.query_handler' not found. "
            "Is querysource installed? Install with: pip install qworker[querysource]"
        ) from exc

    queue: asyncio.Queue = asyncio.Queue()

    # Build the query dict
    if slug is None and conditions and "query" in conditions:
        # Raw SQL query — treat entire conditions dict as the query dict
        query = dict(conditions)
        name = "raw"
    else:
        # Slug-based query
        query = {"slug": slug}
        if conditions:
            query.update(conditions)
        name = slug

    logger.info("Executing query: %s", name)

    query_obj = QueryObject(
        name=name,
        query=query,
        queue=queue,
        request=None,
        # loop= omitted: deprecated since Python 3.8 and removed in Python 3.12
    )

    await query_obj.build_provider()
    await query_obj.query()

    try:
        result_dict = await asyncio.wait_for(queue.get(), timeout=WORKER_TASK_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"query_handler timed out after {WORKER_TASK_TIMEOUT}s waiting for "
            f"QueryObject result (query={name!r}). The query may have raised an "
            "exception without putting a result in the queue."
        ) from exc
    return result_dict[name]
