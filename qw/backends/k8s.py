"""Kubernetes execution backend for QWorker.

Executes tasks as ephemeral Kubernetes pods. Supports in-cluster service account
auth and kubeconfig-based auth for local/remote clusters.

Task data is passed to the pod via the TASK_DATA environment variable
(base64-encoded cloudpickle). The TASK_FORMAT variable is always set to
'cloudpickle'. Pods use restartPolicy: Never — QWorker manages retries.

The kubernetes package is an optional dependency:
    uv pip install qworker[k8s]
"""
import asyncio
import base64
import logging
import time
import uuid
from typing import Any, Optional

import cloudpickle

try:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    from kubernetes.client.exceptions import ApiException
    HAS_K8S = True
except ImportError:
    HAS_K8S = False

from .base import BaseExecutionBackend
from .models import ContainerConfig, ContainerResources, TaskResult


_POD_PHASE_MAP = {
    "Pending": "pending",
    "Running": "running",
    "Succeeded": "completed",
    "Failed": "failed",
    "Unknown": "failed",
}


class K8sBackend(BaseExecutionBackend):
    """Executes tasks as ephemeral Kubernetes pods.

    Task data is passed via the TASK_DATA environment variable
    (base64-encoded cloudpickle). Pods use restartPolicy: Never — QWorker
    manages retries.

    Args:
        namespace: Default Kubernetes namespace to create pods in.
                   Never creates the namespace. Individual tasks may override
                   via ContainerConfig.namespace.
        kubeconfig: Path to kubeconfig file. None triggers auto-detect
                    (in-cluster → $KUBECONFIG → ~/.kube/config).
    """

    def __init__(
        self,
        namespace: str = "default",
        kubeconfig: Optional[str] = None,
    ) -> None:
        if not HAS_K8S:
            raise ImportError(
                "kubernetes package is required for K8sBackend. "
                "Install it with: uv pip install 'qworker[k8s]'"
            )
        self.logger = logging.getLogger("QW.Backend.K8s")
        self._namespace = namespace
        # Auto-detect: try in-cluster first, then kubeconfig
        try:
            k8s_config.load_incluster_config()
            self.logger.debug("Using in-cluster K8s config")
        except Exception:
            k8s_config.load_kube_config(config_file=kubeconfig)
            self.logger.debug("Using kubeconfig for K8s")
        self._core_v1 = k8s_client.CoreV1Api()
        # Maps task_id -> (pod_name, namespace) so all API calls use the
        # correct namespace even when the task overrides the default.
        self._tasks: dict[uuid.UUID, tuple[str, str]] = {}
        # Maps task_id -> start time
        self._start_times: dict[uuid.UUID, float] = {}

    def _make_pod_name(self, task_id: uuid.UUID) -> str:
        """Generate a DNS-compatible pod name from task_id.

        Args:
            task_id: Task UUID.

        Returns:
            String like 'qw-task-<short-uuid>'.
        """
        short = str(task_id).replace("-", "")[:16]
        return f"qw-task-{short}"

    def _serialize_task(self, task: Any) -> str:
        """Serialize task to base64-encoded cloudpickle string.

        Args:
            task: Task object to serialize.

        Returns:
            Base64-encoded string.
        """
        return base64.b64encode(cloudpickle.dumps(task)).decode("utf-8")

    def _build_resource_requirements(
        self, resources: Optional[ContainerResources]
    ) -> Optional[Any]:
        """Build k8s ResourceRequirements from ContainerResources.

        Args:
            resources: Optional ContainerResources with limits/requests.

        Returns:
            k8s V1ResourceRequirements or None.
        """
        if resources is None:
            return None
        limits = {}
        requests = {}
        if resources.cpu_limit:
            limits["cpu"] = resources.cpu_limit
        if resources.memory_limit:
            limits["memory"] = resources.memory_limit
        if resources.cpu_request:
            requests["cpu"] = resources.cpu_request
        if resources.memory_request:
            requests["memory"] = resources.memory_request
        return k8s_client.V1ResourceRequirements(
            limits=limits or None,
            requests=requests or None,
        )

    def _build_pod_spec(
        self,
        task: Any,
        config: ContainerConfig,
        pod_name: str,
        namespace: str,
    ) -> Any:
        """Build a V1Pod spec for the task.

        Task data is passed via the TASK_DATA environment variable
        (base64-encoded cloudpickle).

        Args:
            task: Task to serialize.
            config: ContainerConfig with image/env/resources.
            pod_name: DNS-compatible pod name.
            namespace: Kubernetes namespace for the pod metadata.

        Returns:
            k8s V1Pod object.

        Raises:
            ValueError: If the serialized task payload exceeds 900KB (approaching
                        the 1MB environment variable limit).
        """
        task_id = str(task.id) if hasattr(task, "id") else str(uuid.uuid4())
        serialized = self._serialize_task(task)

        if len(serialized) > 900_000:
            raise ValueError(
                "Task payload too large for container backend (>900KB). "
                "Consider chunking or using a shared storage backend."
            )

        # Environment variables: user-defined + task data
        env_vars = [
            k8s_client.V1EnvVar(name=k, value=v)
            for k, v in config.env.items()
        ]
        env_vars.append(
            k8s_client.V1EnvVar(name="TASK_DATA", value=serialized)
        )
        env_vars.append(
            k8s_client.V1EnvVar(name="TASK_FORMAT", value="cloudpickle")
        )

        resource_req = self._build_resource_requirements(config.resources)

        container = k8s_client.V1Container(
            name="task",
            image=config.image,
            env=env_vars,
            resources=resource_req,
        )

        pod_spec = k8s_client.V1PodSpec(
            containers=[container],
            restart_policy="Never",
        )

        labels = {
            "app": "qworker",
            "task-id": task_id[:63],  # K8s label values max 63 chars
        }

        return k8s_client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=k8s_client.V1ObjectMeta(
                name=pod_name,
                namespace=namespace,
                labels=labels,
            ),
            spec=pod_spec,
        )

    async def dispatch(self, task: Any) -> uuid.UUID:
        """Create an ephemeral pod for the task.

        Args:
            task: A QueueWrapper instance with container_config.backend='k8s'.

        Returns:
            The task's UUID.

        Raises:
            ApiException: If K8s API returns an error (e.g., 403 RBAC).
        """
        config: ContainerConfig = task.container_config
        namespace = config.namespace or self._namespace
        task_id: uuid.UUID = task.id if hasattr(task, "id") else uuid.uuid4()

        pod_name = self._make_pod_name(task_id)

        self._tasks[task_id] = (pod_name, namespace)
        self._start_times[task_id] = time.monotonic()

        pod_manifest = self._build_pod_spec(task, config, pod_name, namespace)

        loop = asyncio.get_running_loop()

        def _create_pod():
            self._core_v1.create_namespaced_pod(
                namespace=namespace,
                body=pod_manifest,
            )

        try:
            await loop.run_in_executor(None, _create_pod)
            self.logger.info(
                "Dispatched task %s as pod %s in namespace %s",
                task_id, pod_name, namespace,
            )
        except ApiException as exc:
            self.logger.error(
                "K8s API error creating pod for task %s: %s", task_id, exc
            )
            self._tasks.pop(task_id, None)
            self._start_times.pop(task_id, None)
            raise

        return task_id

    async def poll(self, task_id: uuid.UUID) -> str:
        """Check pod status.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            One of: 'pending', 'running', 'completed', 'failed'.
        """
        entry = self._tasks.get(task_id)
        if entry is None:
            return "pending"

        pod_name, namespace = entry

        loop = asyncio.get_running_loop()

        def _get_phase():
            try:
                pod = self._core_v1.read_namespaced_pod(
                    name=pod_name, namespace=namespace
                )
                return pod.status.phase if pod.status else None
            except ApiException as exc:
                if exc.status == 404:
                    return "Succeeded"  # Pod was already cleaned up
                self.logger.warning("Poll error for %s: %s", task_id, exc)
                return None

        try:
            phase = await loop.run_in_executor(None, _get_phase)
        except Exception as exc:
            self.logger.warning("Poll failed for task %s: %s", task_id, exc)
            return "failed"

        if phase is None:
            return "running"
        return _POD_PHASE_MAP.get(phase, "pending")

    async def get_result(self, task_id: uuid.UUID) -> TaskResult:
        """Read pod logs and return TaskResult.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            TaskResult with pod log output.

        Raises:
            KeyError: If task_id is unknown.
        """
        entry = self._tasks.get(task_id)
        if entry is None:
            raise KeyError(f"Unknown task_id: {task_id}")

        pod_name, namespace = entry
        start_time = self._start_times.get(task_id, time.monotonic())
        loop = asyncio.get_running_loop()

        def _get_logs_and_phase():
            try:
                pod = self._core_v1.read_namespaced_pod(
                    name=pod_name, namespace=namespace
                )
                phase = pod.status.phase if pod.status else "Unknown"
                logs = self._core_v1.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                )
                return phase, logs
            except ApiException as exc:
                return "Failed", str(exc)

        try:
            phase, logs = await loop.run_in_executor(None, _get_logs_and_phase)
        except Exception as exc:
            return TaskResult(
                task_id=task_id,
                success=False,
                result=None,
                error=str(exc),
                execution_time=round(time.monotonic() - start_time, 4),
                backend="k8s",
            )

        success = phase == "Succeeded"
        error_str = None if success else f"Pod phase: {phase}. Logs: {str(logs)[:500]}"

        return TaskResult(
            task_id=task_id,
            success=success,
            result=logs if success else None,
            error=error_str,
            execution_time=round(time.monotonic() - start_time, 4),
            backend="k8s",
        )

    async def cancel(self, task_id: uuid.UUID) -> bool:
        """Delete the pod.

        Args:
            task_id: The UUID returned by dispatch().

        Returns:
            True if deleted, False if not found.
        """
        entry = self._tasks.get(task_id)
        if entry is None:
            return False

        pod_name, namespace = entry
        loop = asyncio.get_running_loop()

        def _delete():
            try:
                self._core_v1.delete_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                )
                return True
            except ApiException as exc:
                if exc.status == 404:
                    return False
                raise

        try:
            result = await loop.run_in_executor(None, _delete)
            if result:
                self.logger.info("Deleted pod %s for task %s", pod_name, task_id)
            return result
        except Exception as exc:
            self.logger.warning("Cancel failed for task %s: %s", task_id, exc)
            return False

    async def cleanup(self, task_id: uuid.UUID) -> None:
        """Delete pod and tracking entries.

        Args:
            task_id: The UUID returned by dispatch().
        """
        entry = self._tasks.pop(task_id, None)
        self._start_times.pop(task_id, None)

        loop = asyncio.get_running_loop()

        def _delete_resources():
            if entry:
                pod_name, namespace = entry
                try:
                    self._core_v1.delete_namespaced_pod(
                        name=pod_name, namespace=namespace
                    )
                except ApiException as exc:
                    if exc.status != 404:
                        self.logger.warning(
                            "Pod cleanup error for %s: %s", pod_name, exc
                        )

        pod_label = entry[0] if entry else "unknown"
        try:
            await loop.run_in_executor(None, _delete_resources)
            self.logger.debug("Cleaned up pod %s for task %s", pod_label, task_id)
        except Exception as exc:
            self.logger.warning("Cleanup error for task %s: %s", task_id, exc)

    async def health_check(self) -> dict:
        """Check K8s API connectivity.

        Returns:
            Dict with status and active pod count.
        """
        loop = asyncio.get_running_loop()

        def _check_api():
            try:
                v = k8s_client.VersionApi()
                v.get_code()
                return True
            except Exception:
                return False

        try:
            reachable = await loop.run_in_executor(None, _check_api)
        except Exception:
            reachable = False

        return {
            "status": "connected" if reachable else "disconnected",
            "active_pods": len(self._tasks),
            "namespace": self._namespace,
        }
