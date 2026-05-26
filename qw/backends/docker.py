"""Docker execution backend for QWorker.

Executes tasks inside Docker containers via the Docker SDK for Python.
Supports local daemon (Unix socket) and remote Docker hosts (TCP/TLS).

The docker package is an optional dependency:
    uv pip install qworker[docker]
"""
import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Optional

import cloudpickle

try:
    import docker
    from docker.errors import APIError, ContainerError, ImageNotFound, NotFound
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

from .base import BaseExecutionBackend
from .models import ContainerConfig, TaskResult


class DockerBackend(BaseExecutionBackend):
    """Executes tasks in ephemeral Docker containers.

    Task data is passed to the container via the TASK_DATA environment variable
    (base64-encoded cloudpickle). Results are read from container stdout.

    Args:
        docker_host: Docker daemon URL. None uses the local socket (from_env).
    """

    def __init__(self, docker_host: Optional[str] = None) -> None:
        if not HAS_DOCKER:
            raise ImportError(
                "Docker SDK is required for DockerBackend. "
                "Install it with: uv pip install 'qworker[docker]'"
            )
        self.logger = logging.getLogger("QW.Backend.Docker")
        if docker_host:
            self._client = docker.DockerClient(base_url=docker_host)
        else:
            self._client = docker.from_env()
        # Maps task_id -> container_id
        self._tasks: dict[uuid.UUID, str] = {}
        # Maps task_id -> ContainerConfig
        self._configs: dict[uuid.UUID, ContainerConfig] = {}
        # Maps task_id -> start time
        self._start_times: dict[uuid.UUID, float] = {}

    def _serialize_task(self, task: Any) -> str:
        """Serialize task to base64-encoded cloudpickle string.

        Args:
            task: Task object to serialize.

        Returns:
            Base64-encoded cloudpickle string.
        """
        return base64.b64encode(cloudpickle.dumps(task)).decode("utf-8")

    def _build_env(self, task: Any, config: ContainerConfig) -> dict[str, str]:
        """Build container environment variables.

        Args:
            task: Task to serialize into TASK_DATA.
            config: ContainerConfig with user-defined env vars.

        Returns:
            Merged environment dict.
        """
        env = dict(config.env)
        env["TASK_DATA"] = self._serialize_task(task)
        env["TASK_FORMAT"] = "cloudpickle"
        return env

    def _build_volumes(self, config: ContainerConfig) -> dict:
        """Build Docker volume mount configuration.

        Parses each volume spec from the list: '/host:/container' (rw) or
        '/host:/container:ro' (read-only).

        Args:
            config: ContainerConfig with volume list.

        Returns:
            Docker SDK volumes dict suitable for containers.run(volumes=...).
        """
        volumes = {}
        for vol in config.volumes:
            parts = vol.split(":")
            if len(parts) < 2:
                self.logger.warning("Skipping malformed volume spec: %r", vol)
                continue
            host_path = parts[0]
            container_path = parts[1]
            mode = parts[2] if len(parts) > 2 else "rw"
            volumes[host_path] = {"bind": container_path, "mode": mode}
        return volumes

    def _build_resource_opts(self, config: ContainerConfig) -> dict:
        """Build resource constraint kwargs for docker run.

        Args:
            config: ContainerConfig with optional resources.

        Returns:
            Dict of Docker SDK resource kwargs.
        """
        opts: dict[str, Any] = {}
        if config.resources:
            if config.resources.cpu_limit:
                opts["cpu_quota"] = _cpu_to_quota(config.resources.cpu_limit)
            if config.resources.memory_limit:
                opts["mem_limit"] = config.resources.memory_limit
        return opts

    async def dispatch(self, task: Any) -> uuid.UUID:
        """Create and start a Docker container for the task.

        Args:
            task: A QueueWrapper instance (must have an id attribute and
                  a container_config with backend='docker').

        Returns:
            The task's UUID.

        Raises:
            ImageNotFound: If the container image does not exist.
            APIError: If Docker daemon returns an error.
        """
        config: ContainerConfig = task.container_config
        task_id: uuid.UUID = task.id if hasattr(task, "id") else uuid.uuid4()
        self._configs[task_id] = config
        self._start_times[task_id] = time.monotonic()

        loop = asyncio.get_running_loop()

        def _create_and_start():
            env = self._build_env(task, config)
            volumes = self._build_volumes(config)
            resource_opts = self._build_resource_opts(config)
            container = self._client.containers.run(
                image=config.image,
                environment=env,
                volumes=volumes,
                detach=True,
                remove=False,  # We remove manually in cleanup()
                restart_policy={"Name": "no"},
                **resource_opts,
            )
            return container.id

        try:
            container_id = await loop.run_in_executor(None, _create_and_start)
            self._tasks[task_id] = container_id
            self.logger.info(
                "Dispatched task %s to container %s (image=%s)",
                task_id, container_id[:12], config.image,
            )
        except ImageNotFound as exc:
            self.logger.error("Image not found for task %s: %s", task_id, exc)
            raise
        except APIError as exc:
            self.logger.error("Docker API error for task %s: %s", task_id, exc)
            raise

        return task_id

    async def poll(self, task_id: uuid.UUID) -> str:
        """Check container status.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            One of: 'pending', 'running', 'completed', 'failed'.
        """
        container_id = self._tasks.get(task_id)
        if container_id is None:
            return "pending"

        loop = asyncio.get_running_loop()

        def _get_status():
            try:
                container = self._client.containers.get(container_id)
                container.reload()
                return container.status
            except NotFound:
                return "exited"

        try:
            status = await loop.run_in_executor(None, _get_status)
        except Exception as exc:
            self.logger.warning("Poll failed for task %s: %s", task_id, exc)
            return "failed"

        status_map = {
            "created": "pending",
            "restarting": "running",
            "running": "running",
            "removing": "running",
            "paused": "running",
            "exited": "completed",
            "dead": "failed",
        }
        return status_map.get(status, "pending")

    async def get_result(self, task_id: uuid.UUID) -> TaskResult:
        """Read container stdout and parse result.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            TaskResult with the container output.

        Raises:
            KeyError: If task_id is unknown.
        """
        container_id = self._tasks.get(task_id)
        if container_id is None:
            raise KeyError(f"Unknown task_id: {task_id}")

        loop = asyncio.get_running_loop()
        start_time = self._start_times.get(task_id, time.monotonic())

        def _get_logs():
            try:
                container = self._client.containers.get(container_id)
                exit_code = container.wait()["StatusCode"]
                logs = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                return exit_code, logs
            except NotFound:
                return -1, ""

        try:
            exit_code, logs = await loop.run_in_executor(None, _get_logs)
        except Exception as exc:
            return TaskResult(
                task_id=task_id,
                success=False,
                result=None,
                error=str(exc),
                execution_time=round(time.monotonic() - start_time, 4),
                backend="docker",
            )

        success = exit_code == 0
        result_data = None
        error_str = None

        if success and logs.strip():
            try:
                result_data = json.loads(logs.strip())
            except (json.JSONDecodeError, ValueError):
                result_data = logs.strip()
        elif not success:
            error_str = f"Container exited with code {exit_code}. Output: {logs[:500]}"

        return TaskResult(
            task_id=task_id,
            success=success,
            result=result_data,
            error=error_str,
            execution_time=round(time.monotonic() - start_time, 4),
            backend="docker",
        )

    async def cancel(self, task_id: uuid.UUID) -> bool:
        """Stop the Docker container.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            True if the container was stopped, False if not found.
        """
        container_id = self._tasks.get(task_id)
        if container_id is None:
            return False

        loop = asyncio.get_running_loop()

        def _stop():
            try:
                container = self._client.containers.get(container_id)
                container.stop(timeout=5)
                return True
            except NotFound:
                return False

        try:
            result = await loop.run_in_executor(None, _stop)
            if result:
                self.logger.info("Stopped container for task %s", task_id)
            return result
        except Exception as exc:
            self.logger.warning("Cancel failed for task %s: %s", task_id, exc)
            return False

    async def cleanup(self, task_id: uuid.UUID) -> None:
        """Remove the Docker container and tracking entries.

        Args:
            task_id: The UUID returned by dispatch().
        """
        container_id = self._tasks.pop(task_id, None)
        self._configs.pop(task_id, None)
        self._start_times.pop(task_id, None)

        if container_id is None:
            return

        loop = asyncio.get_running_loop()

        def _remove():
            try:
                container = self._client.containers.get(container_id)
                container.remove(force=True)
            except NotFound:
                pass  # Already removed

        try:
            await loop.run_in_executor(None, _remove)
            self.logger.debug("Removed container %s for task %s", container_id[:12], task_id)
        except Exception as exc:
            self.logger.warning(
                "Cleanup failed for container %s (task %s): %s",
                container_id[:12], task_id, exc,
            )

    async def health_check(self) -> dict:
        """Check Docker daemon connectivity.

        Returns:
            Dict with status and active container count.
        """
        loop = asyncio.get_running_loop()

        def _ping():
            try:
                self._client.ping()
                return True
            except Exception:
                return False

        try:
            reachable = await loop.run_in_executor(None, _ping)
        except Exception:
            reachable = False

        return {
            "status": "connected" if reachable else "disconnected",
            "active_containers": len(self._tasks),
        }


def _cpu_to_quota(cpu_str: str) -> int:
    """Convert a CPU limit string to Docker cpu_quota microseconds.

    Args:
        cpu_str: e.g. '500m' (500 millicpus) or '2' (2 full CPUs).

    Returns:
        cpu_quota value for Docker API (100000 = 1 CPU per 100ms period).
    """
    try:
        if cpu_str.endswith("m"):
            millicpus = int(cpu_str[:-1])
            return int(millicpus * 100)  # 1000m = 100000 (1 CPU)
        else:
            cpus = float(cpu_str)
            return int(cpus * 100000)
    except (ValueError, AttributeError):
        return 100000  # Default: 1 CPU
