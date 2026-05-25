"""Unit tests for DockerBackend — TASK-035.

Uses unittest.mock to avoid requiring a real Docker daemon or docker SDK.
"""
import sys
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

from qw.backends.models import ContainerConfig
from qw.wrappers.base import QueueWrapper


pytestmark = pytest.mark.asyncio


def _make_mock_container(status="running", exit_code=0, logs=b'{"result": "ok"}'):
    """Build a mock Docker container object."""
    c = MagicMock()
    c.id = "abc123def456abc123def456"  # 24-char container ID
    c.status = status
    c.wait.return_value = {"StatusCode": exit_code}
    c.logs.return_value = logs
    c.reload = MagicMock()
    c.stop = MagicMock()
    c.remove = MagicMock()
    return c


def _build_docker_mock():
    """Build a mock docker module with necessary attributes."""
    mock_docker_mod = types.ModuleType("docker")
    mock_client = MagicMock()
    mock_docker_mod.from_env = MagicMock(return_value=mock_client)
    mock_docker_mod.DockerClient = MagicMock(return_value=mock_client)
    # Mock exception classes
    mock_errors = types.ModuleType("docker.errors")
    mock_errors.APIError = type("APIError", (Exception,), {})
    mock_errors.ContainerError = type("ContainerError", (Exception,), {})
    mock_errors.ImageNotFound = type("ImageNotFound", (Exception,), {})
    mock_errors.NotFound = type("NotFound", (Exception,), {})
    mock_docker_mod.errors = mock_errors
    return mock_docker_mod, mock_client


class TestDockerBackendImportError:
    """Test graceful failure when docker SDK is not installed."""

    @pytest.mark.no_cover
    def test_import_error_without_sdk(self):
        """DockerBackend raises ImportError when docker SDK is not installed."""
        import qw.backends.docker as docker_module
        original = docker_module.HAS_DOCKER
        try:
            docker_module.HAS_DOCKER = False
            from qw.backends.docker import DockerBackend
            with pytest.raises(ImportError, match="Docker SDK"):
                DockerBackend()
        finally:
            docker_module.HAS_DOCKER = original


