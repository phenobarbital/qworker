"""Execution backend abstraction for QWorker.

Defines the BaseExecutionBackend ABC that all execution backends implement.
"""
import uuid
from abc import ABC, abstractmethod
from typing import Any

from .models import TaskResult


class BaseExecutionBackend(ABC):
    """Abstract base class defining the interface for all execution backends.

    All methods are async. Concrete backends (Local, Docker, K8s) must
    implement every abstract method.
    """

    @abstractmethod
    async def dispatch(self, task: Any) -> uuid.UUID:
        """Submit a task for execution.

        Args:
            task: A QueueWrapper instance to execute.

        Returns:
            A UUID that uniquely identifies this dispatch for subsequent
            poll/get_result/cancel/cleanup calls.
        """

    @abstractmethod
    async def poll(self, task_id: uuid.UUID) -> str:
        """Check the status of a dispatched task.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            One of: 'pending', 'running', 'completed', 'failed'.
        """

    @abstractmethod
    async def get_result(self, task_id: uuid.UUID) -> TaskResult:
        """Retrieve the result of a completed task.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            A TaskResult containing the outcome.

        Raises:
            KeyError: If task_id is unknown.
            RuntimeError: If the task has not yet completed.
        """

    @abstractmethod
    async def cancel(self, task_id: uuid.UUID) -> bool:
        """Cancel a running or pending task.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            True if the task was successfully cancelled, False otherwise.
        """

    @abstractmethod
    async def cleanup(self, task_id: uuid.UUID) -> None:
        """Release resources associated with a completed task.

        Should be called after get_result() to free containers, pods,
        or internal tracking state.

        Args:
            task_id: The UUID returned by dispatch().
        """

    @abstractmethod
    async def health_check(self) -> dict:
        """Return backend health status.

        Returns:
            A dict with at minimum a 'status' key ('ok', 'degraded',
            'error') and backend-specific details.
        """


