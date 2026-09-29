# Feature Specification: Launch Docker/K8s Task Execution

**Feature ID**: FEAT-006
**Date**: 2026-05-25
**Author**: Jesus Lara
**Status**: draft
**Target version**: 2.1.0
**Proposal**: sdd/proposals/launch-docker-k8s.proposal.md

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement

Qworker executes all tasks in-process via asyncio event loop + ThreadPoolExecutor.
This creates two limitations:

1. **No runtime isolation**: Tasks that require different Python versions, system
   libraries, or runtime environments cannot be accommodated. Everything shares the
   worker's process space.

2. **No overflow under pressure**: When memory usage spikes (RAM > 90%), tasks continue
   queuing locally until the process OOMs or starts failing. There is no mechanism to
   offload work to external compute.

### Goals
- Allow tasks to declare a container execution target (Docker image or K8s pod spec)
  so Qworker routes them to the appropriate backend instead of running in-process.
- Enable automatic overflow to a Kubernetes cluster when local memory pressure exceeds
  a configurable threshold, with hysteresis-based auto-recovery.
- Introduce a pluggable execution backend abstraction so local, Docker, and K8s
  backends share a common interface.
- Maintain full backward compatibility — in-process execution remains the default.

### Non-Goals (explicitly out of scope)
- Building or pushing Docker images (only run pre-existing images)
- Kubernetes namespace creation or cluster provisioning
- Container networking/service mesh configuration
- GPU scheduling or specialized hardware affinity
- Task result streaming (only final result retrieval)
- Multi-cloud orchestration (one cluster config at a time)

---

## 2. Architectural Design

### Overview

A new `BaseExecutionBackend` abstraction sits between `QueueManager` and the actual
task execution. The current in-process `TaskExecutor` becomes the `LocalBackend`.
Two new backends — `DockerBackend` and `K8sBackend` — execute tasks inside containers.

A `ResourceMonitor` tracks local RAM usage. When memory exceeds the high threshold
(default 90%, from existing `RESOURCE_THRESHOLD`), the `QueueManager` routes new tasks
to the K8s backend. When memory drops below the low threshold (default 75%), new tasks
resume local routing. Tasks already dispatched to K8s are never pulled back.

Tasks declare container targets via a `ContainerConfig` in their wrapper metadata,
or via a worker-side config file that maps task names to default container targets.

### Component Diagram
```
QClient
  │
  ▼
Redis Stream / TCP / RabbitMQ
  │
  ▼
QWorker (server.py)
  │
  ▼
QueueManager ─────────────────────────────┐
  │                                       │
  │  ResourceMonitor                      │
  │  (psutil RAM check)                   │
  │     │                                 │
  │     ├─ RAM < 75% ──► LocalBackend     │
  │     │                    │            │
  │     │                    ▼            │
  │     │               TaskExecutor      │
  │     │               (in-process)      │
  │     │                                 │
  │     └─ RAM > 90% ──► K8sBackend       │
  │                       (overflow)      │
  │                                       │
  ├─ task.container_config? ──────────────┤
  │     │                                 │
  │     ├─ backend=docker ► DockerBackend │
  │     │                    │            │
  │     │                    ▼            │
  │     │               docker run        │
  │     │               docker inspect    │
  │     │                                 │
  │     └─ backend=k8s ──► K8sBackend     │
  │                          │            │
  │                          ▼            │
  │                     kubectl create    │
  │                     kubectl get pod   │
  │                                       │
  └───────────────────────────────────────┘
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QueueWrapper` | extends | Add optional `container_config` attribute |
| `FuncWrapper` | inherits change | Gets `container_config` from `QueueWrapper` |
| `TaskWrapper` | inherits change | Gets `container_config` from `QueueWrapper` |
| `TaskExecutor` | wraps | Becomes `LocalBackend` behind `BaseExecutionBackend` |
| `QueueManager` | modifies | Dispatch logic checks container config + resource state |
| `conf.py` | extends | New config vars for Docker/K8s/overflow settings |
| `HealthServer` | extends | New status fields for container backends |
| `RESOURCE_THRESHOLD` | uses existing | Already defined in `conf.py` at 90 |
| `CHECK_RESOURCE_USAGE` | uses existing | Already defined in `conf.py` as True |

