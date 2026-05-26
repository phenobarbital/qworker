"""Unit tests for K8sBackend — TASK-036.

Uses unittest.mock to avoid requiring a real Kubernetes cluster.
"""
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

from qw.backends.models import ContainerConfig
from qw.wrappers.base import QueueWrapper


pytestmark = pytest.mark.asyncio


def _build_k8s_mock():
    """Build a mock kubernetes module with minimal attributes."""
    mock_k8s_mod = types.ModuleType("kubernetes")

    # Mock client submodule
    mock_client_mod = types.ModuleType("kubernetes.client")
    mock_client_mod.CoreV1Api = MagicMock()
    mock_client_mod.VersionApi = MagicMock()
    mock_client_mod.V1Pod = MagicMock()
    mock_client_mod.V1Container = MagicMock()
    mock_client_mod.V1PodSpec = MagicMock()
    mock_client_mod.V1ObjectMeta = MagicMock()
    mock_client_mod.V1EnvVar = MagicMock()
    mock_client_mod.V1ResourceRequirements = MagicMock()

    # Mock config submodule
    mock_config_mod = types.ModuleType("kubernetes.config")
    mock_config_mod.load_incluster_config = MagicMock(
        side_effect=Exception("not in cluster")
    )
    mock_config_mod.load_kube_config = MagicMock()

    # Mock exceptions
    ApiException = type("ApiException", (Exception,), {"status": 0})
    mock_exceptions_mod = types.ModuleType("kubernetes.client.exceptions")
    mock_exceptions_mod.ApiException = ApiException

    mock_k8s_mod.client = mock_client_mod
    mock_k8s_mod.config = mock_config_mod

    return mock_k8s_mod, mock_client_mod, mock_config_mod, ApiException


class TestK8sBackendImportError:
    """Test graceful failure when kubernetes SDK is not installed."""

    def test_import_error_without_sdk(self):
        """K8sBackend raises ImportError when kubernetes SDK not installed."""
        import qw.backends.k8s as k8s_module
        original = k8s_module.HAS_K8S
        try:
            k8s_module.HAS_K8S = False
            from qw.backends.k8s import K8sBackend
            with pytest.raises(ImportError, match="kubernetes"):
                K8sBackend()
        finally:
            k8s_module.HAS_K8S = original


