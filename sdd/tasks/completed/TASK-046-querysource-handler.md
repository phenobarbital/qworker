# TASK-046: QuerySource Handler

**Feature**: qworker-query-handler
**Spec**: `sdd/specs/qworker-query-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-042
**Assigned-to**: unassigned

---

## Context

This task implements the actual querysource query handler — the function that
receives a query slug + conditions from `RemoteExecutor`, executes the query
locally using QuerySource's `QueryObject`, and returns the result DataFrame.

This handler matches the interface contract defined in querysource's
`sdd/contracts/qworker-query-handler.md`.

Implements Spec Module 5.

---

## Scope

- Create `qw/handlers/__init__.py` (empty or with utility imports)
- Create `qw/handlers/querysource.py` with `query_handler()` function
- Handler supports slug-based queries AND raw SQL queries (slug=None)
- querysource imported LAZILY inside the function body (not at module level)
- Handler raises helpful ImportError if querysource is not installed
- Write unit tests with mocked QueryObject

**NOT in scope**: Entry_points registration in pyproject.toml (TASK-047),
registry infrastructure (TASK-042), server dispatch (TASK-045).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/handlers/__init__.py` | CREATE | Package init (can be empty) |
| `qw/handlers/querysource.py` | CREATE | query_handler function |
| `tests/test_querysource_handler.py` | CREATE | Unit tests |

---

## Implementation Notes

### Handler Implementation

The handler must match the querysource contract exactly:

```python
# qw/handlers/querysource.py
import asyncio
from navconfig.logging import logging

logger = logging.getLogger('QW.Handler.QuerySource')


async def query_handler(slug: str = None, conditions: dict = None, **options):
    """Execute a QuerySource query and return the result DataFrame.

    Args:
        slug: The query slug to execute. None for raw SQL queries.
        conditions: Key-value filter conditions merged into the query dict.
        **options: Reserved for future use.

    Returns:
        pd.DataFrame: The raw query result.

    Raises:
        ImportError: If querysource is not installed.
        SlugNotFound: If the slug is not in the local slug table.
        QueryException: If query execution fails.
        DriverError: If data source connection fails.
    """
    try:
        from querysource.queries.obj import QueryObject
    except ImportError as e:
        raise ImportError(
            "querysource is not installed. Install with: pip install qworker[querysource]"
        ) from e

    queue = asyncio.Queue()

    # Build the query dict
    if slug is None and conditions and "query" in conditions:
        # Raw SQL query
        query = dict(conditions)
        name = "raw"
    else:
        # Slug-based query
        query = {"slug": slug}
        if conditions:
            query.update(conditions)
        name = slug

    logger.info(f"Executing query: {name}")

    query_obj = QueryObject(
        name=name,
        query=query,
        queue=queue,
        request=None,
        loop=asyncio.get_running_loop(),
    )

    await query_obj.build_provider()
    await query_obj.query()

    result_dict = await queue.get()
    return result_dict[name]
```

### Key Constraints

- **Lazy import**: `from querysource.queries.obj import QueryObject` MUST be inside
  the function body, NOT at module level. This ensures qworker starts fast and
  operates normally without querysource installed.
- **No request context**: `request=None` — there is no HTTP request on the worker side.
- **Error propagation**: Let querysource exceptions (`SlugNotFound`, `QueryException`,
  `DriverError`, `DataNotFound`) propagate as-is. The cloudpickle protocol serializes
  them and the client receives the original exception.
- **Raw SQL support**: When `slug is None` and `conditions` contains a `"query"` key,
  treat the entire conditions dict as the query dict.

### References in Codebase

```python
# From querysource contract (sdd/contracts/qworker-query-handler.md):
# Handler signature:
async def query_handler(slug: str = None, conditions: dict = None, **options) -> pd.DataFrame

# QueryObject constructor (querysource/queries/obj.py:26):
class QueryObject(BaseQuery):
    def __init__(self, name, query, conditions=None, request=None, queue=None, loop=None)

# QueryObject methods:
    async def build_provider(self)   # line 65
    async def query(self)            # line 183
    # Puts {name: DataFrame} into queue at line 203
```

---

## Codebase Contract

### Verified External Imports (querysource — optional)

```python
# These imports exist in querysource (verified 2026-05-26):
from querysource.queries.obj import QueryObject    # querysource/queries/obj.py:20
# QueryObject.__init__(name, query, conditions=None, request=None, queue=None, loop=None)
# QueryObject.build_provider() — async, line 65
# QueryObject.query() — async, line 183, puts {name: result} into queue

# Exceptions that may be raised:
# querysource.exceptions.SlugNotFound   — line 34
# querysource.exceptions.QueryException — line 6
# querysource.exceptions.DriverError    — line 58
# querysource.exceptions.DataNotFound   — line 48
```

