# TASK-009: State Tracker Module

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> This is the foundational module for FEAT-002. It provides the `StateTracker` class that
> encapsulates all reads/writes to the `multiprocessing.Manager` shared dict. Every other
> task in this feature depends on this module existing.
> Implements: Spec Module 1 (State Tracker).

---

## Scope

- Create `qw/state.py` with a `StateTracker` class
- Implement `task_queued(task_id, function_name)` — adds a task entry to the appropriate
  source list with status "queued" and `enqueued_at` timestamp
- Implement `task_executing(task_id, source)` — transitions a task to "executing" status
  with `started_at` timestamp. `source` is one of: "queue", "tcp", "redis", "broker"
- Implement `task_completed(task_id, result, source)` — moves a task from active lists to
  the completed ring buffer (max 10 entries, oldest evicted). Records `completed_at`,
  `duration`, and result ("success" or "error")
- Implement `get_state()` — returns the current worker's state as a plain dict
- Implement `_get_function_name(task)` — extracts function name from different wrapper types:
  `FuncWrapper` → `task.func.__name__`, `TaskWrapper` → `repr(task)`,
  `QueueWrapper` → `repr(task)`, fallback → `"<unknown>"`
- Handle Manager proxy nested mutation correctly: always reassign full values to shared dict
  keys (proxies don't detect in-place mutations of nested structures)
- Write unit tests in `tests/test_state_tracker.py`

**NOT in scope**:
- Integration with QWorker, QueueManager, or SpawnProcess (those are TASK-010 through TASK-013)
- CLI output formatting (TASK-014)
- TCP `info` command (TASK-011)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/state.py` | CREATE | StateTracker class |
| `tests/test_state_tracker.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# These imports are confirmed to work and may be needed:
from qw.wrappers import QueueWrapper, FuncWrapper, TaskWrapper  # qw/server.py:43-44
from qw.wrappers.base import QueueWrapper  # qw/queues/manager.py:28
from qw.wrappers.func import FuncWrapper   # qw/wrappers/__init__.py
```

### Existing Signatures to Use
```python
# qw/wrappers/base.py:10
class QueueWrapper:
    _id: uuid.UUID              # line 17
    retries: int = 0            # line 25
    coro                        # line 27
    @property
    def id(self) -> uuid.UUID:  # line 58
    # NOTE: QueueWrapper has NO __repr__ or __str__ — defaults to object repr

# qw/wrappers/func.py:7
class FuncWrapper(QueueWrapper):
    func: Callable              # line 13
    host: str                   # line 12
    def __repr__(self) -> str:  # line 29 — returns '<%s> from %s' % (func.__name__, host)
    def retry(self):            # line 35
    # FuncWrapper HAS func.__name__ available

# qw/wrappers/di_task.py (TaskWrapper)
class TaskWrapper(QueueWrapper):
    # Has __repr__ — check actual implementation before using
    def retry(self):            # line 115
```

### Does NOT Exist
- ~~`QueueWrapper.function_name`~~ — no such property; use repr(task) or task.func.__name__ for FuncWrapper
- ~~`QueueWrapper.enqueued_at`~~ — no timestamp tracking; StateTracker records this externally
- ~~`QueueWrapper.started_at`~~ — no such attribute; StateTracker records this externally
- ~~`QueueWrapper.__repr__`~~ — base QueueWrapper has no custom __repr__; only FuncWrapper does
- ~~`qw/state.py`~~ — does not exist yet; this task creates it

---

## Implementation Notes

### Pattern to Follow
```python
# StateTracker wraps a multiprocessing.Manager DictProxy.
# CRITICAL: Manager proxies do NOT detect nested mutations.
# WRONG: shared_state[key]["queue"].append(item)  # change is LOST
# RIGHT: d = dict(shared_state[key]); d["queue"] = [...]; shared_state[key] = d

import time
import os
from collections import deque
from typing import Optional

class StateTracker:
    """Thin wrapper around multiprocessing.Manager shared dict."""

    MAX_COMPLETED = 10

    def __init__(self, shared_state: dict, worker_name: str, pid: int):
        self._state = shared_state
        self._worker_name = worker_name
        self._pid = pid
        # Initialize this worker's entry in the shared dict
        self._state[worker_name] = {
            "pid": pid,
            "queue": [],
            "tcp_executing": [],
            "redis_executing": [],
            "broker_executing": [],
            "completed": []
        }

    def _get_function_name(self, task) -> str:
        """Extract a human-readable function name from a task wrapper."""
        # Check isinstance in order: FuncWrapper first (has .func)
        ...

    def task_queued(self, task_id: str, function_name: str) -> None:
        """Record a task being added to the asyncio queue."""
        ...

    def task_executing(self, task_id: str, source: str) -> None:
        """Transition a task to executing state. Source: queue|tcp|redis|broker."""
        ...

    def task_completed(self, task_id: str, result: str, source: str) -> None:
        """Move a task to the completed ring buffer."""
        ...

    def get_state(self) -> dict:
        """Return this worker's current state as a plain dict."""
        ...
```

### Key Constraints
- All values stored in shared dict must be plain dicts/lists (JSON-serializable, picklable)
- Do NOT use dataclasses or Pydantic models inside the shared dict
- Use `time.time()` for timestamps (float, seconds since epoch)
- The completed list is a plain list, capped at MAX_COMPLETED (remove oldest via slicing)
- Thread-safe: Manager proxies handle locking, but minimize time between read and write

### References in Codebase
- `qw/wrappers/base.py` — QueueWrapper base class (id, retries properties)
- `qw/wrappers/func.py` — FuncWrapper (has func.__name__)
- `qw/wrappers/di_task.py` — TaskWrapper

---

## Acceptance Criteria

- [ ] `qw/state.py` exists with `StateTracker` class
- [ ] `task_queued()` adds entry with task_id, function_name, enqueued_at, retries=0, status="queued"
- [ ] `task_executing()` sets started_at and status="executing" for the correct source list
- [ ] `task_completed()` moves to completed list with duration and result
- [ ] Completed list never exceeds 10 entries (oldest evicted)
- [ ] `_get_function_name()` handles FuncWrapper, TaskWrapper, QueueWrapper, and fallback
- [ ] `get_state()` returns a plain dict copy of the worker's state
- [ ] All tests pass: `pytest tests/test_state_tracker.py -v`
- [ ] No use of nested mutation on Manager proxies (always reassign full values)

---

## Test Specification

```python
# tests/test_state_tracker.py
import pytest
from multiprocessing import Manager
from qw.state import StateTracker


@pytest.fixture
def shared_state():
    manager = Manager()
    state = manager.dict()
    yield state
    manager.shutdown()


@pytest.fixture
def tracker(shared_state):
    return StateTracker(shared_state, worker_name="TestWorker_0", pid=12345)


class TestStateTracker:
    def test_init_creates_worker_entry(self, tracker, shared_state):
        """Worker entry exists in shared state after init."""
        assert "TestWorker_0" in shared_state
        state = shared_state["TestWorker_0"]
        assert state["pid"] == 12345
        assert state["queue"] == []

    def test_task_queued(self, tracker, shared_state):
        """task_queued adds entry with correct fields."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 1
        assert state["queue"][0]["task_id"] == "abc-123"
        assert state["queue"][0]["status"] == "queued"

    def test_task_executing_from_queue(self, tracker, shared_state):
        """task_executing transitions queued task to executing."""
        tracker.task_queued("abc-123", "process_data")
        tracker.task_executing("abc-123", source="queue")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 1
        assert state["queue"][0]["status"] == "executing"

    def test_task_executing_tcp(self, tracker, shared_state):
        """task_executing for TCP source adds to tcp_executing."""
        tracker.task_executing("abc-123", source="tcp")
        state = shared_state["TestWorker_0"]
        assert len(state["tcp_executing"]) == 1

    def test_task_completed_success(self, tracker, shared_state):
        """task_completed moves task to completed list."""
        tracker.task_queued("abc-123", "process_data")
        tracker.task_executing("abc-123", source="queue")
        tracker.task_completed("abc-123", result="success", source="queue")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 0
        assert len(state["completed"]) == 1
        assert state["completed"][0]["result"] == "success"

    def test_completed_ring_buffer(self, tracker, shared_state):
        """Completed list never exceeds 10 entries."""
        for i in range(15):
            tid = f"task-{i}"
            tracker.task_executing(tid, source="tcp")
            tracker.task_completed(tid, result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        assert len(state["completed"]) == 10
        # Oldest should have been evicted
        assert state["completed"][0]["task_id"] == "task-5"

    def test_get_state_returns_dict(self, tracker):
        """get_state returns a plain dict."""
        state = tracker.get_state()
        assert isinstance(state, dict)
        assert "pid" in state
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm `qw/wrappers/base.py` still has QueueWrapper with `_id`, `retries` properties
   - Confirm `qw/wrappers/func.py` still has FuncWrapper with `func` attribute and `__repr__`
   - If anything has changed, update the contract FIRST, then implement
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement** `qw/state.py` and `tests/test_state_tracker.py`
6. **Verify** all acceptance criteria pass
7. **Move this file** to `tasks/completed/TASK-009-state-tracker.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none | describe if any
