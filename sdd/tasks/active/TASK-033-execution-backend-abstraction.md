# TASK-033: Execution Backend Abstraction

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-032
**Assigned-to**: unassigned

---

## Context

Defines the `BaseExecutionBackend` ABC and `BackendRegistry` that all execution
backends (local, Docker, K8s) implement. This is the pluggable interface layer.

Implements Spec Section 2 (New Public Interfaces) and Section 3 (Module 2).

---

## Scope

- Implement `qw/backends/base.py` with:
  - `BaseExecutionBackend` ABC with abstract methods: `dispatch`, `poll`, `get_result`,
    `cancel`, `cleanup`, `health_check`
  - `BackendRegistry` class for registering/resolving backends by name (e.g., "local", "docker", "k8s")
  - Registry should be a simple dict-based singleton
- Write unit tests for the registry

**NOT in scope**: Concrete backend implementations (TASK-034, 035, 036)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/base.py` | CREATE | ABC + BackendRegistry |
| `qw/backends/__init__.py` | MODIFY | Export BaseExecutionBackend, BackendRegistry |
| `tests/test_backend_base.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
from abc import ABC, abstractmethod
from typing import Any, Optional
import uuid
from .models import TaskResult, ContainerConfig
from qw.wrappers.base import QueueWrapper


class BaseExecutionBackend(ABC):
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
        """Clean up resources after task completion."""

    @abstractmethod
    async def health_check(self) -> dict:
        """Return backend health status."""
```

### Key Constraints
- All methods are async
- `dispatch` returns a UUID tracking ID
- `poll` returns one of: `'pending'`, `'running'`, `'completed'`, `'failed'`
- `BackendRegistry` must support: `register(name, cls)`, `get(name)`, `list_backends()`
- Logger: `logging.getLogger('QW.Backend')`

### References in Codebase
- `qw/backends/models.py` — TaskResult, ContainerConfig (TASK-032)
- `qw/wrappers/base.py` — QueueWrapper class

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/backends/models.py (created by TASK-032)
class TaskResult(BaseModel):
    task_id: uuid.UUID
    success: bool
    result: Any
    error: Optional[str]
    execution_time: float
    backend: str

class ContainerConfig(BaseModel):
    backend: Literal["docker", "k8s"]
    image: str
    ...

# qw/wrappers/base.py:10
class QueueWrapper:
    _id: uuid.UUID
    args: tuple
    kwargs: dict
    retries: int
    coro: Any
```

### Does NOT Exist
- No existing ABC or base class in `qw/backends/` — this creates the first
- No existing registry pattern in `qw/` — implement a fresh one
- No `qw/backends/registry.py` — put BackendRegistry in `base.py`

---

## Acceptance Criteria

- [ ] `from qw.backends.base import BaseExecutionBackend, BackendRegistry`
- [ ] Cannot instantiate `BaseExecutionBackend` directly (ABC)
- [ ] A concrete subclass must implement all 6 abstract methods
- [ ] `BackendRegistry.register("test", TestBackend)` + `BackendRegistry.get("test")` works
- [ ] `BackendRegistry.get("nonexistent")` raises `KeyError`
- [ ] All tests pass: `pytest tests/test_backend_base.py -v`

---

## Test Specification

```python
import pytest
import uuid
from qw.backends.base import BaseExecutionBackend, BackendRegistry
from qw.backends.models import TaskResult


class TestBaseExecutionBackend:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseExecutionBackend()

    def test_concrete_subclass(self):
        class DummyBackend(BaseExecutionBackend):
            async def dispatch(self, task): return uuid.uuid4()
            async def poll(self, task_id): return "completed"
            async def get_result(self, task_id): return TaskResult(...)
            async def cancel(self, task_id): return True
            async def cleanup(self, task_id): pass
            async def health_check(self): return {"status": "ok"}

        backend = DummyBackend()
        assert isinstance(backend, BaseExecutionBackend)


class TestBackendRegistry:
    def test_register_and_get(self):
        registry = BackendRegistry()
        class MockBackend(BaseExecutionBackend):
            async def dispatch(self, task): return uuid.uuid4()
            async def poll(self, task_id): return "completed"
            async def get_result(self, task_id): return None
            async def cancel(self, task_id): return True
            async def cleanup(self, task_id): pass
            async def health_check(self): return {}
        registry.register("mock", MockBackend)
        assert registry.get("mock") is MockBackend

    def test_get_nonexistent_raises(self):
        registry = BackendRegistry()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_list_backends(self):
        registry = BackendRegistry()
        assert isinstance(registry.list_backends(), list)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-032 is completed (`qw/backends/models.py` exists)
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** `qw/backends/base.py`
5. **Update** `qw/backends/__init__.py` exports
6. **Run tests**: `pytest tests/test_backend_base.py -v`
7. **Verify** all acceptance criteria
8. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

*(Agent fills this in when done)*
