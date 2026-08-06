"""Backend dispatcher for QWorker container execution routing.

Routes tasks to the appropriate execution backend based on:
1. Task-level container_config (highest priority)
2. Worker-side task mapping file
3. Resource monitor overflow state
4. Default: LocalBackend

Implements Spec Section 3 (Module 8) of launch-docker-k8s.
"""
import asyncio
import fnmatch
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .base import BaseExecutionBackend
from .local import LocalBackend
from .models import ContainerConfig, ContainerTaskMapping, TaskResult
from .monitor import ResourceMonitor
from ..conf import (
    CONTAINER_DEFAULT_TIMEOUT,
    CONTAINER_FIRE_FORGET_GRACE,
    CONTAINER_POLL_INTERVAL,
    CONTAINER_TASK_MAPPING_FILE,
    WORKER_RETRY_COUNT,
    WORKER_RETRY_INTERVAL,
)


class BackendDispatcher:
    """Routes tasks to the appropriate execution backend.

    Priority resolution order for resolve_backend():
    1. Task's container_config (explicit per-task routing)
    2. Worker-side task mapping file (name/pattern matching)
    3. ResourceMonitor overflow state (k8s if overflowing)
    4. Default: LocalBackend

    Args:
        local_backend: The local (in-process) backend. Always required.
        docker_backend: Optional Docker backend instance.
        k8s_backend: Optional Kubernetes backend instance.
        resource_monitor: Optional ResourceMonitor for overflow decisions.
        task_mappings: List of ContainerTaskMapping loaded from config file.
    """

    def __init__(
        self,
        local_backend: LocalBackend,
        docker_backend: Optional[Any] = None,
        k8s_backend: Optional[Any] = None,
        resource_monitor: Optional[ResourceMonitor] = None,
        task_mappings: Optional[list[ContainerTaskMapping]] = None,
    ) -> None:
        self.logger = logging.getLogger("QW.Backend.Dispatcher")
        self._local = local_backend
        self._docker = docker_backend
        self._k8s = k8s_backend
        self._monitor = resource_monitor
        self._mappings: list[ContainerTaskMapping] = task_mappings or []

        if task_mappings is None and CONTAINER_TASK_MAPPING_FILE:
            try:
                loaded = self.load_task_mappings(CONTAINER_TASK_MAPPING_FILE)
                self._mappings = loaded
                self.logger.info(
                    "Loaded %d task mappings from %s",
                    len(loaded),
                    CONTAINER_TASK_MAPPING_FILE,
                )
            except Exception as exc:
                self.logger.warning(
                    "Failed to load task mappings from %s: %s",
                    CONTAINER_TASK_MAPPING_FILE,
                    exc,
                )

    def resolve_backend(self, task: Any) -> BaseExecutionBackend:
        """Determine which backend should execute the given task.

        Resolution priority:
        1. Task's container_config.backend (explicit)
        2. Worker-side task mapping (pattern match on task name)
        3. Resource overflow → K8s
        4. Default → Local

        Args:
            task: A QueueWrapper (or similar object with optional container_config).

        Returns:
            The BaseExecutionBackend instance to use.

        Raises:
            RuntimeError: If container_config specifies a backend that isn't
                         configured (e.g., docker backend not available).
        """
        # 1. Task-level container_config takes priority
        container_config: Optional[ContainerConfig] = getattr(
            task, "container_config", None
        )
        if container_config is not None:
            if container_config.backend == "docker":
                if self._docker is None:
                    raise RuntimeError(
                        "Task requested Docker backend but DockerBackend is not configured"
                    )
                return self._docker
            elif container_config.backend == "k8s":
                if self._k8s is None:
                    raise RuntimeError(
                        "Task requested K8s backend but K8sBackend is not configured"
                    )
                return self._k8s

        # 2. Worker-side task mapping file
        task_name = self._get_task_name(task)
        if task_name and self._mappings:
            backend = self._match_task_mapping(task_name)
            if backend is not None:
                return backend

        # 3. Resource overflow — route to K8s if overflowing
        if self._monitor is not None and self._k8s is not None:
            if self._monitor.should_overflow():
                self.logger.debug(
                    "Resource overflow active — routing task %s to K8s", task_name
                )
                return self._k8s

        # 4. Default to local
        return self._local

    def _get_task_name(self, task: Any) -> Optional[str]:
        """Extract a string name from a task for mapping lookups.

        Args:
            task: A QueueWrapper or similar object.

        Returns:
            Task name string, or None if not determinable.
        """
        # Try common task name attributes
        for attr in ("task_name", "name", "__name__"):
            val = getattr(task, attr, None)
            if val and isinstance(val, str):
                return val
        # Fall back to the coro name if available
        coro = getattr(task, "coro", None)
        if coro is not None:
            name = getattr(coro, "__name__", None)
            if name:
                return name
        return None

    def _match_task_mapping(self, task_name: str) -> Optional[BaseExecutionBackend]:
        """Check task name against configured mappings using fnmatch patterns.

        Args:
            task_name: The task function/name to match.

        Returns:
            Matched backend, or None if no pattern matches.
        """
        for mapping in self._mappings:
            if fnmatch.fnmatch(task_name, mapping.task_pattern):
                backend_name = mapping.config.backend
                self.logger.debug(
                    "Task '%s' matched mapping pattern '%s' → %s",
                    task_name,
                    mapping.task_pattern,
                    backend_name,
                )
                if backend_name == "docker":
                    if self._docker is not None:
                        return self._docker
                    self.logger.warning(
                        "Task '%s' mapped to Docker but no Docker backend configured",
                        task_name,
                    )
                elif backend_name == "k8s":
                    if self._k8s is not None:
                        return self._k8s
                    self.logger.warning(
                        "Task '%s' mapped to K8s but no K8s backend configured",
                        task_name,
                    )
        return None

    def should_use_container(self, task: Any) -> bool:
        """Quick check: would this task be routed to a container backend?

        Used by QueueManager to decide whether to invoke the dispatcher
        for a task that has no explicit container_config.

        Args:
            task: A QueueWrapper or similar object.

        Returns:
            True if any non-local backend would be selected.
        """
        # Check task mapping
        task_name = self._get_task_name(task)
        if task_name and self._mappings:
            for mapping in self._mappings:
                if fnmatch.fnmatch(task_name, mapping.task_pattern):
                    if mapping.config.backend in ("docker", "k8s"):
                        return True

        # Check resource overflow
        if self._monitor is not None and self._k8s is not None:
            if self._monitor.should_overflow():
                return True

        return False

    async def dispatch_and_track(self, task: Any) -> TaskResult:
        """Dispatch task to resolved backend and optionally poll until done.

        Args:
            task: A QueueWrapper with optional container_config.

        Returns:
            TaskResult with execution outcome.
        """
        container_config: Optional[ContainerConfig] = getattr(
            task, "container_config", None
        )
        fire_and_forget = (
            container_config.fire_and_forget if container_config else False
        )
        timeout = (
            container_config.timeout if container_config else CONTAINER_DEFAULT_TIMEOUT
        )
        if timeout is None:
            timeout = CONTAINER_DEFAULT_TIMEOUT

        backend = self.resolve_backend(task)
        start = time.monotonic()

        # Retry loop
        last_error: Optional[str] = None
        for attempt in range(max(1, WORKER_RETRY_COUNT)):
            try:
                task_id: uuid.UUID = await backend.dispatch(task)
            except Exception as exc:
                last_error = str(exc)
                self.logger.warning(
                    "Dispatch attempt %d failed for task: %s", attempt + 1, exc
                )
                if attempt < WORKER_RETRY_COUNT - 1:
                    await asyncio.sleep(WORKER_RETRY_INTERVAL)
                continue

            # Fire-and-forget: return immediately after dispatch.
            # Schedule cleanup in a background task after a grace period so
            # containers are not leaked. The grace period allows the container
            # to finish and its output to be collected even though the caller
            # does not wait for a result.
            if fire_and_forget:
                async def _deferred_cleanup(
                    _backend: BaseExecutionBackend,
                    _task_id: uuid.UUID,
                    _grace: int,
                ) -> None:
                    await asyncio.sleep(_grace)
                    try:
                        await _backend.cleanup(_task_id)
                    except Exception as _exc:
                        self.logger.debug(
                            "Fire-and-forget cleanup error for %s: %s",
                            _task_id,
                            _exc,
                        )

                asyncio.create_task(
                    _deferred_cleanup(backend, task_id, CONTAINER_FIRE_FORGET_GRACE)
                )
                return TaskResult(
                    task_id=task_id,
                    success=True,
                    result=None,
                    error=None,
                    execution_time=round(time.monotonic() - start, 4),
                    backend=type(backend).__name__.lower().replace("backend", ""),
                )

            # Poll until done or timeout
            result = await self._poll_until_done(
                backend, task_id, timeout=timeout, start=start
            )
            if result.success or attempt >= WORKER_RETRY_COUNT - 1:
                return result

            # Retry on failure
            self.logger.warning(
                "Task %s failed on attempt %d, retrying in %ds",
                task_id, attempt + 1, WORKER_RETRY_INTERVAL,
            )
            await asyncio.sleep(WORKER_RETRY_INTERVAL)
            last_error = result.error

        # All retries exhausted
        elapsed = round(time.monotonic() - start, 4)
        return TaskResult(
            task_id=task.id if hasattr(task, "id") else uuid.uuid4(),
            success=False,
            result=None,
            error=last_error or "All dispatch attempts failed",
            execution_time=elapsed,
            backend=type(backend).__name__.lower().replace("backend", ""),
        )

    async def _poll_until_done(
        self,
        backend: BaseExecutionBackend,
        task_id: uuid.UUID,
        timeout: int,
        start: float,
    ) -> TaskResult:
        """Poll backend until task completes or times out.

        Args:
            backend: The backend that owns the task.
            task_id: UUID returned by dispatch().
            timeout: Maximum seconds to wait.
            start: monotonic start time for elapsed calculation.

        Returns:
            TaskResult with outcome.
        """
        deadline = start + timeout
        poll_interval = CONTAINER_POLL_INTERVAL
        status: str = "pending"

        while time.monotonic() < deadline:
            try:
                status = await backend.poll(task_id)
            except Exception as exc:
                self.logger.warning("Poll error for %s: %s", task_id, exc)
                status = "failed"

            if status in ("completed", "failed"):
                break

            await asyncio.sleep(poll_interval)

        # Timed out — treat as failure
        if time.monotonic() >= deadline and status not in ("completed", "failed"):
            self.logger.warning(
                "Task %s timed out after %ds", task_id, timeout
            )
            try:
                await backend.cancel(task_id)
            except Exception:
                pass
            return TaskResult(
                task_id=task_id,
                success=False,
                result=None,
                error=f"Task timed out after {timeout}s",
                execution_time=round(time.monotonic() - start, 4),
                backend=type(backend).__name__.lower().replace("backend", ""),
            )

        try:
            return await backend.get_result(task_id)
        except Exception as exc:
            return TaskResult(
                task_id=task_id,
                success=False,
                result=None,
                error=str(exc),
                execution_time=round(time.monotonic() - start, 4),
                backend=type(backend).__name__.lower().replace("backend", ""),
            )
        finally:
            try:
                await backend.cleanup(task_id)
            except Exception as cleanup_exc:
                self.logger.debug(
                    "Cleanup error for %s: %s", task_id, cleanup_exc
                )

    @staticmethod
    def load_task_mappings(filepath: str) -> list[ContainerTaskMapping]:
        """Load task-to-backend mappings from a YAML or TOML file.

        File format (YAML example):
        ```yaml
        mappings:
          - task_pattern: "ml_*"
            backend: k8s
            image: ml-worker:latest
          - task_pattern: "report_*"
            backend: docker
            image: report-worker:latest
        ```

        File format (TOML example):
        ```toml
        [[mappings]]
        task_pattern = "ml_*"
        backend = "k8s"
        image = "ml-worker:latest"
        ```

        Args:
            filepath: Path to YAML or TOML mapping file.

        Returns:
            List of ContainerTaskMapping instances.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the file format is not supported or content invalid.
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Task mapping file not found: {filepath}")

        suffix = path.suffix.lower()
        raw: dict = {}

        if suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                raise ImportError(
                    "PyYAML is required to load YAML mapping files: "
                    "uv pip install pyyaml"
                )
            with open(path) as f:
                raw = yaml.safe_load(f) or {}

        elif suffix == ".toml":
            import tomllib
            with open(path, "rb") as f:
                raw = tomllib.load(f)

        else:
            raise ValueError(
                f"Unsupported mapping file format: {suffix}. Use .yaml or .toml"
            )

        mappings_data = raw.get("mappings", [])
        if not isinstance(mappings_data, list):
            raise ValueError("Mapping file must contain a 'mappings' list")

        result = []
        for item in mappings_data:
            try:
                item = dict(item)  # defensive copy
                if "task_pattern" not in item:
                    raise ValueError("Missing required key 'task_pattern'")
                task_pattern = item.pop("task_pattern")
                # Reconstruct nested ContainerConfig from flat YAML/TOML keys.
                # The documented format uses flat keys (backend, image, env, …)
                # rather than a nested 'config' dict.
                if "config" in item:
                    # Already nested — accept both formats
                    config = ContainerConfig(**item.pop("config"))
                else:
                    config = ContainerConfig(**item)
                result.append(
                    ContainerTaskMapping(task_pattern=task_pattern, config=config)
                )
            except Exception as exc:
                raise ValueError(f"Invalid mapping entry {item!r}: {exc}") from exc

        return result
