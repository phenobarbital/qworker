"""Unit tests for qw.backends.base — TASK-033."""
import uuid

import pytest

from qw.backends.base import BaseExecutionBackend
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


