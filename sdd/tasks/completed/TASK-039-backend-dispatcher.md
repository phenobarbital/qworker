# TASK-039: Backend Dispatcher Integration

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: XL (> 8h)
**Depends-on**: TASK-034, TASK-035, TASK-036, TASK-037, TASK-038
**Assigned-to**: unassigned

---

## Context

The central orchestration module. Sits inside `QueueManager` and routes tasks
to the appropriate backend based on: (1) task's container_config, (2) worker-side
task mapping file, (3) resource monitor overflow state.

Implements Spec Section 3 (Module 8).

---

## Scope

- Implement `qw/backends/dispatch.py` with `BackendDispatcher`:
  - `__init__` — initializes backends (local always, docker/k8s if configured),
    loads task mapping file, creates ResourceMonitor
  - `resolve_backend(task) -> BaseExecutionBackend` — determines which backend:
    1. Check task's `container_config` (highest priority)
    2. Check worker-side mapping file for task name match
    3. Check ResourceMonitor for overflow (route to K8s if overflowing)
    4. Default to LocalBackend
  - `dispatch_and_track(task) -> TaskResult` — dispatches to resolved backend,
    manages poll loop (unless fire_and_forget), handles retries on failure
  - `load_task_mappings(filepath) -> list[ContainerTaskMapping]` — reads YAML/TOML config
- Modify `qw/queues/manager.py`:
  - `queue_handler()` — before calling `TaskExecutor(task)` directly, check if task
    should go to a container backend via `BackendDispatcher`
  - Only activate if backends are configured (backward compatible)
- Write unit tests with mocked backends

**NOT in scope**: Health server extension (TASK-041), client-side changes (done in TASK-038)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/dispatch.py` | CREATE | BackendDispatcher class |
| `qw/queues/manager.py` | MODIFY | Integrate dispatcher into queue_handler |
| `tests/test_backend_dispatcher.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
class BackendDispatcher:
    def __init__(
        self,
        local_backend: LocalBackend,
        docker_backend: DockerBackend | None = None,
        k8s_backend: K8sBackend | None = None,
        resource_monitor: ResourceMonitor | None = None,
        task_mappings: list[ContainerTaskMapping] | None = None,
    ):
        self.logger = logging.getLogger('QW.Backend.Dispatcher')
        self._local = local_backend
        self._docker = docker_backend
        self._k8s = k8s_backend
        self._monitor = resource_monitor
        self._mappings = task_mappings or []

    def resolve_backend(self, task: QueueWrapper) -> BaseExecutionBackend:
        # 1. Task-level container_config takes priority
        if hasattr(task, 'container_config') and task.container_config:
            cfg = task.container_config
            if cfg.backend == "docker":
                if self._docker is None:
                    raise RuntimeError("Docker backend not configured")
                return self._docker
            elif cfg.backend == "k8s":
                if self._k8s is None:
                    raise RuntimeError("K8s backend not configured")
                return self._k8s

        # 2. Worker-side task mapping
        # ... match task name against self._mappings patterns

        # 3. Resource overflow
        if self._monitor and self._k8s and self._monitor.should_overflow():
            return self._k8s

        # 4. Default to local
        return self._local
```

### Key Constraints
- Backward compatible: if no backends configured, `queue_handler` behavior is identical
- The dispatcher is optional in QueueManager — only active when configured
- Poll loop for container tasks: poll every `CONTAINER_POLL_INTERVAL` seconds (default 5)
- Retry logic: on container failure, re-dispatch up to `WORKER_RETRY_COUNT` times
- Fire-and-forget: dispatch and return immediately, no poll loop
- Task mapping file format: YAML or TOML, loaded at startup
- `fnmatch` for task name pattern matching in mappings

### Integration with QueueManager.queue_handler
```python
# In queue_handler (manager.py:387-472), the change is:
# Before: executor = TaskExecutor(task); result = await executor.run()
# After:
if self._dispatcher and (
    (hasattr(task, 'container_config') and task.container_config)
    or self._dispatcher.should_use_container(task)
):
    result = await self._dispatcher.dispatch_and_track(task)
else:
    executor = TaskExecutor(task)
    result = await executor.run()
```

### References in Codebase
```python
# qw/queues/manager.py:387-412 — queue_handler current flow
async def queue_handler(self):
    while True:
        result = None
        task = await self.queue.get()
        ...
        executor = TaskExecutor(task)
        result = await executor.run()

# qw/queues/manager.py:38-43 — QueueManager.__init__ signature
def __init__(
    self,
    worker_name: str,
    state_tracker=None,
    policy: QueueSizePolicy | None = None,
) -> None:
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/queues/manager.py:35-43
class QueueManager:
    def __init__(
        self,
        worker_name: str,
        state_tracker=None,
        policy: QueueSizePolicy | None = None,
    ) -> None:

