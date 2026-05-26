"""Unit tests for BackendDispatcher — TASK-039."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qw.backends.dispatch import BackendDispatcher
from qw.backends.local import LocalBackend
from qw.backends.models import ContainerConfig, TaskResult
from qw.wrappers.base import QueueWrapper


pytestmark = pytest.mark.asyncio


def _make_task(backend: str = None, image: str = "worker:latest"):
    """Create a QueueWrapper with optional container_config."""
    async def fn():
        return "ok"

    if backend is not None:
        cfg = ContainerConfig(backend=backend, image=image)
        return QueueWrapper(coro=fn, container_config=cfg)
    return QueueWrapper(coro=fn)


def _make_dispatcher(overflow=False):
    """Build a BackendDispatcher with mock backends."""
    local = MagicMock(spec=LocalBackend)
    docker = MagicMock()
    k8s = MagicMock()
    monitor = MagicMock()
    monitor.should_overflow.return_value = overflow
    return BackendDispatcher(
        local_backend=local,
        docker_backend=docker,
        k8s_backend=k8s,
        resource_monitor=monitor,
    ), local, docker, k8s, monitor


class TestResolveBackend:
    """Tests for BackendDispatcher.resolve_backend()."""

    def test_routes_docker_task(self):
        """Task with container_config.backend='docker' routes to DockerBackend."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher()
        task = _make_task(backend="docker")
        assert dispatcher.resolve_backend(task) is docker

    def test_routes_k8s_task(self):
        """Task with container_config.backend='k8s' routes to K8sBackend."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher()
        task = _make_task(backend="k8s")
        assert dispatcher.resolve_backend(task) is k8s

    def test_routes_local_by_default(self):
        """Task without container_config routes to LocalBackend."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher()
        task = _make_task()
        assert dispatcher.resolve_backend(task) is local

    def test_overflow_routes_to_k8s(self):
        """Under resource overflow, tasks route to K8s backend."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher(overflow=True)
        task = _make_task()  # No explicit container_config
        assert dispatcher.resolve_backend(task) is k8s

    def test_no_overflow_routes_to_local(self):
        """Without overflow, plain tasks go to local."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher(overflow=False)
        task = _make_task()
        assert dispatcher.resolve_backend(task) is local

    def test_docker_config_overrides_overflow(self):
        """Explicit container_config takes priority over overflow."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher(overflow=True)
        task = _make_task(backend="docker")
        # Should still use docker, not k8s from overflow
        assert dispatcher.resolve_backend(task) is docker

    def test_docker_missing_raises(self):
        """RuntimeError if task needs docker but docker backend not configured."""
        local = MagicMock(spec=LocalBackend)
        dispatcher = BackendDispatcher(
            local_backend=local,
            docker_backend=None,
            k8s_backend=None,
        )
        task = _make_task(backend="docker")
        with pytest.raises(RuntimeError, match="DockerBackend is not configured"):
            dispatcher.resolve_backend(task)

    def test_k8s_missing_raises(self):
        """RuntimeError if task needs k8s but k8s backend not configured."""
        local = MagicMock(spec=LocalBackend)
        dispatcher = BackendDispatcher(
            local_backend=local,
            docker_backend=None,
            k8s_backend=None,
        )
        task = _make_task(backend="k8s")
        with pytest.raises(RuntimeError, match="K8sBackend is not configured"):
            dispatcher.resolve_backend(task)

    def test_no_monitor_no_overflow(self):
        """Without a resource monitor, no overflow routing occurs."""
        local = MagicMock(spec=LocalBackend)
        k8s = MagicMock()
        dispatcher = BackendDispatcher(
            local_backend=local,
            k8s_backend=k8s,
        )
        task = _make_task()
        assert dispatcher.resolve_backend(task) is local


class TestTaskMappings:
    """Tests for task pattern matching in BackendDispatcher."""

    def test_task_mapping_routes_to_docker(self):
        """Task name matching a docker mapping goes to DockerBackend."""
        from qw.backends.models import ContainerTaskMapping
        local = MagicMock(spec=LocalBackend)
        docker = MagicMock()
        mappings = [
            ContainerTaskMapping(
                task_pattern="report_*",
                config=ContainerConfig(backend="docker", image="report-worker:latest"),
            )
        ]
        dispatcher = BackendDispatcher(
            local_backend=local,
            docker_backend=docker,
            task_mappings=mappings,
        )

        async def report_monthly():
            pass

        task = QueueWrapper(coro=report_monthly)
        assert dispatcher.resolve_backend(task) is docker

    def test_task_mapping_routes_to_k8s(self):
        """Task name matching a k8s mapping goes to K8sBackend."""
        from qw.backends.models import ContainerTaskMapping
        local = MagicMock(spec=LocalBackend)
        k8s = MagicMock()
        mappings = [
            ContainerTaskMapping(
                task_pattern="ml_*",
                config=ContainerConfig(backend="k8s", image="ml-worker:latest"),
            )
        ]
        dispatcher = BackendDispatcher(
            local_backend=local,
            k8s_backend=k8s,
            task_mappings=mappings,
        )

        async def ml_train():
            pass

        task = QueueWrapper(coro=ml_train)
        assert dispatcher.resolve_backend(task) is k8s

    def test_unmatched_task_falls_through_to_local(self):
        """Task not matching any mapping goes to LocalBackend."""
        from qw.backends.models import ContainerTaskMapping
        local = MagicMock(spec=LocalBackend)
        docker = MagicMock()
        mappings = [
            ContainerTaskMapping(
                task_pattern="report_*",
                config=ContainerConfig(backend="docker", image="report-worker:latest"),
            )
        ]
        dispatcher = BackendDispatcher(
            local_backend=local,
            docker_backend=docker,
            task_mappings=mappings,
        )

        async def compute():
            pass

        task = QueueWrapper(coro=compute)
        assert dispatcher.resolve_backend(task) is local


class TestShouldUseContainer:
    """Tests for BackendDispatcher.should_use_container()."""

    def test_returns_false_by_default(self):
        """Returns False when no mappings and no overflow."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher(overflow=False)
        task = _make_task()
        assert dispatcher.should_use_container(task) is False

    def test_returns_true_when_overflow(self):
        """Returns True when overflow is active."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher(overflow=True)
        task = _make_task()
        assert dispatcher.should_use_container(task) is True

    def test_returns_true_for_mapped_task(self):
        """Returns True when task matches a mapping."""
        from qw.backends.models import ContainerTaskMapping
        local = MagicMock(spec=LocalBackend)
        docker = MagicMock()
        mappings = [
            ContainerTaskMapping(
                task_pattern="report_*",
                config=ContainerConfig(backend="docker", image="report-worker:latest"),
            )
        ]
        dispatcher = BackendDispatcher(
            local_backend=local,
            docker_backend=docker,
            task_mappings=mappings,
        )

        async def report_daily():
            pass

        task = QueueWrapper(coro=report_daily)
        assert dispatcher.should_use_container(task) is True


class TestDispatchAndTrack:
    """Tests for BackendDispatcher.dispatch_and_track()."""

    @pytest.fixture
    def mock_backend(self):
        """Mock backend with async dispatch/poll/get_result/cleanup."""
        backend = MagicMock()
        task_id = uuid.uuid4()
        backend.dispatch = AsyncMock(return_value=task_id)
        backend.poll = AsyncMock(return_value="completed")
        backend.get_result = AsyncMock(
            return_value=TaskResult(
                task_id=task_id,
                success=True,
                result="done",
                error=None,
                execution_time=0.1,
                backend="local",
            )
        )
        backend.cleanup = AsyncMock()
        return backend, task_id

    async def test_dispatch_and_track_success(self, mock_backend):
        """dispatch_and_track() returns TaskResult on success."""
        backend, task_id = mock_backend
        dispatcher, local, docker, k8s, monitor = _make_dispatcher()
        # Route to local
        with patch.object(dispatcher, "resolve_backend", return_value=backend):
            task = _make_task()
            result = await dispatcher.dispatch_and_track(task)

        assert result.success is True

    async def test_fire_and_forget_returns_immediately(self, mock_backend):
        """dispatch_and_track() returns immediately for fire_and_forget tasks."""
        backend, task_id = mock_backend
        dispatcher, local, docker, k8s, monitor = _make_dispatcher()

        async def fn():
            return "ok"

        cfg = ContainerConfig(
            backend="docker", image="worker:latest", fire_and_forget=True
        )
        task = QueueWrapper(coro=fn, container_config=cfg)

        with patch.object(dispatcher, "resolve_backend", return_value=backend):
            result = await dispatcher.dispatch_and_track(task)

        assert result.success is True
        backend.poll.assert_not_called()

    async def test_dispatch_failure_returns_error_result(self):
        """dispatch_and_track() returns failure TaskResult when dispatch fails."""
        dispatcher, local, docker, k8s, monitor = _make_dispatcher()
        bad_backend = MagicMock()
        bad_backend.dispatch = AsyncMock(side_effect=RuntimeError("failed"))

        task = _make_task()
        with patch.object(dispatcher, "resolve_backend", return_value=bad_backend):
            with patch("qw.backends.dispatch.WORKER_RETRY_COUNT", 1):
                result = await dispatcher.dispatch_and_track(task)

        assert result.success is False
        assert result.error is not None
