# TASK-027: QWorker Heartbeat Loop & Draining Check

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-024, TASK-025
**Assigned-to**: unassigned

---

## Context

> Implements Module 4 from the spec. Adds the heartbeat background task to
> QWorker that periodically writes timestamps to shared state, and the
> draining check in `connection_handler()` and `handle_queue_wrapper()` that
> rejects new queued tasks when the worker is marked as draining.

---

## Scope

- Add `_heartbeat_loop()` async method to `QWorker`:
  - Runs as an asyncio task, writing heartbeat via `self._state.update_heartbeat()`
    every `WORKER_HEARTBEAT_INTERVAL` seconds
  - Handles `asyncio.CancelledError` for clean shutdown
- Add `_is_draining()` sync method to `QWorker`:
  - Reads `status` from shared state via `self._state.get_status()`
  - Returns `True` if status is `"draining"`
  - Handles missing state tracker gracefully (returns `False`)
- Modify `QWorker.start()`:
  - Create and store `self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())`
- Modify `QWorker.shutdown()`:
  - Cancel `self._heartbeat_task`
- Modify `QWorker.connection_handler()`:
  - Before `self.queue.put(task, id=task_uuid)` (line 771), check `_is_draining()`.
    If draining, return error via `closing_writer` so client retries on another worker.
- Modify `QWorker.handle_queue_wrapper()`:
  - Before `self.queue.put(task, id=task.id)` (line 679), check `_is_draining()`.
    If draining, return error via `closing_writer`.
- Add new config imports to `qw/server.py`

**NOT in scope**:
- The supervisor that reads heartbeats and sets draining (TASK-028)
- The task ledger write/remove (TASK-026)
- The HealthServer draining report (TASK-030)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/server.py` | MODIFY | Add `_heartbeat_loop()`, `_is_draining()`, modify `start()`, `shutdown()`, `connection_handler()`, `handle_queue_wrapper()` |
| `tests/test_heartbeat.py` | CREATE | Unit tests for heartbeat and draining behavior |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.server import QWorker                   # verified: qw/server.py:49
from qw.state import StateTracker               # verified: qw/state.py:13 (imported at qw/server.py:38)
from qw.conf import (
    WORKER_HEALTH_PORT,                         # verified: qw/conf.py:44
    # NEW (from TASK-024):
    WORKER_HEARTBEAT_INTERVAL,                  # to be added in TASK-024
)
from qw.exceptions import QWException           # verified: qw/exceptions.py:15
import asyncio                                  # stdlib, already imported: qw/server.py:5
import cloudpickle                              # dependency, already imported: qw/server.py:15
```

### Existing Signatures to Use
```python
# qw/server.py:49
class QWorker:
    def __init__(self, ..., shared_state=None):              # line 60
        self._running: bool = True                           # line 81
        self._state: Optional[StateTracker]                  # line 90
        self._loop: asyncio.AbstractEventLoop                # line 85
        self.queue: QueueManager                             # set in start(), line 330

    async def start(self):                                   # line 326
        # QueueManager created at line 330
        # subscription_task created at line 332
        # fire_consumers at line 381
        # serve_forever at line 383

    async def shutdown(self):                                # line 387
        self._running = False                                # line 388

    async def connection_handler(self, reader, writer):      # line 719
        # Queue put at line 771:
        #   await self.queue.put(task, id=task_uuid)

    async def handle_queue_wrapper(self, task, uid, writer): # line 666
        # Queue put at line 679:
        #   await self.queue.put(task, id=task.id)

    async def closing_writer(self, writer, result): ...      # line 791

# qw/state.py (after TASK-025):
class StateTracker:
    def update_heartbeat(self) -> None: ...
    def get_status(self) -> str: ...  # returns "healthy" or "draining"
```

