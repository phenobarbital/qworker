# TASK-026: QueueManager Ledger Integration & Draining Guard

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-025
**Assigned-to**: unassigned

---

## Context

> Implements Module 3 from the spec. Integrates the task ledger into
> QueueManager so that every queued task has a serialized shadow copy in
> shared state, and tasks are removed from the ledger when dequeued.
> Also adds a draining guard to `put()` that rejects new tasks when the
> worker is marked as draining.

---

## Scope

- Modify `QueueManager.put()` to:
  1. Check if the worker is draining (via `self._state.get_status()`) before
     attempting to queue. If draining, raise `asyncio.QueueFull` with a
     descriptive message.
  2. After successful `put_nowait()`, serialize the task with `cloudpickle` +
     `base64` and call `self._state.ledger_add(task_id, payload)`.
- Modify `QueueManager.queue_handler()` to:
  1. After `task = await self.queue.get()` (line 354), immediately call
     `self._state.ledger_remove(str(task.id))` to remove the task from the
     ledger at dequeue time (not completion time — per spec decision).
- Write unit tests for ledger write/remove and draining guard behavior.

**NOT in scope**:
- The heartbeat loop (TASK-027)
- The draining flag being SET by the supervisor (TASK-028)
- The StateTracker methods themselves (TASK-025)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/manager.py` | MODIFY | Add ledger write in `put()`, ledger remove in `queue_handler()`, draining guard |
| `tests/test_queue_manager_ledger.py` | CREATE | Unit tests for ledger integration and draining guard |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.queues.manager import QueueManager   # verified: qw/queues/manager.py:33
from qw.state import StateTracker             # verified: qw/state.py:13
from qw.wrappers.base import QueueWrapper     # verified: qw/wrappers/base.py:10
import cloudpickle                            # dependency, used in qw/server.py:15
import base64                                 # stdlib
import asyncio                                # stdlib, already imported: qw/queues/manager.py:1
```

### Existing Signatures to Use
```python
# qw/queues/manager.py:33
class QueueManager:
    def __init__(self, worker_name: str, state_tracker=None, policy=None):  # line 36
        self.queue: asyncio.Queue               # line 47
        self._state = state_tracker             # line 62
        self.worker_name: str                   # line 43

    async def put(self, task: QueueWrapper, id: str) -> bool:  # line 190
        # Key locations:
        # put_nowait: line 219
        # State tracking (task_queued): lines 242-244
        # Cooldown reset: lines 247-249

    async def queue_handler(self):              # line 350
        # task = await self.queue.get(): line 354
        # state.task_executing: line 359
        # self.queue.task_done(): line 413
        # state.task_completed: lines 420-422

# qw/state.py (after TASK-025):
class StateTracker:
    def get_status(self) -> str: ...             # returns "healthy" or "draining"
    def ledger_add(self, task_id: str, payload: str) -> None: ...
    def ledger_remove(self, task_id: str) -> None: ...
```

### Does NOT Exist
- ~~`QueueManager._is_draining()`~~ — draining check goes directly in `put()` via `self._state.get_status()`
- ~~`QueueManager.ledger`~~ — no dedicated attribute; uses StateTracker methods
- ~~`self._state.ledger_add_task()`~~ — wrong method name; correct is `ledger_add()`

---

## Implementation Notes

### Pattern to Follow
```python
# In put(), BEFORE the existing put_nowait logic (line 218):
if self._state and self._state.get_status() == "draining":
    self.logger.warning(
        f"Worker {self.worker_name} is draining — rejecting task {id}"
    )
    raise asyncio.queues.QueueFull(
        f"Worker {self.worker_name} is draining"
    )

# In put(), AFTER successful put_nowait (after line 219 or 224):
if self._state:
    try:
        serialized = base64.b64encode(cloudpickle.dumps(task)).decode()
        self._state.ledger_add(str(task.id), serialized)
    except Exception as ledger_err:
        self.logger.error("Ledger write failed for %s: %s", task.id, ledger_err)

# In queue_handler(), AFTER task = await self.queue.get() (line 354):
if self._state:
    self._state.ledger_remove(str(task.id))
```

### Key Constraints
- The draining guard must be checked BEFORE any queue size logic
- `cloudpickle.dumps(task)` is the same serialization used by the Redis stream
  path (`qw/server.py:239`), so the payload format is compatible
- Ledger remove happens at DEQUEUE time (when `queue.get()` returns), not at
  completion time — this is an explicit spec decision to minimize duplicate execution
- Ledger operations should be wrapped in try/except — a ledger failure must
  NOT prevent the task from being queued/executed
- The `import cloudpickle` and `import base64` must be added to the top of
  `qw/queues/manager.py`

---

## Acceptance Criteria

- [ ] `put()` rejects tasks with `QueueFull` when worker status is "draining"
- [ ] `put()` writes a serialized task entry to the ledger after successful queue
- [ ] `queue_handler()` removes the task from the ledger immediately after dequeue
- [ ] Ledger failures do not prevent task execution
- [ ] All existing tests still pass: `pytest tests/ -v`
- [ ] New tests pass: `pytest tests/test_queue_manager_ledger.py -v`

---

## Test Specification

```python
# tests/test_queue_manager_ledger.py
import asyncio
import base64
import multiprocessing as mp
import pytest
import cloudpickle
from unittest.mock import MagicMock, AsyncMock
from qw.state import StateTracker


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


class TestDrainingGuard:
    @pytest.mark.asyncio
    async def test_put_rejects_when_draining(self, shared):
        """put() raises QueueFull when worker is marked draining."""
        st = StateTracker(shared, "W0", 1234)
        st.set_status("draining", draining_since=1000.0)
        from qw.queues.manager import QueueManager
        qm = QueueManager(worker_name="W0", state_tracker=st)
        task = MagicMock()
        task.id = "test-id"
        with pytest.raises(asyncio.QueueFull, match="draining"):
            await qm.put(task, id="test-id")

    @pytest.mark.asyncio
    async def test_put_accepts_when_healthy(self, shared):
        """put() succeeds when worker is healthy."""
        st = StateTracker(shared, "W0", 1234)
        from qw.queues.manager import QueueManager
        qm = QueueManager(worker_name="W0", state_tracker=st)
        task = MagicMock()
        task.id = "test-id"
        result = await qm.put(task, id="test-id")
        assert result is True


class TestLedgerIntegration:
    @pytest.mark.asyncio
    async def test_put_writes_ledger(self, shared):
        """After put(), task appears in the ledger."""
        st = StateTracker(shared, "W0", 1234)
        from qw.queues.manager import QueueManager
        qm = QueueManager(worker_name="W0", state_tracker=st)
        task = MagicMock()
        task.id = "test-id"
        await qm.put(task, id="test-id")
        d = dict(shared["W0"])
        assert len(d["task_ledger"]) == 1
        assert d["task_ledger"][0]["task_id"] == "test-id"
        # Verify payload is deserializable
        payload = base64.b64decode(d["task_ledger"][0]["payload"])
        restored = cloudpickle.loads(payload)
        assert restored is not None
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify TASK-024 and TASK-025 are in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm `qw/queues/manager.py` signatures still match
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** following the scope, pattern, and constraints above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-026-queue-manager-ledger.md`
8. **Update index** status to `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
