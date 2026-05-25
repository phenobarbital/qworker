"""Local (in-process) execution backend for QWorker.

Wraps the existing TaskExecutor as a BaseExecutionBackend. This is the
default backend — tasks without a container_config are dispatched here.
"""
import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from .base import BackendRegistry, BaseExecutionBackend
from .models import TaskResult


class LocalBackend(BaseExecutionBackend):
    """Executes tasks in-process via the existing TaskExecutor.

    This backend preserves exact current QWorker behavior. No process or
    container isolation — tasks run inside the worker's event loop.

    Internal tracking dicts:
        _tasks: task_id -> asyncio.Task (running async task)
        _results: task_id -> TaskResult (completed result)
        _status: task_id -> str (current status string)
    """

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._results: dict[uuid.UUID, TaskResult] = {}
        self._status: dict[uuid.UUID, str] = {}
        self.logger = logging.getLogger("QW.Backend.Local")

    async def dispatch(self, task: Any) -> uuid.UUID:
        """Run a task via TaskExecutor and track it.

        Creates an asyncio.Task so dispatch() is non-blocking. The actual
        execution happens in the background; use poll()/get_result() to
        retrieve the outcome.

        Args:
            task: A QueueWrapper instance to execute.

        Returns:
            The task's UUID (task.id).
        """
        # Import here to avoid circular imports at module level
        from qw.executor import TaskExecutor

        task_id: uuid.UUID = task.id if hasattr(task, "id") else uuid.uuid4()
        self._status[task_id] = "running"

        async def _run_and_store() -> None:
            start = time.monotonic()
            try:
                executor = TaskExecutor(task)
                result = await executor.run()
                success = not isinstance(result, BaseException)
                error_str = str(result) if isinstance(result, BaseException) else None
                actual_result = None if isinstance(result, BaseException) else result
                self._results[task_id] = TaskResult(
                    task_id=task_id,
                    success=success,
                    result=actual_result,
                    error=error_str,
                    execution_time=round(time.monotonic() - start, 4),
                    backend="local",
                )
                self._status[task_id] = "completed" if success else "failed"
            except Exception as exc:
                self._results[task_id] = TaskResult(
                    task_id=task_id,
                    success=False,
                    result=None,
                    error=str(exc),
                    execution_time=round(time.monotonic() - start, 4),
                    backend="local",
                )
                self._status[task_id] = "failed"
                self.logger.error("LocalBackend task %s failed: %s", task_id, exc)
            finally:
                self._tasks.pop(task_id, None)

        t = asyncio.create_task(_run_and_store())
        self._tasks[task_id] = t
        self.logger.debug("Dispatched task %s to LocalBackend", task_id)
        return task_id

    async def poll(self, task_id: uuid.UUID) -> str:
        """Return current task status.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            One of: 'pending', 'running', 'completed', 'failed'.
        """
        return self._status.get(task_id, "pending")

    async def get_result(self, task_id: uuid.UUID) -> TaskResult:
        """Return the TaskResult for a completed task.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            TaskResult with success/failure information.

        Raises:
            KeyError: If task_id is unknown (was never dispatched).
            RuntimeError: If the task has not yet completed.
        """
        if task_id not in self._results and task_id not in self._status:
            raise KeyError(f"Unknown task_id: {task_id}")
        if task_id not in self._results:
            raise RuntimeError(
                f"Task {task_id} has not completed yet "
                f"(status: {self._status.get(task_id, 'unknown')})"
            )
        return self._results[task_id]

    async def cancel(self, task_id: uuid.UUID) -> bool:
        """Cancel a running asyncio.Task.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            True if the task was found and cancelled, False otherwise.
        """
        t = self._tasks.get(task_id)
        if t is not None and not t.done():
            t.cancel()
            self._status[task_id] = "failed"
            self.logger.info("Cancelled task %s", task_id)
            return True
        return False

    async def cleanup(self, task_id: uuid.UUID) -> None:
        """Remove internal tracking entries for a completed task.

        Args:
            task_id: The UUID returned by dispatch().
        """
        self._tasks.pop(task_id, None)
        self._results.pop(task_id, None)
        self._status.pop(task_id, None)
        self.logger.debug("Cleaned up task %s from LocalBackend", task_id)

    async def health_check(self) -> dict:
        """Return LocalBackend health status.

        Returns:
            Dict with 'status' and 'active_tasks' count.
        """
        active = len(
            [t for t in self._tasks.values() if not t.done()]
        )
        return {
            "status": "ok",
            "active_tasks": active,
        }


# Register the local backend in the default registry
_default_registry = BackendRegistry()
_default_registry.register("local", LocalBackend)
