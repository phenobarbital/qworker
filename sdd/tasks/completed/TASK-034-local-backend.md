# TASK-034: Local Backend

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-033
**Assigned-to**: unassigned

---

## Context

Wraps the existing `TaskExecutor` as a `BaseExecutionBackend` implementation.
This is the default backend — all tasks without container config go here.
Must preserve exact current behavior.

Implements Spec Section 3 (Module 3).

---

## Scope

- Implement `qw/backends/local.py` with `LocalBackend(BaseExecutionBackend)`:
  - `dispatch(task)` — runs the task via `TaskExecutor.run()`, stores result keyed by task UUID
  - `poll(task_id)` — returns status from internal tracking dict
  - `get_result(task_id)` — returns `TaskResult` from stored results
  - `cancel(task_id)` — cancels the asyncio task if still running
  - `cleanup(task_id)` — removes tracking entry
  - `health_check()` — returns `{"status": "ok", "active_tasks": N}`
- Register "local" in BackendRegistry
- Write unit tests

**NOT in scope**: Docker/K8s backends (TASK-035, 036), dispatcher integration (TASK-039)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/local.py` | CREATE | LocalBackend wrapping TaskExecutor |
| `qw/backends/__init__.py` | MODIFY | Export LocalBackend |
| `tests/test_local_backend.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
# Wraps existing TaskExecutor without changing its behavior
from .base import BaseExecutionBackend
from ..executor import TaskExecutor
from .models import TaskResult

class LocalBackend(BaseExecutionBackend):
    def __init__(self):
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._results: dict[uuid.UUID, TaskResult] = {}
        self.logger = logging.getLogger('QW.Backend.Local')
```

### Key Constraints
- Must NOT change `TaskExecutor` behavior — wrap, don't modify
- `dispatch` should create an `asyncio.Task` so it's non-blocking
- Track task lifecycle internally (`_tasks` dict with asyncio.Task references)
- `TaskExecutor` already has a semaphore (WORKER_CONCURRENCY_NUMBER) — respect it

### References in Codebase
```python
# qw/executor/__init__.py:16-17
class TaskExecutor:
    def __init__(self, task, *args, **kwargs):

# qw/executor/__init__.py:84
    async def run(self):  # returns result or exception
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/executor/__init__.py:16
class TaskExecutor:
    def __init__(self, task, *args, **kwargs):
        self.task = task
        self.semaphore = asyncio.Semaphore(WORKER_CONCURRENCY_NUMBER)

    async def run(self):  # line 84 — dispatches based on task type, returns result

# qw/backends/base.py (TASK-033)
class BaseExecutionBackend(ABC):
    async def dispatch(self, task: QueueWrapper) -> uuid.UUID: ...
    async def poll(self, task_id: uuid.UUID) -> str: ...
    async def get_result(self, task_id: uuid.UUID) -> TaskResult: ...
    async def cancel(self, task_id: uuid.UUID) -> bool: ...
    async def cleanup(self, task_id: uuid.UUID) -> None: ...
    async def health_check(self) -> dict: ...

# qw/backends/models.py (TASK-032)
class TaskResult(BaseModel):
    task_id: uuid.UUID
    success: bool
    result: Any
    error: Optional[str]
    execution_time: float
    backend: str
```

### Does NOT Exist
- `TaskExecutor` has no `async def start()` method — use `run()` directly
- `TaskExecutor` has no task tracking — LocalBackend must add its own
- No `qw/executor/local.py` — don't create one, put this in `qw/backends/local.py`

---

## Acceptance Criteria

- [ ] `from qw.backends.local import LocalBackend`
- [ ] `LocalBackend` is a concrete `BaseExecutionBackend`
- [ ] `dispatch()` runs a FuncWrapper task and returns a UUID
- [ ] `poll()` returns correct status during/after execution
- [ ] `get_result()` returns a valid `TaskResult`
- [ ] `cancel()` cancels a running task
- [ ] Existing `TaskExecutor` is NOT modified
- [ ] All tests pass: `pytest tests/test_local_backend.py -v`

---

## Test Specification

```python
import pytest
import asyncio
import uuid
from qw.backends.local import LocalBackend
from qw.backends.models import TaskResult
from qw.wrappers.base import QueueWrapper


class TestLocalBackend:
    @pytest.fixture
    def backend(self):
        return LocalBackend()

    @pytest.mark.asyncio
    async def test_dispatch_returns_uuid(self, backend):
        async def dummy(): return 42
        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        assert isinstance(task_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_poll_completed(self, backend):
        async def dummy(): return 42
        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        await asyncio.sleep(0.2)
        status = await backend.poll(task_id)
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_get_result(self, backend):
        async def dummy(): return 42
        task = QueueWrapper(coro=dummy)
        task_id = await backend.dispatch(task)
        await asyncio.sleep(0.2)
        result = await backend.get_result(task_id)
        assert isinstance(result, TaskResult)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_health_check(self, backend):
        health = await backend.health_check()
        assert health["status"] == "ok"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-033 is completed
3. **Implement** `qw/backends/local.py`
4. **Run tests**: `pytest tests/test_local_backend.py -v`
5. **Verify** acceptance criteria
6. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (sdd-worker)
**Date**: 2026-05-25
**Notes**: All 11 tests pass. TaskExecutor import is deferred inside dispatch() to avoid circular imports.
**Deviations from spec**: none
