"""Unit tests for LocalBackend — TASK-034."""
import asyncio
import uuid

import pytest

from qw.backends.local import LocalBackend
from qw.backends.models import TaskResult
from qw.wrappers.base import QueueWrapper


pytestmark = pytest.mark.asyncio


class TestLocalBackend:
    """Tests for LocalBackend execution and tracking."""

    @pytest.fixture
    def backend(self):
        """Fresh LocalBackend instance per test."""
        return LocalBackend()

    async def test_dispatch_returns_uuid(self, backend):
        """dispatch() returns the task's UUID."""
        async def dummy():
            return 42

        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        assert isinstance(task_id, uuid.UUID)
        assert task_id == task.id

    async def test_poll_running_then_completed(self, backend):
        """poll() eventually returns 'completed' after task finishes."""
        async def dummy():
            return 42

        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        # Give asyncio a chance to run the task
        await asyncio.sleep(0.2)
        status = await backend.poll(task_id)
        assert status == "completed"

    async def test_poll_unknown_returns_pending(self, backend):
        """poll() returns 'pending' for unknown task_id."""
        status = await backend.poll(uuid.uuid4())
        assert status == "pending"

    async def test_get_result_success(self, backend):
        """get_result() returns a successful TaskResult."""
        async def dummy():
            return 42

        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        await asyncio.sleep(0.2)
        result = await backend.get_result(task_id)
        assert isinstance(result, TaskResult)
        assert result.success is True
        assert result.result == 42
        assert result.backend == "local"

    async def test_get_result_failure(self, backend):
        """get_result() captures exceptions as failed TaskResult."""
        async def failing():
            raise ValueError("test error")

        task = QueueWrapper(coro=failing)
        task_id = await backend.dispatch(task)
        await asyncio.sleep(0.2)
        result = await backend.get_result(task_id)
        assert isinstance(result, TaskResult)
        assert result.success is False

    async def test_get_result_unknown_raises(self, backend):
        """get_result() raises KeyError for unknown task_id."""
        with pytest.raises(KeyError):
            await backend.get_result(uuid.uuid4())

    async def test_health_check(self, backend):
        """health_check() returns ok status."""
        health = await backend.health_check()
        assert health["status"] == "ok"
        assert "active_tasks" in health

    async def test_cleanup_removes_tracking(self, backend):
        """cleanup() removes task from internal tracking."""
        async def dummy():
            return 1

        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        await asyncio.sleep(0.2)
        await backend.cleanup(task_id)
        # After cleanup, task_id should be unknown
        status = await backend.poll(task_id)
        assert status == "pending"  # unknown → pending

    async def test_cancel_running_task(self, backend):
        """cancel() cancels a running task."""
        async def long_running():
            await asyncio.sleep(10)
            return "done"

        task = QueueWrapper(coro=long_running)
        task_id = await backend.dispatch(task)
        # Give it a moment to start running
        await asyncio.sleep(0.05)
        cancelled = await backend.cancel(task_id)
        assert cancelled is True

    async def test_cancel_unknown_returns_false(self, backend):
        """cancel() returns False for unknown task_id."""
        result = await backend.cancel(uuid.uuid4())
        assert result is False

    async def test_execution_time_recorded(self, backend):
        """TaskResult.execution_time is a positive float."""
        async def dummy():
            return "ok"

        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        await asyncio.sleep(0.2)
        result = await backend.get_result(task_id)
        assert result.execution_time >= 0.0