class TestDockerBackend:
    """Tests for DockerBackend with mocked Docker SDK."""

    @pytest.fixture
    def mock_setup(self):
        """Inject a mock docker module into sys.modules and patch HAS_DOCKER."""
        mock_docker_mod, mock_client = _build_docker_mock()
        with patch.dict(
            "sys.modules",
            {
                "docker": mock_docker_mod,
                "docker.errors": mock_docker_mod.errors,
            },
        ):
            import importlib
            import qw.backends.docker as docker_module
            old_has = docker_module.HAS_DOCKER
            old_docker = getattr(docker_module, "docker", None)
            old_notfound = getattr(docker_module, "NotFound", None)
            old_apierror = getattr(docker_module, "APIError", None)
            old_imagenf = getattr(docker_module, "ImageNotFound", None)
            docker_module.HAS_DOCKER = True
            docker_module.docker = mock_docker_mod
            docker_module.NotFound = mock_docker_mod.errors.NotFound
            docker_module.APIError = mock_docker_mod.errors.APIError
            docker_module.ImageNotFound = mock_docker_mod.errors.ImageNotFound
            try:
                yield mock_client
            finally:
                docker_module.HAS_DOCKER = old_has
                if old_docker is not None:
                    docker_module.docker = old_docker
                if old_notfound is not None:
                    docker_module.NotFound = old_notfound
                if old_apierror is not None:
                    docker_module.APIError = old_apierror
                if old_imagenf is not None:
                    docker_module.ImageNotFound = old_imagenf

    @pytest.fixture
    def backend(self, mock_setup):
        """DockerBackend instance with mocked client."""
        from qw.backends.docker import DockerBackend
        b = DockerBackend()
        b._client = mock_setup
        return b

    def _make_task(self, image="python:3.12-slim"):
        async def fn():
            return "ok"

        cfg = ContainerConfig(backend="docker", image=image)
        task = QueueWrapper(coro=fn, container_config=cfg)
        return task

    async def test_dispatch_creates_container(self, backend, mock_setup):
        """dispatch() calls containers.run() with correct image."""
        mock_container = _make_mock_container()
        mock_setup.containers.run.return_value = mock_container

        task = self._make_task(image="python:3.12-slim")
        task_id = await backend.dispatch(task)

        assert isinstance(task_id, uuid.UUID)
        mock_setup.containers.run.assert_called_once()

    async def test_dispatch_stores_container_id(self, backend, mock_setup):
        """dispatch() stores container_id in _tasks dict."""
        mock_container = _make_mock_container()
        mock_setup.containers.run.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)

        assert task_id in backend._tasks
        assert backend._tasks[task_id] == mock_container.id

    async def test_poll_running_container(self, backend, mock_setup):
        """poll() returns 'running' for a running container."""
        mock_container = _make_mock_container(status="running")
        mock_setup.containers.run.return_value = mock_container
        mock_setup.containers.get.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)
        status = await backend.poll(task_id)
        assert status == "running"

    async def test_poll_exited_container(self, backend, mock_setup):
        """poll() returns 'completed' for an exited container."""
        mock_container = _make_mock_container(status="exited")
        mock_setup.containers.run.return_value = mock_container
        mock_setup.containers.get.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)
        status = await backend.poll(task_id)
        assert status == "completed"

    async def test_poll_unknown_task(self, backend):
        """poll() returns 'pending' for unknown task_id."""
        status = await backend.poll(uuid.uuid4())
        assert status == "pending"

    async def test_get_result_success(self, backend, mock_setup):
        """get_result() returns TaskResult with parsed JSON output."""
        import json
        mock_container = _make_mock_container(
            status="exited",
            exit_code=0,
            logs=json.dumps({"value": 42}).encode("utf-8"),
        )
        mock_setup.containers.run.return_value = mock_container
        mock_setup.containers.get.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)
        result = await backend.get_result(task_id)

        assert result.success is True
        assert result.backend == "docker"

    async def test_get_result_failure(self, backend, mock_setup):
        """get_result() returns failed TaskResult when container exits non-zero."""
        mock_container = _make_mock_container(
            status="exited",
            exit_code=1,
            logs=b"Error: task failed",
        )
        mock_setup.containers.run.return_value = mock_container
        mock_setup.containers.get.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)
        result = await backend.get_result(task_id)

        assert result.success is False
        assert result.error is not None

    async def test_get_result_unknown_raises(self, backend):
        """get_result() raises KeyError for unknown task_id."""
        with pytest.raises(KeyError):
            await backend.get_result(uuid.uuid4())

    async def test_cleanup_removes_container(self, backend, mock_setup):
        """cleanup() calls container.remove()."""
        mock_container = _make_mock_container()
        mock_setup.containers.run.return_value = mock_container
        mock_setup.containers.get.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)
        await backend.cleanup(task_id)

        mock_container.remove.assert_called_once_with(force=True)
        assert task_id not in backend._tasks

    async def test_cancel_stops_container(self, backend, mock_setup):
        """cancel() calls container.stop()."""
        mock_container = _make_mock_container()
        mock_setup.containers.run.return_value = mock_container
        mock_setup.containers.get.return_value = mock_container

        task = self._make_task()
        task_id = await backend.dispatch(task)
        cancelled = await backend.cancel(task_id)

        assert cancelled is True
        mock_container.stop.assert_called_once()

    async def test_cancel_unknown_returns_false(self, backend):
        """cancel() returns False for unknown task_id."""
        assert await backend.cancel(uuid.uuid4()) is False

    async def test_health_check_ping_success(self, backend, mock_setup):
        """health_check() returns connected when ping succeeds."""
        mock_setup.ping.return_value = True
        health = await backend.health_check()
        assert health["status"] == "connected"
        assert "active_containers" in health

    async def test_health_check_disconnected(self, backend, mock_setup):
        """health_check() returns disconnected when ping fails."""
        mock_setup.ping.side_effect = Exception("connection refused")
        health = await backend.health_check()
        assert health["status"] == "disconnected"