### Does NOT Exist
- ~~`QWorker._heartbeat_loop()`~~ — must be created
- ~~`QWorker._heartbeat_task`~~ — attribute must be created
- ~~`QWorker._is_draining()`~~ — must be created
- ~~`WORKER_HEARTBEAT_INTERVAL`~~ — exists only after TASK-024

---

## Implementation Notes

### Pattern to Follow
```python
# New method in QWorker:
async def _heartbeat_loop(self):
    """Periodically write heartbeat timestamp to shared state."""
    while self._running:
        try:
            if self._state is not None:
                self._state.update_heartbeat()
            await asyncio.sleep(WORKER_HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception:
            self.logger.exception("heartbeat error")

def _is_draining(self) -> bool:
    """Check if this worker has been marked as draining by the supervisor."""
    if self._state is None:
        return False
    try:
        return self._state.get_status() == "draining"
    except Exception:
        return False

# In start(), after fire_consumers() (line 381):
self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

# In shutdown(), before closing redis:
if hasattr(self, '_heartbeat_task'):
    self._heartbeat_task.cancel()
    try:
        await self._heartbeat_task
    except asyncio.CancelledError:
        pass

# In connection_handler(), before line 771 (self.queue.put):
if self._is_draining():
    result = cloudpickle.dumps(
        QWException("Worker is draining, retry on another worker")
    )
    await self.closing_writer(writer, result)
    return False

# In handle_queue_wrapper(), before line 679 (self.queue.put):
if self._is_draining():
    result = cloudpickle.dumps(
        QWException(f"Worker {self.name} draining, retry on another worker")
    )
    await self.closing_writer(writer, result)
    return False
```

### Key Constraints
- `_heartbeat_loop` is an asyncio task, not a thread — consistent with all
  other background tasks in QWorker
- `_is_draining()` is sync (no await) because it's called from the hot path
  and `Manager().dict()` access is synchronous
- The draining rejection must return a serialized `QWException` so the client
  receives a proper error, not a broken pipe
- Import `WORKER_HEARTBEAT_INTERVAL` from `qw.conf` in the server's import block

---

## Acceptance Criteria

- [ ] `_heartbeat_loop()` updates shared state heartbeat every N seconds
- [ ] `_is_draining()` returns `True` when status is "draining", `False` otherwise
- [ ] `_is_draining()` returns `False` when `_state` is None
- [ ] `connection_handler()` rejects queued tasks when draining
- [ ] `handle_queue_wrapper()` rejects queued tasks when draining
- [ ] Heartbeat task is started in `start()` and cancelled in `shutdown()`
- [ ] All existing tests still pass: `pytest tests/ -v`
- [ ] New tests pass: `pytest tests/test_heartbeat.py -v`

---

## Test Specification

```python
# tests/test_heartbeat.py
import asyncio
import time
import multiprocessing as mp
import pytest
from qw.state import StateTracker


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


class TestIsDraining:
    def test_not_draining_by_default(self, shared):
        """_is_draining() returns False for a healthy worker."""
        st = StateTracker(shared, "W0", 1234)
        assert st.get_status() == "healthy"

    def test_draining_when_set(self, shared):
        """_is_draining() returns True after supervisor sets draining."""
        st = StateTracker(shared, "W0", 1234)
        st.set_status("draining", draining_since=time.time())
        assert st.get_status() == "draining"

    def test_no_state_tracker_returns_false(self):
        """_is_draining() returns False when state tracker is None."""
        # This tests the QWorker method indirectly
        pass  # Covered by QWorker unit test with shared_state=None


class TestHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_heartbeat_writes_timestamp(self, shared):
        """Heartbeat loop updates the timestamp in shared state."""
        st = StateTracker(shared, "W0", 1234)
        assert st.get_heartbeat() == 0.0
        st.update_heartbeat()
        assert st.get_heartbeat() > 0
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify TASK-024 and TASK-025 are in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm `qw/server.py` line numbers still match
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** following the scope and pattern above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-027-qworker-heartbeat-draining.md`
8. **Update index** status to `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
