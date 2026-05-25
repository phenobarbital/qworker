"""Unit tests for qw.backends.base — TASK-033."""
import uuid

import pytest

from qw.backends.base import BackendRegistry, BaseExecutionBackend
from qw.backends.models import TaskResult


class TestBaseExecutionBackend:
    """Tests for BaseExecutionBackend ABC."""

    def test_cannot_instantiate(self):
        """BaseExecutionBackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseExecutionBackend()

    def test_concrete_subclass_must_implement_all_methods(self):
        """A concrete subclass must implement all 6 abstract methods."""
        class DummyBackend(BaseExecutionBackend):
            async def dispatch(self, task):
                return uuid.uuid4()

            async def poll(self, task_id):
                return "completed"

            async def get_result(self, task_id):
                return TaskResult(
                    task_id=task_id,
                    success=True,
                    result=None,
                    error=None,
                    execution_time=0.1,
                    backend="dummy",
                )

            async def cancel(self, task_id):
                return True

            async def cleanup(self, task_id):
                pass

            async def health_check(self):
                return {"status": "ok"}

        backend = DummyBackend()
        assert isinstance(backend, BaseExecutionBackend)

    def test_partial_subclass_cannot_instantiate(self):
        """A subclass missing some abstract methods cannot be instantiated."""
        class PartialBackend(BaseExecutionBackend):
            async def dispatch(self, task):
                return uuid.uuid4()

            # Missing poll, get_result, cancel, cleanup, health_check

        with pytest.raises(TypeError):
            PartialBackend()

    def test_all_methods_are_abstract(self):
        """Verify all 6 expected methods are abstract."""
        abstract_methods = BaseExecutionBackend.__abstractmethods__
        expected = {"dispatch", "poll", "get_result", "cancel", "cleanup", "health_check"}
        assert expected == abstract_methods


class TestBackendRegistry:
    """Tests for BackendRegistry."""

    def test_register_and_get(self):
        """BackendRegistry stores and retrieves a backend class by name."""
        registry = BackendRegistry()

        class MockBackend(BaseExecutionBackend):
            async def dispatch(self, task):
                return uuid.uuid4()

            async def poll(self, task_id):
                return "completed"

            async def get_result(self, task_id):
                return None

            async def cancel(self, task_id):
                return True

            async def cleanup(self, task_id):
                pass

            async def health_check(self):
                return {}

        registry.register("mock", MockBackend)
        assert registry.get("mock") is MockBackend

    def test_get_nonexistent_raises(self):
        """BackendRegistry.get raises KeyError for unknown backend names."""
        registry = BackendRegistry()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_list_backends(self):
        """BackendRegistry.list_backends returns a list."""
        registry = BackendRegistry()
        assert isinstance(registry.list_backends(), list)

    def test_list_backends_includes_registered(self):
        """BackendRegistry.list_backends includes newly registered backends."""
        registry = BackendRegistry()

        class MockBackend(BaseExecutionBackend):
            async def dispatch(self, task):
                return uuid.uuid4()

            async def poll(self, task_id):
                return "completed"

            async def get_result(self, task_id):
                return None

            async def cancel(self, task_id):
                return True

            async def cleanup(self, task_id):
                pass

            async def health_check(self):
                return {}

        registry.register("alpha", MockBackend)
        registry.register("beta", MockBackend)
        backends = registry.list_backends()
        assert "alpha" in backends
        assert "beta" in backends

    def test_register_overwrites_existing(self):
        """Registering the same name twice overwrites the previous entry."""
        registry = BackendRegistry()

        class BackendV1(BaseExecutionBackend):
            async def dispatch(self, task):
                return uuid.uuid4()

            async def poll(self, task_id):
                return "completed"

            async def get_result(self, task_id):
                return None

            async def cancel(self, task_id):
                return True

            async def cleanup(self, task_id):
                pass

            async def health_check(self):
                return {}

        class BackendV2(BackendV1):
            pass

        registry.register("test", BackendV1)
        registry.register("test", BackendV2)
        assert registry.get("test") is BackendV2
