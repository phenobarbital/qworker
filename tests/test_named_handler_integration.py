"""Integration tests for named handler dispatch end-to-end (TASK-047).

Tests the full roundtrip:
  QClient.get_wrapped_function(str) → NamedHandlerWrapper → cloudpickle → registry
  resolve → handler execute → result

No running server is required — the wire transfer is simulated via cloudpickle
serialization/deserialization.
"""
import pytest
import asyncio
import cloudpickle
import pandas as pd

from qw.client import QClient
from qw.registry import HandlerRegistry
from qw.wrappers.named import NamedHandlerWrapper


class TestNamedHandlerIntegration:
    @pytest.fixture
    def registry(self):
        """Fresh registry per test."""
        return HandlerRegistry()

    @pytest.fixture
    def sample_handler(self):
        """A DataFrame-returning async handler for use in tests."""
        async def handler(slug=None, conditions=None, **options):
            return pd.DataFrame({"id": [1, 2], "value": [10, 20]})
        return handler

    @pytest.mark.asyncio
    async def test_client_to_wrapper_roundtrip(self):
        """QClient creates NamedHandlerWrapper, cloudpickle roundtrip preserves it."""
        client = QClient(worker_list=[("localhost", 8888)])
        wrapper = client.get_wrapped_function(
            "test.handler", "localhost", "my-slug", conditions={"id": 42}
        )
        assert isinstance(wrapper, NamedHandlerWrapper)

        # Simulate wire transfer
        data = cloudpickle.dumps(wrapper)
        restored = cloudpickle.loads(data)

        assert restored.handler_name == "test.handler"
        assert "my-slug" in restored.args
        assert restored.kwargs["conditions"] == {"id": 42}

    @pytest.mark.asyncio
    async def test_full_dispatch_roundtrip(self, registry, sample_handler):
        """Full flow: string → wrapper → serialize → resolve → execute → result."""
        registry.register("test.handler", sample_handler)

        # Client side
        client = QClient(worker_list=[("localhost", 8888)])
        wrapper = client.get_wrapped_function(
            "test.handler", "localhost", "my-slug", conditions={"id": 42}
        )

        # Wire transfer
        data = cloudpickle.dumps(wrapper)
        restored = cloudpickle.loads(data)

        # Server side
        handler = registry.resolve(restored.handler_name)
        result = await handler(*restored.args, **restored.kwargs)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_function_dispatch_unchanged(self):
        """Non-string fn still works through existing code path."""
        client = QClient(worker_list=[("localhost", 8888)])

        def my_func(x):
            return x * 2

        wrapper = client.get_wrapped_function(my_func, "localhost", 42)
        assert not isinstance(wrapper, NamedHandlerWrapper)
        assert callable(wrapper)

    @pytest.mark.asyncio
    async def test_mixed_string_and_function_dispatch(self, registry, sample_handler):
        """Both string and function dispatch work in the same client session."""
        registry.register("test.handler", sample_handler)
        client = QClient(worker_list=[("localhost", 8888)])

        # String dispatch
        w1 = client.get_wrapped_function("test.handler", "localhost", "slug1")
        assert isinstance(w1, NamedHandlerWrapper)

        # Function dispatch
        def my_func(x):
            return x

        w2 = client.get_wrapped_function(my_func, "localhost", 42)
        assert not isinstance(w2, NamedHandlerWrapper)

    def test_config_setting_exists(self):
        """HANDLER_ENTRY_POINTS_GROUP config is accessible with correct default."""
        from qw.conf import HANDLER_ENTRY_POINTS_GROUP
        assert HANDLER_ENTRY_POINTS_GROUP == "qworker.handlers"

    @pytest.mark.asyncio
    async def test_wrapper_queued_is_false(self):
        """NamedHandlerWrapper created by client always has queued=False."""
        client = QClient(worker_list=[("localhost", 8888)])
        wrapper = client.get_wrapped_function("my.handler", "localhost")
        assert wrapper.queued is False

    @pytest.mark.asyncio
    async def test_cloudpickle_preserves_handler_name(self):
        """cloudpickle roundtrip preserves the handler name exactly."""
        handler_name = "querysource.remote.query_handler"
        client = QClient(worker_list=[("localhost", 8888)])
        wrapper = client.get_wrapped_function(handler_name, "localhost", "my-slug")

        data = cloudpickle.dumps(wrapper)
        restored = cloudpickle.loads(data)
        assert restored.handler_name == handler_name

    @pytest.mark.asyncio
    async def test_multiple_handlers_dispatched_independently(self, registry):
        """Multiple distinct handlers resolve and execute independently."""
        async def handler_a(slug=None, **opts):
            return pd.DataFrame({"source": ["A"]})

        async def handler_b(slug=None, **opts):
            return pd.DataFrame({"source": ["B"]})

        registry.register("handler.a", handler_a)
        registry.register("handler.b", handler_b)

        result_a = await registry.resolve("handler.a")("slug-a")
        result_b = await registry.resolve("handler.b")("slug-b")

        assert result_a["source"][0] == "A"
        assert result_b["source"][0] == "B"

    def test_pyproject_has_querysource_optional_dep(self):
        """pyproject.toml has querysource optional dependency group."""
        import pathlib
        pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text()
        assert 'querysource = ["querysource"]' in content

    def test_pyproject_has_entry_points(self):
        """pyproject.toml has entry_points for the querysource handler."""
        import pathlib
        pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text()
        assert '[project.entry-points."qworker.handlers"]' in content
        assert '"querysource.remote.query_handler"' in content