### Data Models
```python
from typing import Optional, Literal
from pydantic import BaseModel, Field


class ContainerResources(BaseModel):
    """Resource limits for container execution."""
    cpu_limit: Optional[str] = Field(None, description="CPU limit, e.g. '500m', '2'")
    memory_limit: Optional[str] = Field(None, description="Memory limit, e.g. '512Mi', '2Gi'")
    cpu_request: Optional[str] = Field(None, description="CPU request (K8s only)")
    memory_request: Optional[str] = Field(None, description="Memory request (K8s only)")


class ContainerConfig(BaseModel):
    """Task-level container execution configuration."""
    backend: Literal["docker", "k8s"] = Field(..., description="Execution backend")
    image: str = Field(..., description="Container image (must exist in registry)")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    volumes: dict[str, str] = Field(default_factory=dict, description="Host:container volume mounts")
    resources: Optional[ContainerResources] = None
    fire_and_forget: bool = Field(default=False, description="Skip result tracking")
    timeout: Optional[int] = Field(None, description="Override WORKER_TASK_TIMEOUT for this task (minutes)")
    namespace: Optional[str] = Field(None, description="K8s namespace override")


class ContainerTaskMapping(BaseModel):
    """Worker-side config mapping task names to container targets."""
    task_pattern: str = Field(..., description="Task name or glob pattern")
    config: ContainerConfig
```

### New Public Interfaces
```python
from abc import ABC, abstractmethod
from typing import Any, Optional
import uuid


class TaskResult:
    """Result from a backend execution."""
    task_id: uuid.UUID
    success: bool
    result: Any
    error: Optional[str]
    execution_time: float
    backend: str


class BaseExecutionBackend(ABC):
    """Interface for all execution backends."""

    @abstractmethod
    async def dispatch(self, task: QueueWrapper) -> uuid.UUID:
        """Submit a task for execution. Returns a tracking ID."""

    @abstractmethod
    async def poll(self, task_id: uuid.UUID) -> str:
        """Check task status. Returns: 'pending', 'running', 'completed', 'failed'."""

    @abstractmethod
    async def get_result(self, task_id: uuid.UUID) -> TaskResult:
        """Retrieve the result of a completed task."""

    @abstractmethod
    async def cancel(self, task_id: uuid.UUID) -> bool:
        """Cancel a running task. Returns True if cancelled."""

    @abstractmethod
    async def cleanup(self, task_id: uuid.UUID) -> None:
        """Clean up resources after task completion (remove container/pod)."""

    @abstractmethod
    async def health_check(self) -> dict:
        """Return backend health status for /supervisor/status endpoint."""


class ResourceMonitor:
    """Monitors local system resources for overflow decisions."""

    async def get_memory_percent(self) -> float:
        """Return current RAM usage as percentage (0-100)."""

    def should_overflow(self) -> bool:
        """Return True if tasks should overflow to remote backend."""

    def should_recover(self) -> bool:
        """Return True if tasks can return to local execution."""
```

---

## 3. Module Breakdown

> These directly map to Task Artifacts in Phase 2.

### Module 1: Container Configuration Models
- **Path**: `qw/backends/models.py`
- **Responsibility**: Pydantic models for `ContainerConfig`, `ContainerResources`,
  `ContainerTaskMapping`, `TaskResult`. Serialization helpers for cloudpickle/JSON.
- **Depends on**: None (pure data models)

### Module 2: Execution Backend Abstraction
- **Path**: `qw/backends/base.py`
- **Responsibility**: `BaseExecutionBackend` ABC defining the backend contract.
  Also contains `BackendRegistry` for registering/resolving backends by name.
- **Depends on**: Module 1

### Module 3: Local Backend
- **Path**: `qw/backends/local.py`
- **Responsibility**: Wraps existing `TaskExecutor` as a `BaseExecutionBackend`
  implementation. This is the default backend. No behavioral change from current
  in-process execution.
- **Depends on**: Module 2, existing `qw/executor/`

### Module 4: Docker Backend
- **Path**: `qw/backends/docker.py`
- **Responsibility**: `DockerBackend` implementation. Creates containers via Docker
  SDK, passes task data via environment variables + volume-mounted pickle/JSON file,
  polls container status via `docker inspect`, retrieves results from container
  stdout/mounted output file. Supports local daemon and remote Docker hosts.
- **Depends on**: Module 1, Module 2

### Module 5: Kubernetes Backend
- **Path**: `qw/backends/k8s.py`
- **Responsibility**: `K8sBackend` implementation. Creates ephemeral pods via
  kubernetes client, passes task data via ConfigMap or Secret, polls pod status,
  retrieves results from pod logs or mounted volume. Supports in-cluster auth
  and kubeconfig. Pre-configured namespace only.