### Verified Internal Imports

```python
from navconfig.logging import logging    # used across qworker
import asyncio                           # stdlib
```

### Does NOT Exist

- ~~`qw/handlers/`~~ — directory does not exist; this task creates it
- ~~`qw/handlers/__init__.py`~~ — does not exist; this task creates it
- ~~`qw/handlers/querysource.py`~~ — does not exist; this task creates it
- ~~`querysource.remote`~~ — no remote module in querysource; handler lives in qworker
- ~~`querysource.remote.query_handler`~~ — not a real Python import path; it's the
  string identifier used by QClient.run() for handler registry lookup

---

## Acceptance Criteria

- [ ] `query_handler(slug, conditions)` function implemented
- [ ] Slug-based queries work: `query_handler("my_slug", {"store_id": 42})`
- [ ] Raw SQL queries work: `query_handler(None, {"query": "SELECT...", "driver": "pg"})`
- [ ] querysource imported lazily (inside function, not at module level)
- [ ] Missing querysource raises helpful ImportError
- [ ] querysource exceptions propagate as-is (not wrapped)
- [ ] All tests pass: `pytest tests/test_querysource_handler.py -v`
- [ ] Import works: `from qw.handlers.querysource import query_handler`

---

## Test Specification

```python
# tests/test_querysource_handler.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd


class TestQuerySourceHandler:
    @pytest.fixture
    def mock_query_object(self):
        qo = MagicMock()
        qo.build_provider = AsyncMock()
        qo.query = AsyncMock()
        return qo

    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame({"id": [1, 2], "value": [10, 20]})

    async def test_slug_query(self, mock_query_object, sample_df):
        async def mock_query():
            await mock_query_object._queue.put({"test_slug": sample_df})
        mock_query_object.query = mock_query

        with patch("qw.handlers.querysource.QueryObject") as MockQO:
            # Set up the mock to capture the queue and use it
            captured_queue = None
            def create_qo(name, query, queue, request, loop):
                nonlocal captured_queue
                captured_queue = queue
                mock_query_object._queue = queue
                return mock_query_object
            MockQO.side_effect = create_qo
            mock_query_object.query = AsyncMock(
                side_effect=lambda: captured_queue.put({"test_slug": sample_df})
            )

            from qw.handlers.querysource import query_handler
            result = await query_handler("test_slug", conditions={"store_id": 42})
            assert isinstance(result, pd.DataFrame)

    async def test_raw_sql_query(self, mock_query_object, sample_df):
        with patch("qw.handlers.querysource.QueryObject") as MockQO:
            captured_queue = None
            def create_qo(name, query, queue, request, loop):
                nonlocal captured_queue
                captured_queue = queue
                return mock_query_object
            MockQO.side_effect = create_qo
            mock_query_object.query = AsyncMock(
                side_effect=lambda: captured_queue.put({"raw": sample_df})
            )

            from qw.handlers.querysource import query_handler
            result = await query_handler(
                None,
                conditions={"query": "SELECT * FROM t", "driver": "pg"}
            )
            MockQO.assert_called_once()
            call_kwargs = MockQO.call_args
            assert call_kwargs[1]["name"] == "raw" or call_kwargs[0][0] == "raw"

    async def test_missing_querysource_raises(self):
        with patch.dict("sys.modules", {"querysource": None, "querysource.queries": None, "querysource.queries.obj": None}):
            # Force reimport to trigger ImportError
            import importlib
            import qw.handlers.querysource as mod
            # The ImportError happens inside the function, not at import time
            # This test verifies the lazy import pattern

    def test_module_level_no_querysource_import(self):
        """Verify querysource is NOT imported at module level."""
        import ast
        import inspect
        from qw.handlers import querysource as mod
        source = inspect.getsource(mod)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if node.col_offset == 0:  # top-level import
                    if isinstance(node, ast.ImportFrom) and node.module:
                        assert "querysource" not in node.module, \
                            "querysource must NOT be imported at module level"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qworker-query-handler.spec.md` for full context
2. **Read the contract** referenced in the spec: querysource's interface contract
3. **Check dependencies** — verify TASK-042 is in `sdd/tasks/completed/`
4. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
5. **Create** `qw/handlers/__init__.py` (empty)
6. **Implement** `qw/handlers/querysource.py` following the contract and pattern above
7. **Write tests** in `tests/test_querysource_handler.py`
8. **Verify** all acceptance criteria are met
9. **Move this file** to `sdd/tasks/completed/TASK-046-querysource-handler.md`
10. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-05-26
**Notes**: All 8 tests pass. query_handler() with lazy querysource import,
slug-based and raw SQL modes. Tests use sys.modules patching for lazy import.

**Deviations from spec**: none
