"""Unit tests for QClient string-based dispatch (TASK-044)."""
import pytest
from qw.client import QClient
from qw.wrappers.named import NamedHandlerWrapper
from qw.wrappers import FuncWrapper


class TestClientStringDispatch:
    @pytest.mark.asyncio
    async def test_string_creates_named_wrapper(self):
        """get_wrapped_function('handler.name', ...) returns NamedHandlerWrapper."""
        client = QClient(worker_list=[("localhost", 8888)])
        result = client.get_wrapped_function(
            "test.handler", "localhost", "my-slug", conditions={"id": 1}
        )
        assert isinstance(result, NamedHandlerWrapper)
        assert result.handler_name == "test.handler"

    @pytest.mark.asyncio
    async def test_string_wrapper_has_args(self):
        """NamedHandlerWrapper created from string carries positional args."""
        client = QClient(worker_list=[("localhost", 8888)])
        result = client.get_wrapped_function(
            "test.handler", "localhost", "slug", conditions={"id": 42}
        )
        assert "slug" in result.args
        assert result.kwargs["conditions"] == {"id": 42}

    @pytest.mark.asyncio
    async def test_string_wrapper_queued_is_false(self):
        """NamedHandlerWrapper is always immediate (queued=False)."""
        client = QClient(worker_list=[("localhost", 8888)])
        result = client.get_wrapped_function("test.handler", "localhost")
        assert result.queued is False

    @pytest.mark.asyncio
    async def test_function_still_returns_partial(self):
        """Non-string fn still follows the existing code path (returns partial/FuncWrapper)."""
        client = QClient(worker_list=[("localhost", 8888)])

        def my_func(x):
            return x

        result = client.get_wrapped_function(my_func, "localhost", 42)
        assert not isinstance(result, NamedHandlerWrapper)

    @pytest.mark.asyncio
    async def test_function_with_use_wrapper_returns_func_wrapper(self):
        """use_wrapper=True still produces FuncWrapper for callable fn."""
        client = QClient(worker_list=[("localhost", 8888)])

        def my_func(x):
            return x

        result = client.get_wrapped_function(
            my_func, "localhost", 42, use_wrapper=True
        )
        assert isinstance(result, FuncWrapper)
        assert not isinstance(result, NamedHandlerWrapper)

    @pytest.mark.asyncio
    async def test_backward_compat_existing_behavior(self):
        """Non-string fn follows the original code path unchanged."""
        client = QClient(worker_list=[("localhost", 8888)])

        async def async_fn(x):
            return x

        result = client.get_wrapped_function(async_fn, "localhost", "arg1")
        assert not isinstance(result, NamedHandlerWrapper)
        assert callable(result)

    @pytest.mark.asyncio
    async def test_string_no_args(self):
        """String dispatch works even without positional/keyword arguments."""
        client = QClient(worker_list=[("localhost", 8888)])
        result = client.get_wrapped_function("bare.handler", "localhost")
        assert isinstance(result, NamedHandlerWrapper)
        assert result.handler_name == "bare.handler"
        assert result.args == ()
        assert result.kwargs == {}

    @pytest.mark.asyncio
    async def test_string_with_only_kwargs(self):
        """String dispatch with only keyword arguments."""
        client = QClient(worker_list=[("localhost", 8888)])
        result = client.get_wrapped_function(
            "bare.handler", "localhost", slug="my-slug"
        )
        assert isinstance(result, NamedHandlerWrapper)
        assert result.kwargs["slug"] == "my-slug"

    @pytest.mark.asyncio
    async def test_string_check_before_isinstance(self):
        """String detection branch runs before TaskWrapper/FuncWrapper check."""
        # Ensures no TypeError when passing a str to isinstance(fn, ...)
        client = QClient(worker_list=[("localhost", 8888)])
        result = client.get_wrapped_function("any.string.name", "localhost")
        assert isinstance(result, NamedHandlerWrapper)