- **Depends on**: Module 1, Module 2

### Module 6: Resource Monitor
- **Path**: `qw/backends/monitor.py`
- **Responsibility**: `ResourceMonitor` class using `psutil` to track RAM usage.
  Provides `should_overflow()` and `should_recover()` with hysteresis logic
  (high threshold from `RESOURCE_THRESHOLD`, low threshold configurable).
- **Depends on**: Module 1 (for config), existing `qw/conf.py`

### Module 7: QueueWrapper Extension
- **Path**: `qw/wrappers/base.py` (modify existing)
- **Responsibility**: Add optional `container_config: ContainerConfig | None` attribute
  to `QueueWrapper`. Update serialization in `QClient.publish()` to include container
  config in the Redis Stream payload.
- **Depends on**: Module 1

### Module 8: Backend Dispatcher Integration
- **Path**: `qw/backends/dispatch.py`
- **Responsibility**: `BackendDispatcher` class that sits in `QueueManager`. Reads
  task's `container_config`, checks worker-side mapping file, consults
  `ResourceMonitor` for overflow, and routes to the appropriate backend. Manages
  the poll loop for container-executed tasks.
- **Depends on**: Module 2, Module 3, Module 4, Module 5, Module 6

### Module 9: Configuration Extension
- **Path**: `qw/conf.py` (modify existing)
- **Responsibility**: New config variables: `DOCKER_HOST`, `K8S_NAMESPACE`,
  `K8S_KUBECONFIG`, `CONTAINER_TASK_MAPPING_FILE`, `RESOURCE_OVERFLOW_ENABLED`,
  `RESOURCE_RECOVER_THRESHOLD`, `CONTAINER_POLL_INTERVAL`,
  `CONTAINER_DEFAULT_TIMEOUT`.
- **Depends on**: None

### Module 10: Health Server Extension
- **Path**: `qw/health.py` (modify existing)
- **Responsibility**: Extend `/supervisor/status` to include container backend
  status: connected/disconnected, active containers/pods count, overflow state,
  resource monitor metrics.
- **Depends on**: Module 6, Module 8

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_container_config_valid` | Module 1 | ContainerConfig accepts valid docker/k8s config |
| `test_container_config_defaults` | Module 1 | Default values (fire_and_forget=False, etc.) |
| `test_container_config_serialization` | Module 1 | Roundtrip cloudpickle and JSON serialization |
| `test_backend_registry` | Module 2 | Register and resolve backends by name |
| `test_local_backend_dispatch` | Module 3 | LocalBackend runs task and returns result |
| `test_local_backend_timeout` | Module 3 | LocalBackend respects timeout |
| `test_docker_backend_create_container` | Module 4 | DockerBackend creates container with correct config |
| `test_docker_backend_poll_status` | Module 4 | DockerBackend polls container and returns status |
| `test_docker_backend_get_result` | Module 4 | DockerBackend retrieves result from completed container |
| `test_docker_backend_cleanup` | Module 4 | DockerBackend removes container after completion |
| `test_k8s_backend_create_pod` | Module 5 | K8sBackend creates pod with correct spec |
| `test_k8s_backend_poll_status` | Module 5 | K8sBackend polls pod and returns status |
| `test_k8s_backend_namespace` | Module 5 | K8sBackend uses configured namespace |
| `test_k8s_backend_auth_modes` | Module 5 | K8sBackend auto-detects in-cluster vs kubeconfig |
| `test_resource_monitor_threshold` | Module 6 | should_overflow() triggers at high threshold |
| `test_resource_monitor_hysteresis` | Module 6 | should_recover() triggers at low threshold |
| `test_resource_monitor_no_flap` | Module 6 | Between thresholds, state doesn't change |
| `test_wrapper_container_config` | Module 7 | QueueWrapper accepts and stores ContainerConfig |
| `test_wrapper_backward_compat` | Module 7 | Existing wrappers without config still work |
| `test_dispatcher_routes_docker` | Module 8 | Task with docker config goes to DockerBackend |
| `test_dispatcher_routes_k8s` | Module 8 | Task with k8s config goes to K8sBackend |
| `test_dispatcher_routes_local` | Module 8 | Task without config goes to LocalBackend |
| `test_dispatcher_overflow` | Module 8 | Under memory pressure, tasks overflow to K8s |
| `test_dispatcher_recover` | Module 8 | After recovery, new tasks go local |
| `test_dispatcher_fire_and_forget` | Module 8 | fire_and_forget skips poll loop |
| `test_retry_on_container_failure` | Module 8 | Qworker retries failed container tasks |

### Integration Tests
| Test | Description |
|---|---|
| `test_docker_end_to_end` | Publish task with docker config via QClient, verify container runs and result returns |
| `test_local_fallback` | Task with container config but no Docker/K8s available falls back gracefully |
| `test_overflow_simulation` | Mock high RAM, verify task routes to K8s backend |
| `test_mixed_task_stream` | Stream of local + container tasks dispatched correctly |

### Test Data / Fixtures
```python
@pytest.fixture
def container_config_docker():
    return ContainerConfig(
        backend="docker",
        image="python:3.12-slim",
        env={"TASK_MODE": "test"},
        resources=ContainerResources(memory_limit="256Mi"),
    )

