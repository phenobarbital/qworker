"""Unit tests for QueueWrapper container_config extension — TASK-038."""
import cloudpickle
import pytest

from qw.backends.models import ContainerConfig
from qw.wrappers.base import QueueWrapper
from qw.wrappers.func import FuncWrapper


class TestQueueWrapperExtension:
    """Tests for the container_config attribute on QueueWrapper."""

    def test_default_no_config(self):
        """QueueWrapper without container_config has None."""
        async def dummy():
            pass

        w = QueueWrapper(coro=dummy)
        assert w.container_config is None

    def test_with_container_config(self):
        """QueueWrapper stores container_config when provided."""
        async def dummy():
            pass

        cfg = ContainerConfig(backend="docker", image="python:3.12")
        w = QueueWrapper(coro=dummy, container_config=cfg)
        assert w.container_config is not None
        assert w.container_config.backend == "docker"
        assert w.container_config.image == "python:3.12"

    def test_container_config_setter(self):
        """container_config property setter works correctly."""
        async def dummy():
            pass

        w = QueueWrapper(coro=dummy)
        cfg = ContainerConfig(backend="k8s", image="worker:latest")
        w.container_config = cfg
        assert w.container_config is cfg

    def test_container_config_does_not_bleed_into_kwargs(self):
        """container_config is popped from kwargs and not stored in self.kwargs."""
        async def dummy():
            pass

        cfg = ContainerConfig(backend="docker", image="x")
        w = QueueWrapper(coro=dummy, container_config=cfg, some_other_kwarg="value")
        # container_config must NOT appear in self.kwargs
        assert "container_config" not in w.kwargs
        # other kwargs should still be there
        assert w.kwargs.get("some_other_kwarg") == "value"

    def test_funcwrapper_inherits(self):
        """FuncWrapper inherits container_config from QueueWrapper."""
        def fn():
            pass

        cfg = ContainerConfig(backend="k8s", image="worker:latest")
        w = FuncWrapper("localhost", fn, container_config=cfg)
        assert w.container_config is not None
        assert w.container_config.backend == "k8s"

    def test_funcwrapper_backward_compat(self):
        """FuncWrapper without container_config still works (None)."""
        def fn():
            pass

        w = FuncWrapper("localhost", fn)
        assert w.container_config is None

    def test_cloudpickle_roundtrip(self):
        """container_config survives cloudpickle serialization roundtrip."""
        async def dummy():
            pass

        cfg = ContainerConfig(backend="docker", image="python:3.12")
        w = QueueWrapper(coro=dummy, container_config=cfg)
        data = cloudpickle.dumps(w)
        w2 = cloudpickle.loads(data)
        assert w2.container_config is not None
        assert w2.container_config.backend == "docker"
        assert w2.container_config.image == "python:3.12"

    def test_cloudpickle_roundtrip_none_config(self):
        """Wrapper without container_config serializes/deserializes correctly."""
        async def dummy():
            pass

        w = QueueWrapper(coro=dummy)
        data = cloudpickle.dumps(w)
        w2 = cloudpickle.loads(data)
        assert w2.container_config is None

    def test_k8s_config_roundtrip(self):
        """K8s ContainerConfig survives cloudpickle roundtrip."""
        async def dummy():
            pass

        cfg = ContainerConfig(
            backend="k8s",
            image="registry.example.com/worker:latest",
            namespace="prod",
        )
        w = QueueWrapper(coro=dummy, container_config=cfg)
        data = cloudpickle.dumps(w)
        w2 = cloudpickle.loads(data)
        assert w2.container_config.namespace == "prod"

    def test_existing_wrapper_attributes_unaffected(self):
        """Adding container_config does not affect existing wrapper attributes."""
        async def dummy():
            pass

        w = QueueWrapper(coro=dummy, queued=True)
        assert w.queued is True
        assert w.retries == 0
        assert w.container_config is None