# qw/queues/manager.py:387
    async def queue_handler(self):
        # line 411: executor = TaskExecutor(task)
        # line 412: result = await executor.run()

# qw/queues/manager.py:192
    async def put(self, task: QueueWrapper, id: str) -> bool:

# qw/executor/__init__.py:16
class TaskExecutor:
    def __init__(self, task, *args, **kwargs):
    async def run(self):  # line 84

# qw/conf.py:35-36
RESOURCE_THRESHOLD = config.getint('RESOURCE_THRESHOLD', fallback=90)
CHECK_RESOURCE_USAGE = config.getboolean('CHECK_RESOURCE_USAGE', fallback=True)

# qw/conf.py:37-38
WORKER_RETRY_INTERVAL = config.getint('WORKER_RETRY_INTERVAL', fallback=10)
WORKER_RETRY_COUNT = config.getint('WORKER_RETRY_COUNT', fallback=2)
```

### Does NOT Exist
- No `self._dispatcher` in `QueueManager` currently — add in this task
- No `BackendDispatcher` anywhere in codebase — this task creates it
- No task mapping file format defined — choose YAML with PyYAML (already in deps)
  or TOML (stdlib tomllib in Python 3.11+)
- `QueueManager.__init__` does NOT accept a `dispatcher` param — must add it

---

## Acceptance Criteria

- [ ] `from qw.backends.dispatch import BackendDispatcher`
- [ ] Task with `container_config.backend="docker"` routes to DockerBackend
- [ ] Task with `container_config.backend="k8s"` routes to K8sBackend
- [ ] Task without container_config routes to LocalBackend
- [ ] Task matching a mapping pattern routes to configured backend
- [ ] Under memory pressure (overflow), tasks route to K8s
- [ ] After recovery, new tasks route to Local
- [ ] Fire-and-forget tasks return immediately after dispatch
- [ ] Retries work for failed container tasks
- [ ] QueueManager without dispatcher behaves identically to before
- [ ] All tests pass: `pytest tests/test_backend_dispatcher.py -v`

---

## Test Specification

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from qw.backends.dispatch import BackendDispatcher
from qw.backends.local import LocalBackend
from qw.backends.models import ContainerConfig
from qw.wrappers.base import QueueWrapper


class TestBackendDispatcher:
    @pytest.fixture
    def dispatcher(self):
        local = MagicMock(spec=LocalBackend)
        docker = MagicMock()
        k8s = MagicMock()
        monitor = MagicMock()
        monitor.should_overflow.return_value = False
        return BackendDispatcher(
            local_backend=local,
            docker_backend=docker,
            k8s_backend=k8s,
            resource_monitor=monitor,
        )

    def test_routes_docker_task(self, dispatcher):
        async def fn(): pass
        task = QueueWrapper(coro=fn, container_config=ContainerConfig(
            backend="docker", image="x"
        ))
        backend = dispatcher.resolve_backend(task)
        assert backend is dispatcher._docker

    def test_routes_k8s_task(self, dispatcher):
        async def fn(): pass
        task = QueueWrapper(coro=fn, container_config=ContainerConfig(
            backend="k8s", image="x"
        ))
        backend = dispatcher.resolve_backend(task)
        assert backend is dispatcher._k8s

    def test_routes_local_default(self, dispatcher):
        async def fn(): pass
        task = QueueWrapper(coro=fn)
        backend = dispatcher.resolve_backend(task)
        assert backend is dispatcher._local

    def test_overflow_routes_to_k8s(self, dispatcher):
        dispatcher._monitor.should_overflow.return_value = True
        async def fn(): pass
        task = QueueWrapper(coro=fn)
        backend = dispatcher.resolve_backend(task)
        assert backend is dispatcher._k8s
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — ALL of TASK-034 through TASK-038 must be completed
3. **Implement** `qw/backends/dispatch.py`
4. **Modify** `qw/queues/manager.py` to integrate the dispatcher
5. **Run tests**: `pytest tests/test_backend_dispatcher.py -v`
6. **Run existing tests** to verify no regression in queue_handler
7. **Verify** acceptance criteria
8. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (sdd-worker)
**Date**: 2026-05-26
**Notes**: All 18 tests pass. BackendDispatcher implements 4-priority routing: explicit container_config > task mapping file (fnmatch patterns) > resource overflow > local default. QueueManager now accepts optional dispatcher param and routes container tasks through it. load_task_mappings supports YAML (.yaml/.yml) and TOML (.toml) files. ContainerTaskMapping uses nested config: ContainerConfig field (not flat backend/image fields as spec example showed — the model spec from TASK-032 was authoritative).
**Deviations from spec**: ContainerTaskMapping uses `config: ContainerConfig` (nested) not flat `backend/image` fields — this is correct per the model definition in TASK-032. All 221 existing tests pass (no regression).
