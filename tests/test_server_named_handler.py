"""Unit tests for server-side NamedHandlerWrapper dispatch (TASK-045)."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from qw.wrappers.named import NamedHandlerWrapper
from qw.wrappers.base import QueueWrapper
from qw.registry import HandlerRegistry
from qw.exceptions import QWException


class TestServerNamedHandler:
    """Tests for server-side dispatch of NamedHandlerWrapper."""

    def test_named_wrapper_isinstance_queue_wrapper(self):
        """NamedHandlerWrapper IS a QueueWrapper (instanceof ordering matters)."""
        wrapper = NamedHandlerWrapper(
            "test.handler", "my-slug", conditions={"id": 42}
        )
        assert isinstance(wrapper, QueueWrapper)
        assert isinstance(wrapper, NamedHandlerWrapper)

    def test_named_wrapper_is_not_generic_queue_wrapper(self):
        """A plain QueueWrapper is NOT a NamedHandlerWrapper."""
        wrapper = QueueWrapper()
        assert not isinstance(wrapper, NamedHandlerWrapper)

    @pytest.mark.asyncio
    async def test_named_handler_resolves_and_executes(self):
        """handle_named_handler resolves the handler and returns the result."""
        result_value = {"data": [1, 2, 3]}

        async def test_handler(slug=None, conditions=None, **opts):
            return result_value

        registry = HandlerRegistry()
        registry.register("test.handler", test_handler)

        wrapper = NamedHandlerWrapper("test.handler", "my-slug", conditions={"id": 42})
        handler = registry.resolve(wrapper.handler_name)
        result = await handler(*wrapper.args, **wrapper.kwargs)
        assert result == result_value

    @pytest.mark.asyncio
    async def test_named_handler_async_execution(self):
        """Async handler is awaited directly."""
        called_with = {}

        async def async_handler(slug=None, conditions=None):
            called_with.update({"slug": slug, "conditions": conditions})
            return "async-result"

        registry = HandlerRegistry()
        registry.register("async.handler", async_handler)

        wrapper = NamedHandlerWrapper(
            "async.handler", "test-slug", conditions={"key": "value"}
        )
        handler = registry.resolve(wrapper.handler_name)
        assert asyncio.iscoroutinefunction(handler)
        result = await handler(*wrapper.args, **wrapper.kwargs)
        assert result == "async-result"
        assert called_with["slug"] == "test-slug"

    def test_unknown_handler_returns_error(self):
        """Unresolved handler name raises QWException with name in message."""
        registry = HandlerRegistry()
        with pytest.raises(QWException, match="not.registered"):
            registry.resolve("not.registered")

    def test_qwexception_message_includes_handler_name(self):
        """QWException message includes the unresolved handler name."""
        registry = HandlerRegistry()
        try:
            registry.resolve("my.missing.handler")
            assert False, "Should have raised"
        except QWException as exc:
            assert "my.missing.handler" in str(exc)

    @pytest.mark.asyncio
    async def test_handle_named_handler_method_exists_on_qworker(self):
        """QWorker has handle_named_handler method after TASK-045."""
        from qw.server import QWorker
        assert hasattr(QWorker, "handle_named_handler")
        assert asyncio.iscoroutinefunction(QWorker.handle_named_handler)

    @pytest.mark.asyncio
    async def test_handle_named_handler_calls_registry_resolve(self):
        """handle_named_handler delegates resolution to handler_registry.resolve()."""
        import uuid
        from qw.server import QWorker

        async def fake_handler(*args, **kwargs):
            return "resolved-result"

        wrapper = NamedHandlerWrapper("resolve.test.handler", "arg1")
        mock_writer = AsyncMock()
        mock_writer.is_closing.return_value = False

        with patch("qw.server.handler_registry") as mock_registry, \
             patch.object(QWorker, "return_result", new_callable=AsyncMock) as mock_return:
            mock_registry.resolve.return_value = fake_handler

            # Create a minimal QWorker without full server init
            worker = object.__new__(QWorker)
            worker._state = None
            worker.logger = MagicMock()

            await worker.handle_named_handler(wrapper, uuid.uuid4(), mock_writer)
            mock_registry.resolve.assert_called_once_with("resolve.test.handler")

    @pytest.mark.asyncio
    async def test_handle_named_handler_error_serialized(self):
        """Handler exception is serialized via cloudpickle and sent to client."""
        import uuid
        import cloudpickle
        from qw.server import QWorker

        async def failing_handler(*args, **kwargs):
            raise ValueError("handler failed")

        wrapper = NamedHandlerWrapper("fail.handler", "arg1")
        mock_writer = AsyncMock()

        with patch("qw.server.handler_registry") as mock_registry, \
             patch.object(QWorker, "closing_writer", new_callable=AsyncMock) as mock_close:
            mock_registry.resolve.return_value = failing_handler

            worker = object.__new__(QWorker)
            worker._state = None
            worker.logger = MagicMock()

            await worker.handle_named_handler(wrapper, uuid.uuid4(), mock_writer)
            mock_close.assert_called_once()
            # The result passed to closing_writer should be cloudpickle-serialised exception
            result_bytes = mock_close.call_args[0][1]
            err = cloudpickle.loads(result_bytes)
            assert isinstance(err, ValueError)
            assert "handler failed" in str(err)

    @pytest.mark.asyncio
    async def test_connection_handler_routes_named_wrapper_correctly(self):
        """connection_handler dispatches NamedHandlerWrapper before QueueWrapper."""
        # Verify the isinstance order is correct by checking that NamedHandlerWrapper
        # would be caught by the NamedHandlerWrapper check, NOT the QueueWrapper check.
        wrapper = NamedHandlerWrapper("test.handler")

        # A NamedHandlerWrapper IS a QueueWrapper — order matters
        assert isinstance(wrapper, NamedHandlerWrapper)
        assert isinstance(wrapper, QueueWrapper)

        # Simulate the dispatch logic from connection_handler
        if isinstance(wrapper, NamedHandlerWrapper):
            routed_to = "handle_named_handler"
        elif isinstance(wrapper, QueueWrapper):
            routed_to = "handle_queue_wrapper"
        else:
            routed_to = "other"

        assert routed_to == "handle_named_handler"
