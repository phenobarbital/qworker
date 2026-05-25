# TASK-036: Kubernetes Backend

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-033
**Assigned-to**: unassigned

---

## Context

Implements `K8sBackend` — executes tasks as ephemeral Kubernetes pods.
Supports Kind (local), EKS, GKE via kubeconfig or in-cluster auth.

Implements Spec Section 3 (Module 5).

---

## Scope

- Implement `qw/backends/k8s.py` with `K8sBackend(BaseExecutionBackend)`:
  - `__init__` — auto-detect auth: in-cluster (service account) or kubeconfig
  - `dispatch(task)` — creates an ephemeral pod with task data in ConfigMap,
    sets `restartPolicy: Never`, returns tracking UUID
  - `poll(task_id)` — checks pod status (Pending/Running/Succeeded/Failed)
  - `get_result(task_id)` — reads pod logs, deserializes result
  - `cancel(task_id)` — deletes the pod
  - `cleanup(task_id)` — deletes pod + ConfigMap
  - `health_check()` — checks K8s API connectivity, returns status + active pod count
- Handle optional import: if `kubernetes` package not installed, raise ImportError
- Pre-configured namespace only (never create namespaces)
- Register "k8s" in BackendRegistry
- Write unit tests (mock kubernetes client)

**NOT in scope**: Docker backend (TASK-035), namespace creation, GPU scheduling

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/k8s.py` | CREATE | K8sBackend implementation |
| `qw/backends/__init__.py` | MODIFY | Conditional export of K8sBackend |
| `tests/test_k8s_backend.py` | CREATE | Unit tests with mocked K8s client |

---

## Implementation Notes

### Pattern to Follow
```python
try:
    from kubernetes import client as k8s_client, config as k8s_config
    from kubernetes.client.exceptions import ApiException
    HAS_K8S = True
except ImportError:
    HAS_K8S = False

class K8sBackend(BaseExecutionBackend):
    def __init__(self, namespace: str = "default", kubeconfig: str | None = None):
        if not HAS_K8S:
            raise ImportError("kubernetes package required: uv pip install kubernetes")
        self.logger = logging.getLogger('QW.Backend.K8s')
        self._namespace = namespace
        # Auto-detect: in-cluster vs kubeconfig
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config(config_file=kubeconfig)
        self._core_v1 = k8s_client.CoreV1Api()
        self._tasks: dict[uuid.UUID, str] = {}  # task_id -> pod_name
```

### Key Constraints
- Pod naming: `qw-task-{short_uuid}` (must be DNS-compatible)
- Task data: create a ConfigMap, mount it as a volume in the pod
- `restartPolicy: Never` — Qworker manages retries, not K8s
- Pod labels: `app=qworker`, `task-id={uuid}` for easy querying
- Namespace comes from config — never auto-create
- Map pod phases to our status: Pending→pending, Running→running, Succeeded→completed, Failed→failed
- Handle `ApiException` (403 RBAC, 404 not found, etc.) gracefully

### References in Codebase
```python
# qw/conf.py — config pattern (will be extended in TASK-040)
K8S_NAMESPACE = config.get('K8S_NAMESPACE', fallback='default')
K8S_KUBECONFIG = config.get('K8S_KUBECONFIG', fallback=None)
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/backends/base.py (TASK-033)
class BaseExecutionBackend(ABC):
    async def dispatch(self, task: QueueWrapper) -> uuid.UUID: ...
    async def poll(self, task_id: uuid.UUID) -> str: ...
    async def get_result(self, task_id: uuid.UUID) -> TaskResult: ...
    async def cancel(self, task_id: uuid.UUID) -> bool: ...
    async def cleanup(self, task_id: uuid.UUID) -> None: ...
    async def health_check(self) -> dict: ...

# qw/backends/models.py (TASK-032)
class ContainerConfig(BaseModel):
    backend: Literal["docker", "k8s"]
    image: str
    env: dict[str, str]
    volumes: dict[str, str]
    resources: Optional[ContainerResources] = None
    namespace: Optional[str] = None

class ContainerResources(BaseModel):
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None
    cpu_request: Optional[str] = None
    memory_request: Optional[str] = None
```

### Does NOT Exist
- No existing K8s client usage in `qw/` (only health probes in health.py)
- No `kubernetes` package in current dependencies — optional extra
- No `qw/k8s/` module — everything in `qw/backends/k8s.py`
- The `kubernetes` client library is synchronous — must run blocking calls via
  `loop.run_in_executor()` or use `kubernetes_asyncio` if preferred

---

## Acceptance Criteria

- [ ] `from qw.backends.k8s import K8sBackend` works when kubernetes SDK installed
- [ ] ImportError with helpful message when SDK not installed
- [ ] `dispatch()` creates a pod with correct spec (image, env, configmap, restartPolicy=Never)
- [ ] `poll()` maps K8s pod phases to status strings correctly
- [ ] `get_result()` reads pod logs and deserializes
- [ ] `cancel()` deletes the pod
- [ ] `cleanup()` removes pod + configmap
- [ ] `health_check()` reports K8s API connectivity
- [ ] Uses configured namespace, never creates one
- [ ] In-cluster and kubeconfig auth both work
- [ ] All tests pass: `pytest tests/test_k8s_backend.py -v`

---

## Test Specification

```python
import pytest
from unittest.mock import MagicMock, patch

class TestK8sBackend:
    @pytest.fixture
    def mock_k8s(self):
        with patch("qw.backends.k8s.k8s_config") as mock_config, \
             patch("qw.backends.k8s.k8s_client") as mock_client:
            mock_config.load_incluster_config.side_effect = Exception("not in cluster")
            core_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = core_v1
            yield core_v1

    def test_import_error_without_sdk(self):
        pass

    @pytest.mark.asyncio
    async def test_dispatch_creates_pod(self, mock_k8s):
        pass

    @pytest.mark.asyncio
    async def test_poll_maps_phases(self, mock_k8s):
        pass

    @pytest.mark.asyncio
    async def test_namespace_from_config(self, mock_k8s):
        pass

    @pytest.mark.asyncio
    async def test_cleanup_removes_pod_and_configmap(self, mock_k8s):
        pass
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-033 is completed
3. **Implement** `qw/backends/k8s.py`
4. **Run tests**: `pytest tests/test_k8s_backend.py -v`
5. **Verify** acceptance criteria
6. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

*(Agent fills this in when done)*
