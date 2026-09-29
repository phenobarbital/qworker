"""Unit tests for qw.wrappers.named.NamedHandlerWrapper."""
import uuid
import pytest
import cloudpickle

from qw.wrappers.named import NamedHandlerWrapper
from qw.wrappers.base import QueueWrapper


class TestNamedHandlerWrapper:
    def test_init_stores_handler_name(self):
        """Constructor stores handler_name as accessible property."""
        w = NamedHandlerWrapper("test.handler", "arg1", key="val")
        assert w.handler_name == "test.handler"

    def test_extends_queue_wrapper(self):
        """NamedHandlerWrapper must be a QueueWrapper subclass for dispatch ordering."""
        w = NamedHandlerWrapper("test.handler")
        assert isinstance(w, QueueWrapper)

    def test_queued_defaults_false(self):
        """Named handlers are always immediate — queued must default to False."""
        w = NamedHandlerWrapper("test.handler")
        assert w.queued is False

    def test_queued_can_be_overridden(self):
        """queued=False is the default but can be explicitly overridden."""
        w = NamedHandlerWrapper("test.handler", queued=True)
        assert w.queued is True

    def test_args_and_kwargs(self):
        """Positional args and keyword args are stored correctly."""
        w = NamedHandlerWrapper("test.handler", "slug_name", conditions={"id": 1})
        assert w.args == ("slug_name",)
        assert w.kwargs["conditions"] == {"id": 1}

    def test_args_empty_when_none_given(self):
        """With only handler_name, args is empty."""
        w = NamedHandlerWrapper("test.handler")
        assert w.args == ()

    def test_kwargs_no_queued_key(self):
        """The 'queued' key must NOT appear in self.kwargs (consumed by QueueWrapper)."""
        w = NamedHandlerWrapper("test.handler", conditions={"x": 1})
        assert "queued" not in w.kwargs

    def test_repr_includes_handler_name(self):
        """__repr__ must include the handler name for log readability."""
        w = NamedHandlerWrapper("querysource.remote.query_handler")
        assert "querysource.remote.query_handler" in repr(w)

    def test_str_includes_handler_name(self):
        """__str__ must include the handler name for log readability."""
        w = NamedHandlerWrapper("querysource.remote.query_handler")
        assert "querysource.remote.query_handler" in str(w)

    def test_cloudpickle_roundtrip(self):
        """cloudpickle.dumps/loads roundtrip preserves name, args, kwargs, queued."""
        original = NamedHandlerWrapper(
            "test.handler", "my-slug", conditions={"store_id": 42}
        )
        data = cloudpickle.dumps(original)
        restored = cloudpickle.loads(data)
        assert restored.handler_name == "test.handler"
        assert restored.args == ("my-slug",)
        assert restored.kwargs["conditions"] == {"store_id": 42}
        assert restored.queued is False

    def test_cloudpickle_roundtrip_empty(self):
        """Roundtrip works even when no args/kwargs are provided."""
        original = NamedHandlerWrapper("test.handler")
        data = cloudpickle.dumps(original)
        restored = cloudpickle.loads(data)
        assert restored.handler_name == "test.handler"
        assert restored.args == ()
        assert restored.queued is False

    def test_has_uuid_id(self):
        """QueueWrapper assigns a UUID id — must survive in NamedHandlerWrapper."""
        w = NamedHandlerWrapper("test.handler")
        assert isinstance(w.id, uuid.UUID)

    def test_multiple_args(self):
        """Multiple positional arguments are all preserved."""
        w = NamedHandlerWrapper("test.handler", "a", "b", "c")
        assert w.args == ("a", "b", "c")

    def test_import_from_package(self):
        """Can be imported from qw.wrappers package."""
        from qw.wrappers import NamedHandlerWrapper as NHW  # noqa: F401
        assert NHW is NamedHandlerWrapper

    def test_in_all(self):
        """NamedHandlerWrapper is listed in qw.wrappers.__all__."""
        import qw.wrappers as wrappers_module
        assert "NamedHandlerWrapper" in wrappers_module.__all__

    @pytest.mark.asyncio
    async def test_call_raises_not_implemented(self):
        """__call__ is not meant to be used client-side; must raise NotImplementedError."""
        w = NamedHandlerWrapper("test.handler")
        with pytest.raises(NotImplementedError):
            await w()
