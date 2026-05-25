"""Execution backend abstraction for QWorker.

Defines the BaseExecutionBackend ABC that all execution backends implement,
and BackendRegistry for registering/resolving backends by name.
"""
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional

from .models import ContainerConfig, TaskResult


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


class BackendRegistry:
    """Registry for resolving execution backends by name.

    A simple dict-based registry. Backends are registered by name string
    (e.g., 'local', 'docker', 'k8s') and resolved on demand.

    Example:
        registry = BackendRegistry()
        registry.register("local", LocalBackend)
        backend_cls = registry.get("local")
        backend = backend_cls()
    """

    def __init__(self) -> None:
        self._backends: dict[str, type[BaseExecutionBackend]] = {}
        self.logger = logging.getLogger("QW.Backend")

    def register(self, name: str, cls: type[BaseExecutionBackend]) -> None:
        """Register a backend class under the given name.

        Args:
            name: Short identifier string (e.g., 'local', 'docker', 'k8s').
            cls: A concrete subclass of BaseExecutionBackend.
        """
        self._backends[name] = cls
        self.logger.debug("Registered backend: %s -> %s", name, cls.__name__)

    def get(self, name: str) -> type[BaseExecutionBackend]:
        """Retrieve a registered backend class by name.

        Args:
            name: The name used in register().

        Returns:
            The registered backend class.

        Raises:
            KeyError: If no backend is registered under that name.
        """
        if name not in self._backends:
            raise KeyError(
                f"No execution backend registered under {name!r}. "
                f"Available backends: {list(self._backends.keys())}"
            )
        return self._backends[name]

    def list_backends(self) -> list[str]:
        """Return a list of all registered backend names.

        Returns:
            List of registered name strings.
        """
        return list(self._backends.keys())