class TestK8sBackend:
    """Tests for K8sBackend with mocked Kubernetes client."""

    @pytest.fixture
    def mock_setup(self):
        """Inject mock kubernetes modules and patch HAS_K8S."""
        mock_k8s_mod, mock_client_mod, mock_config_mod, ApiException = _build_k8s_mock()
        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s_mod,
                "kubernetes.client": mock_client_mod,
                "kubernetes.config": mock_config_mod,
                "kubernetes.client.exceptions": types.ModuleType(
                    "kubernetes.client.exceptions"
                ),
            },
        ):
            import qw.backends.k8s as k8s_module
            old_has = k8s_module.HAS_K8S
            old_k8s_client = getattr(k8s_module, "k8s_client", None)
            old_k8s_config = getattr(k8s_module, "k8s_config", None)
            old_apiexc = getattr(k8s_module, "ApiException", None)
            k8s_module.HAS_K8S = True
            k8s_module.k8s_client = mock_client_mod
            k8s_module.k8s_config = mock_config_mod
            k8s_module.ApiException = ApiException
            try:
                yield mock_client_mod, mock_config_mod, ApiException
            finally:
                k8s_module.HAS_K8S = old_has
                if old_k8s_client is not None:
                    k8s_module.k8s_client = old_k8s_client
                if old_k8s_config is not None:
                    k8s_module.k8s_config = old_k8s_config
                if old_apiexc is not None:
                    k8s_module.ApiException = old_apiexc

    @pytest.fixture
    def backend(self, mock_setup):
        """K8sBackend instance with mocked client."""
        mock_client_mod, mock_config_mod, ApiException = mock_setup
        mock_core_v1 = MagicMock()
        mock_client_mod.CoreV1Api.return_value = mock_core_v1
        from qw.backends.k8s import K8sBackend
        b = K8sBackend(namespace="test-ns")
        b._core_v1 = mock_core_v1
        return b, mock_core_v1

    def _make_task(self, backend_type="k8s"):
        async def fn():
            return "ok"

        cfg = ContainerConfig(
            backend=backend_type, image="worker:latest", namespace="test-ns"
        )
        task = QueueWrapper(coro=fn, container_config=cfg)
        return task

    async def test_dispatch_creates_pod(self, backend, mock_setup):
        """dispatch() calls create_namespaced_pod with correct args."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        task = self._make_task()
        task_id = await b.dispatch(task)

        assert isinstance(task_id, uuid.UUID)
        mock_core_v1.create_namespaced_pod.assert_called_once()

    async def test_dispatch_stores_pod_name(self, backend, mock_setup):
        """dispatch() stores pod name in _tasks dict."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        task = self._make_task()
        task_id = await b.dispatch(task)

        assert task_id in b._tasks
        assert b._tasks[task_id].startswith("qw-task-")

    async def test_poll_pending(self, backend, mock_setup):
        """poll() returns 'pending' for a pod in Pending phase."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        mock_pod = MagicMock()
        mock_pod.status.phase = "Pending"
        mock_core_v1.read_namespaced_pod = MagicMock(return_value=mock_pod)

        task = self._make_task()
        task_id = await b.dispatch(task)
        status = await b.poll(task_id)
        assert status == "pending"

    async def test_poll_succeeded(self, backend, mock_setup):
        """poll() returns 'completed' for a Succeeded pod."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        mock_pod = MagicMock()
        mock_pod.status.phase = "Succeeded"
        mock_core_v1.read_namespaced_pod = MagicMock(return_value=mock_pod)

        task = self._make_task()
        task_id = await b.dispatch(task)
        status = await b.poll(task_id)
        assert status == "completed"

    async def test_poll_failed(self, backend, mock_setup):
        """poll() returns 'failed' for a Failed pod."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        mock_pod = MagicMock()
        mock_pod.status.phase = "Failed"
        mock_core_v1.read_namespaced_pod = MagicMock(return_value=mock_pod)

        task = self._make_task()
        task_id = await b.dispatch(task)
        status = await b.poll(task_id)
        assert status == "failed"

    async def test_poll_unknown_returns_pending(self, backend, mock_setup):
        """poll() returns 'pending' for unknown task_id."""
        b, _ = backend
        status = await b.poll(uuid.uuid4())
        assert status == "pending"

    async def test_uses_configured_namespace(self, backend, mock_setup):
        """K8sBackend uses the configured namespace, never creates one."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        assert b._namespace == "test-ns"
        task = self._make_task()
        await b.dispatch(task)

        call_kwargs = mock_core_v1.create_namespaced_pod.call_args
        assert call_kwargs[1].get("namespace") == "test-ns" or \
               call_kwargs[0][0] == "test-ns"

    async def test_cancel_deletes_pod(self, backend, mock_setup):
        """cancel() calls delete_namespaced_pod."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()
        mock_core_v1.delete_namespaced_pod = MagicMock()

        task = self._make_task()
        task_id = await b.dispatch(task)
        cancelled = await b.cancel(task_id)

        assert cancelled is True
        mock_core_v1.delete_namespaced_pod.assert_called_once()

    async def test_cancel_unknown_returns_false(self, backend, mock_setup):
        """cancel() returns False for unknown task_id."""
        b, _ = backend
        result = await b.cancel(uuid.uuid4())
        assert result is False

    async def test_cleanup_removes_pod(self, backend, mock_setup):
        """cleanup() deletes the pod and removes tracking entries."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()
        mock_core_v1.delete_namespaced_pod = MagicMock()

        task = self._make_task()
        task_id = await b.dispatch(task)
        await b.cleanup(task_id)

        assert task_id not in b._tasks
        mock_core_v1.delete_namespaced_pod.assert_called()

    async def test_get_result_success(self, backend, mock_setup):
        """get_result() returns successful TaskResult for Succeeded pod."""
        b, mock_core_v1 = backend
        mock_core_v1.create_namespaced_pod = MagicMock()

        mock_pod = MagicMock()
        mock_pod.status.phase = "Succeeded"
        mock_core_v1.read_namespaced_pod = MagicMock(return_value=mock_pod)
        mock_core_v1.read_namespaced_pod_log = MagicMock(
            return_value='{"result": "ok"}'
        )

        task = self._make_task()
        task_id = await b.dispatch(task)
        result = await b.get_result(task_id)

        assert result.success is True
        assert result.backend == "k8s"

    async def test_get_result_unknown_raises(self, backend, mock_setup):
        """get_result() raises KeyError for unknown task_id."""
        b, _ = backend
        with pytest.raises(KeyError):
            await b.get_result(uuid.uuid4())

    async def test_health_check_connected(self, backend, mock_setup):
        """health_check() returns connected when API is reachable."""
        b, mock_core_v1 = backend
        mock_setup[0].VersionApi.return_value.get_code = MagicMock(return_value=True)
        health = await b.health_check()
        assert "status" in health
        assert "namespace" in health