@pytest.fixture
def container_config_k8s():
    return ContainerConfig(
        backend="k8s",
        image="registry.example.com/worker:latest",
        namespace="qworker-tasks",
        resources=ContainerResources(
            cpu_request="100m", cpu_limit="500m",
            memory_request="128Mi", memory_limit="512Mi",
        ),
    )

@pytest.fixture
def mock_resource_monitor():
    """ResourceMonitor with controllable memory readings."""
    monitor = ResourceMonitor()
    monitor._override_memory = 50.0  # Start at 50% RAM
    return monitor
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] All unit tests pass (`pytest tests/unit/backends/ -v`)
- [ ] Docker integration test passes with local Docker daemon
- [ ] K8s integration test passes with Kind cluster
- [ ] Existing tasks without container config execute identically to before
- [ ] `QClient.publish()` supports optional `container_config` parameter
- [ ] Worker-side task mapping config file works for default container targets
- [ ] Overflow triggers at RESOURCE_THRESHOLD (90%) and recovers at 75%
- [ ] Container tasks respect WORKER_RETRY_COUNT retry policy
- [ ] Fire-and-forget mode works (task dispatched, no poll loop)
- [ ] `/supervisor/status` reports container backend health
- [ ] All new dependencies are optional (feature works without docker/kubernetes packages)
- [ ] No breaking changes to existing public API
- [ ] Documentation updated for new configuration variables

---

## 6. Implementation Notes & Constraints

### Patterns to Follow
- Use ABC pattern for `BaseExecutionBackend` (same style as existing abstractions)
- All backend methods are async (async-first design)
- Pydantic models for all configuration and result data
- Logger via `logging.getLogger('QW.Backend.<name>')` per backend
- Config via `navconfig` pattern (same as existing `qw/conf.py`)

### Known Risks / Gotchas
- **Cloudpickle serialization across containers**: The container's Python environment
  must have compatible versions of libraries for cloudpickle deserialization. JSON
  fallback mitigates this for non-Python containers.
- **Docker socket security**: Mounting Docker socket gives the worker root-equivalent
  access to the host. Document this clearly. Remote Docker hosts over TLS are preferred
  for production.
- **K8s RBAC**: The service account or kubeconfig must have permissions to create/delete
  pods and configmaps in the configured namespace. Insufficient permissions will fail
  at runtime.
- **Poll interval trade-off**: Too frequent polling wastes CPU/API calls. Too infrequent
  delays result delivery. Default to 5s, make configurable.
- **Resource monitor accuracy**: `psutil` reports system-wide RAM, not per-process.
  In containerized deployments, cgroup limits may differ from system total. Use
  `psutil.virtual_memory()` which respects cgroup limits on modern kernels.

### External Dependencies
| Package | Version | Reason | Required |
|---|---|---|---|
| `docker` | `>=7.0` | Docker SDK for Python (container lifecycle) | Optional |
| `kubernetes` | `>=29.0` | Official K8s client (pod lifecycle) | Optional |
| `psutil` | `>=5.9` | System resource monitoring (RAM usage) | Optional |

All dependencies are optional extras. Install via:
```
uv pip install qworker[docker]      # Docker support
uv pip install qworker[k8s]         # Kubernetes support
uv pip install qworker[containers]  # Both + psutil
```

---

## 7. Open Questions

> All questions resolved during proposal discussion. None remaining.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-25 | Jesus Lara | Initial draft from proposal discussion |
