# Feature Proposal: launch-docker-k8s

**Date**: 2026-05-25
**Author**: Jesus Lara
**Status**: accepted
**Spec**: sdd/specs/launch-docker-k8s.spec.md

---

## Why

Qworker currently executes all tasks in-process (asyncio event loop + ThreadPoolExecutor).
This means every task shares the same memory space and resource constraints as the worker
process itself. There are two situations where this is limiting:

1. **Isolation & environment requirements**: Some tasks need specific runtimes, libraries,
   or system dependencies that differ from the worker's environment. Today there is no way
   to route a task to a pre-configured Docker container or Kubernetes pod.

2. **High-demand overflow**: When the worker is under heavy memory pressure (RAM > 90%),
   it has no escape valve. Tasks keep piling into the asyncio queue until the process OOMs
   or starts failing. If a Kubernetes cluster is configured, the worker should be able to
   offload tasks to ephemeral pods instead of queuing them locally.

## What Changes

- A task can optionally declare a **container execution target** (Docker image or K8s pod
  spec) in its configuration. When such a task arrives, Qworker routes it to the specified
  container backend instead of running it in-process.
- Qworker gains **resource-aware overflow**: when local RAM exceeds a configurable threshold
  (default 90%), new tasks are dispatched to a remote Kubernetes cluster (if configured)
  rather than the local asyncio queue. When RAM drops below a lower threshold (e.g. 75%),
  new incoming tasks automatically return to local execution (hysteresis to prevent flapping).
  Tasks already dispatched to K8s stay there — no migration back.
- A new **execution backend abstraction** is introduced so that the current in-process
  executor, Docker, and Kubernetes are all pluggable strategies behind a common interface.

## Design Decisions

### Result flow
Worker polls container status (`docker inspect` / `kubectl get pod`) and retrieves results
when the container completes. No callback webhooks or Redis result channels from inside
the container — keeps container images simple and decoupled from qworker internals.

### Docker scope
Support both local Docker daemon (`/var/run/docker.sock`) and remote Docker hosts
(`DOCKER_HOST` env var / TCP/SSH). Covers single-host dev and multi-host production.

### Overflow behavior
Auto-recover with hysteresis. When RAM > 90%, overflow to K8s. When RAM < 75%, new
incoming tasks resume local execution. Tasks already dispatched to K8s stay there —
no pull-back or migration. Prevents both OOM and flapping.

### Task lifecycle
Default: full lifecycle tracking (dispatch → poll → completion/timeout/retry → result).
Same guarantees as local execution. Optional: fire-and-forget mode via task config for
cases where the container handles its own reporting.

### Task container config
Dual source — task can carry its own container config in the wrapper metadata (takes
priority), and the worker also supports a config file mapping task names to default
container targets. This gives task authors flexibility while allowing ops to set defaults.

### K8s authentication
Auto-detect: if running inside K8s, use in-cluster service account. Otherwise fall back
to kubeconfig (`~/.kube/config` or `KUBECONFIG`). Works for Kind (local), EKS, and GKE.

### K8s namespace
Pre-configured by the operator. Qworker never creates namespaces — respects RBAC
boundaries. Namespace specified in worker config.

### Image management
Run pre-existing images only. Building/pushing images is out of scope. Tasks reference
images that already exist in a registry (or are available locally).

### Retry policy
Qworker manages retries using the existing retry policy (`WORKER_RETRY_COUNT` /
`WORKER_RETRY_INTERVAL`). If a container fails, Qworker re-launches it. Consistent
behavior across all backends. Container orchestrator restart policies are set to "Never"
to avoid double-retrying.

### Serialization
Cloudpickle for Python containers (preserves full type fidelity, consistent with current
in-process behavior). JSON fallback for non-Python containers. Backend auto-detects
based on container config or explicit setting.

## Capabilities

### New Capabilities
- `execution-backend-abstraction`: Pluggable execution backend interface
  (`BaseExecutionBackend`) with local, Docker, and K8s implementations. Defines the
  contract: `dispatch(task)`, `poll(task_id)`, `cancel(task_id)`, `get_result(task_id)`.
- `docker-task-executor`: Execute tasks inside Docker containers via local daemon or
  remote Docker host. Polls container status for results. Supports resource limits
  (CPU, memory) per container.
- `k8s-task-executor`: Execute tasks as ephemeral Kubernetes pods. Supports Kind, EKS,
  GKE via kubeconfig or in-cluster auth. Pre-configured namespace. Pods are cleaned up
  after completion.
- `resource-monitor`: Monitor local RAM usage via `psutil`. Exposes current memory
  metrics to the overflow decision layer. Configurable thresholds.
- `resource-aware-overflow`: When RAM > high threshold, route new tasks to K8s backend.
  When RAM < low threshold, resume local. No migration of in-flight tasks.
- `container-task-config`: Task-level container configuration in `QueueWrapper` metadata
  plus worker-side config file for default task-to-container mappings.

### Modified Capabilities
- `task-executor`: `TaskExecutor` gains a dispatch layer that checks for container config
  before running in-process. Becomes the "local" backend behind the new abstraction.
- `queue-manager`: `QueueManager` gains overflow routing when resource thresholds are
  breached, delegating to the K8s backend.

## Impact

- **Task authors**: Can annotate tasks with container config via QClient. Existing tasks
  remain unchanged — in-process execution is the default.
- **Operators/DevOps**: New configuration surface: Docker host, K8s cluster, overflow
  thresholds, task-to-container mappings, K8s namespace.
- **Dependencies**: New optional packages: `docker` (Docker SDK for Python),
  `kubernetes` (K8s client), `psutil` (resource monitoring). Only needed when the
  respective backend is enabled.
- **Deployment**: Workers using Docker need socket access or DOCKER_HOST. Workers using
  K8s need kubeconfig or in-cluster service account.
- **Health server**: Extend existing `/supervisor/status` endpoint to include container
  backend status (connected/disconnected, active containers/pods, overflow state).
- **Breaking changes**: None. In-process execution remains the default. Feature is
  entirely opt-in via configuration.

## Open Questions

None — all questions resolved during discussion.
