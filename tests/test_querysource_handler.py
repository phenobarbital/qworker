"""Unit tests for the QuerySource handler (TASK-046)."""
import asyncio
import ast
import sys
import inspect
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock


def make_mock_querysource(sample_df, name_key=None):
    """Build sys.modules patch dict with a mock QueryObject.

    The mock QueryObject captures its constructor args and puts the DataFrame
    into the provided queue when .query() is awaited.

    Args:
        sample_df: DataFrame to put into the queue on .query().
        name_key: Optional fixed name key. If None, uses constructor's name arg.

    Returns:
        Tuple of (mock_modules_dict, captured_args_dict)
    """
    captured = {}

    class MockQueryObject:
        def __init__(self, name, query, queue=None, request=None, loop=None):
            captured["name"] = name
            captured["query"] = query
            captured["queue"] = queue
            self._name = name
            self._queue = queue

        async def build_provider(self):
            pass

        async def query(self):
            key = name_key if name_key is not None else self._name
            await self._queue.put({key: sample_df})

    mock_qs_obj = MagicMock()
    mock_qs_obj.QueryObject = MockQueryObject

    mock_qs = MagicMock()
    mock_qs_queries = MagicMock()
    mock_qs_queries.obj = mock_qs_obj

    modules = {
        "querysource": mock_qs,
        "querysource.queries": mock_qs_queries,
        "querysource.queries.obj": mock_qs_obj,
    }
    return modules, captured


class TestQuerySourceHandler:
    """Tests for qw.handlers.querysource.query_handler."""

    @pytest.fixture
    def sample_df(self):
        """Sample DataFrame returned by mock QueryObject."""
        return pd.DataFrame({"id": [1, 2], "value": [10, 20]})

    @pytest.mark.asyncio
    async def test_slug_query_returns_dataframe(self, sample_df):
        """query_handler('slug', conditions) returns a DataFrame."""
        mock_modules, _ = make_mock_querysource(sample_df)

        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, mock_modules
        ):
            # Force reimport inside test scope
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            from qw.handlers.querysource import query_handler
            result = await query_handler("test_slug", conditions={"store_id": 42})

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_slug_query_passes_correct_args(self, sample_df):
        """Slug-based query merges slug and conditions into query dict."""
        mock_modules, captured = make_mock_querysource(sample_df)

        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, mock_modules
        ):
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            from qw.handlers.querysource import query_handler
            await query_handler("my_slug", conditions={"store_id": 42})

        assert captured["name"] == "my_slug"
        assert captured["query"]["slug"] == "my_slug"
        assert captured["query"]["store_id"] == 42

    @pytest.mark.asyncio
    async def test_raw_sql_query(self, sample_df):
        """slug=None + conditions with 'query' key triggers raw SQL path."""
        mock_modules, captured = make_mock_querysource(sample_df, name_key="raw")

        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, mock_modules
        ):
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            from qw.handlers.querysource import query_handler
            await query_handler(
                None,
                conditions={"query": "SELECT * FROM t", "driver": "pg"}
            )

        assert captured["name"] == "raw"
        assert "query" in captured["query"]

    @pytest.mark.asyncio
    async def test_missing_querysource_raises_import_error(self):
        """Calling handler without querysource installed raises ImportError."""
        # Block the querysource import by setting modules to None
        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, {
                "querysource": None,
                "querysource.queries": None,
                "querysource.queries.obj": None,
            }
        ):
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            from qw.handlers.querysource import query_handler

            with pytest.raises(ImportError, match="querysource.remote.query_handler"):
                await query_handler("some_slug")

    def test_module_level_no_querysource_import(self):
        """querysource must NOT be imported at module level — only inside function body."""
        import importlib
        import qw.handlers.querysource as mod
        # Re-read the source directly from file
        source = inspect.getsource(mod)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if node.col_offset == 0:  # top-level import
                    if isinstance(node, ast.ImportFrom) and node.module:
                        assert "querysource" not in node.module, (
                            "querysource must NOT be imported at module level; "
                            f"found: {ast.unparse(node)}"
                        )

    @pytest.mark.asyncio
    async def test_no_conditions_uses_slug_only(self, sample_df):
        """query_handler with no conditions still builds correct slug query."""
        mock_modules, captured = make_mock_querysource(sample_df)

        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, mock_modules
        ):
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            from qw.handlers.querysource import query_handler
            await query_handler("bare_slug")

        assert captured["query"] == {"slug": "bare_slug"}

    def test_importable(self):
        """qw.handlers.querysource.query_handler is importable and async."""
        from qw.handlers.querysource import query_handler
        assert asyncio.iscoroutinefunction(query_handler)

    def test_package_importable(self):
        """qw.handlers package is importable."""
        import qw.handlers  # noqa: F401

    @pytest.mark.asyncio
    async def test_queryobject_exception_propagates(self):
        """An exception raised by QueryObject.query() propagates out of query_handler."""
        captured = {}

        class FailingQueryObject:
            def __init__(self, name, query, queue=None, request=None, loop=None):
                captured["name"] = name
                self._queue = queue

            async def build_provider(self):
                pass

            async def query(self):
                # Raise without putting anything in the queue
                raise RuntimeError("QuerySource internal error")

        mock_qs_obj = MagicMock()
        mock_qs_obj.QueryObject = FailingQueryObject

        mock_qs = MagicMock()
        mock_qs_queries = MagicMock()
        mock_qs_queries.obj = mock_qs_obj

        modules = {
            "querysource": mock_qs,
            "querysource.queries": mock_qs_queries,
            "querysource.queries.obj": mock_qs_obj,
        }

        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, modules
        ):
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            from qw.handlers.querysource import query_handler

            with pytest.raises(RuntimeError, match="QuerySource internal error"):
                await query_handler("test_slug")

    @pytest.mark.asyncio
    async def test_queue_timeout_raises_timeout_error(self, sample_df):
        """If QueryObject never puts a result in the queue, TimeoutError is raised."""
        class HangingQueryObject:
            def __init__(self, name, query, queue=None, request=None, loop=None):
                pass

            async def build_provider(self):
                pass

            async def query(self):
                # Never puts anything into the queue — simulates a silent failure
                pass

        mock_qs_obj = MagicMock()
        mock_qs_obj.QueryObject = HangingQueryObject

        mock_qs = MagicMock()
        mock_qs_queries = MagicMock()
        mock_qs_queries.obj = mock_qs_obj

        modules = {
            "querysource": mock_qs,
            "querysource.queries": mock_qs_queries,
            "querysource.queries.obj": mock_qs_obj,
        }

        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            sys.modules, modules
        ):
            if "qw.handlers.querysource" in sys.modules:
                del sys.modules["qw.handlers.querysource"]
            # Patch WORKER_TASK_TIMEOUT to a very small value to keep test fast
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "qw.conf.WORKER_TASK_TIMEOUT", 0.05
            ):
                from qw.handlers.querysource import query_handler

                with pytest.raises(TimeoutError, match="timed out"):
                    await query_handler("hanging_slug")
