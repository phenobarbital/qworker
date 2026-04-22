# TASK-025: StateTracker Heartbeat, Status & Task Ledger Extensions

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-024
**Assigned-to**: unassigned

---

## Context

> Implements Module 2 from the spec. Extends the existing `StateTracker` class
> with heartbeat, status, draining_since, and task_ledger fields in the shared
> state schema, plus methods for heartbeat updates and task ledger CRUD.
> This is the data layer that the heartbeat loop (TASK-027), queue manager
> (TASK-026), and supervisor (TASK-028) all depend on.

---

## Scope

- Extend `StateTracker.__init__()` to initialize the 4 new fields in the
  shared_state entry: `heartbeat`, `status`, `draining_since`, `task_ledger`
- Add 6 new methods to `StateTracker`:
  - `update_heartbeat()` — write `time.time()` to `shared_state[worker_name]["heartbeat"]`
  - `set_status(status: str, draining_since: float | None = None)` — set worker status
  - `get_heartbeat() -> float` — read heartbeat timestamp for this worker
  - `get_status() -> str` — read status for this worker
  - `ledger_add(task_id: str, payload: str)` — append a serialized task entry to the ledger
  - `ledger_remove(task_id: str)` — remove a completed task entry from the ledger
  - `ledger_drain() -> list[dict]` — read and clear the entire ledger (used by supervisor)
- Write unit tests for all new methods

**NOT in scope**:
- Calling these methods from QWorker or QueueManager (TASK-026, TASK-027)
- The supervisor logic that reads heartbeats (TASK-028)
- Configuration constants (TASK-024)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/state.py` | MODIFY | Add new fields to `__init__`, add 6 new methods |
| `tests/test_state_tracker.py` | MODIFY | Add tests for new heartbeat/status/ledger methods |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.state import StateTracker           # verified: qw/state.py:13
import time                                 # stdlib, already imported: qw/state.py:10
import multiprocessing as mp                # stdlib, for test fixtures
```

### Existing Signatures to Use
```python
# qw/state.py:13
class StateTracker:
    MAX_COMPLETED: int = 10                                         # line 25

    def __init__(self, shared_state: dict, worker_name: str, pid: int):  # line 27
        self._state = shared_state                                  # line 28
        self._worker_name = worker_name                             # line 29
        self._pid = pid                                             # line 30
        # Current init dict (lines 32-39):
        self._state[worker_name] = {
            "pid": pid,
            "queue": [],
            "tcp_executing": [],
            "redis_executing": [],
            "broker_executing": [],
            "completed": []
        }

    def task_queued(self, task_id: str, function_name: str) -> None: ...   # line 98
    def task_executing(self, task_id: str, source: str) -> None: ...       # line 122
    def task_completed(self, task_id: str, result: str, source: str) -> None: ... # line 167
    def get_state(self) -> dict: ...                                       # line 220
    def get_all_states(self) -> dict: ...                                  # line 226
```

### Does NOT Exist
- ~~`StateTracker.update_heartbeat()`~~ — must be created
- ~~`StateTracker.set_status()`~~ — must be created
- ~~`StateTracker.get_heartbeat()`~~ — must be created
- ~~`StateTracker.get_status()`~~ — must be created
- ~~`StateTracker.ledger_add()`~~ — must be created
- ~~`StateTracker.ledger_remove()`~~ — must be created
- ~~`StateTracker.ledger_drain()`~~ — must be created
- ~~`shared_state[name]["heartbeat"]`~~ — not in current init dict
- ~~`shared_state[name]["status"]`~~ — not in current init dict
- ~~`shared_state[name]["draining_since"]`~~ — not in current init dict
- ~~`shared_state[name]["task_ledger"]`~~ — not in current init dict

---

## Implementation Notes

### Pattern to Follow
```python
# CRITICAL: Manager proxies do NOT detect nested mutations (qw/state.py:6-8).
# Every method MUST use full-value replacement:
def update_heartbeat(self) -> None:
    d = dict(self._state[self._worker_name])
    d["heartbeat"] = time.time()
    self._state[self._worker_name] = d

def set_status(self, status: str, draining_since: float | None = None) -> None:
    d = dict(self._state[self._worker_name])
    d["status"] = status
    d["draining_since"] = draining_since
    self._state[self._worker_name] = d

def get_heartbeat(self) -> float:
    return dict(self._state[self._worker_name]).get("heartbeat", 0.0)

def get_status(self) -> str:
    return dict(self._state[self._worker_name]).get("status", "healthy")

def ledger_add(self, task_id: str, payload: str) -> None:
    d = dict(self._state[self._worker_name])
    d["task_ledger"] = list(d.get("task_ledger", [])) + [{
        "task_id": task_id,
        "payload": payload,
        "enqueued_at": time.time(),
    }]
    self._state[self._worker_name] = d

def ledger_remove(self, task_id: str) -> None:
    d = dict(self._state[self._worker_name])
    d["task_ledger"] = [
        e for e in list(d.get("task_ledger", []))
        if e["task_id"] != task_id
    ]
    self._state[self._worker_name] = d

def ledger_drain(self) -> list[dict]:
    d = dict(self._state[self._worker_name])
    entries = list(d.get("task_ledger", []))
    d["task_ledger"] = []
    self._state[self._worker_name] = d
    return entries
```

### Key Constraints
- Extend the existing `__init__` dict with the 4 new keys — do NOT remove existing keys
- All methods must follow the atomic full-value replacement pattern
- `set_status` is called by the supervisor (different process), not the worker itself
- `get_heartbeat` and `get_status` must handle missing keys gracefully (defaults)

### References in Codebase
- `qw/state.py` — all existing methods follow the same Manager proxy pattern
- `tests/test_state_tracker.py` — existing test patterns to extend

---

## Acceptance Criteria

- [ ] `StateTracker.__init__` initializes `heartbeat`, `status`, `draining_since`, `task_ledger`
- [ ] `update_heartbeat()` writes current timestamp to shared state
- [ ] `set_status("draining", time.time())` sets both fields atomically
- [ ] `get_heartbeat()` returns the timestamp (or 0.0 if missing)
- [ ] `get_status()` returns the status string (or "healthy" if missing)
- [ ] `ledger_add()` + `ledger_remove()` maintain correct list entries
- [ ] `ledger_drain()` returns all entries and clears the ledger
- [ ] All existing tests still pass: `pytest tests/test_state_tracker.py -v`
- [ ] New tests pass: `pytest tests/test_state_tracker.py -v`

---

## Test Specification

```python
# tests/test_state_tracker.py (extend existing file)
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


class TestHeartbeat:
    def test_initial_heartbeat_is_zero(self, shared):
        st = StateTracker(shared, "W0", 1234)
        assert st.get_heartbeat() == 0.0

    def test_update_heartbeat(self, shared):
        st = StateTracker(shared, "W0", 1234)
        before = time.time()
        st.update_heartbeat()
        after = time.time()
        hb = st.get_heartbeat()
        assert before <= hb <= after


class TestStatus:
    def test_initial_status_is_healthy(self, shared):
        st = StateTracker(shared, "W0", 1234)
        assert st.get_status() == "healthy"

    def test_set_status_draining(self, shared):
        st = StateTracker(shared, "W0", 1234)
        now = time.time()
        st.set_status("draining", draining_since=now)
        assert st.get_status() == "draining"
        d = dict(shared["W0"])
        assert d["draining_since"] == now

    def test_set_status_back_to_healthy(self, shared):
        st = StateTracker(shared, "W0", 1234)
        st.set_status("draining", draining_since=time.time())
        st.set_status("healthy")
        assert st.get_status() == "healthy"
        d = dict(shared["W0"])
        assert d["draining_since"] is None


class TestLedger:
    def test_ledger_add(self, shared):
        st = StateTracker(shared, "W0", 1234)
        st.ledger_add("id-1", "payload-1")
        d = dict(shared["W0"])
        assert len(d["task_ledger"]) == 1
        assert d["task_ledger"][0]["task_id"] == "id-1"

    def test_ledger_remove(self, shared):
        st = StateTracker(shared, "W0", 1234)
        st.ledger_add("id-1", "payload-1")
        st.ledger_add("id-2", "payload-2")
        st.ledger_remove("id-1")
        d = dict(shared["W0"])
        assert len(d["task_ledger"]) == 1
        assert d["task_ledger"][0]["task_id"] == "id-2"

    def test_ledger_drain(self, shared):
        st = StateTracker(shared, "W0", 1234)
        st.ledger_add("id-1", "payload-1")
        st.ledger_add("id-2", "payload-2")
        entries = st.ledger_drain()
        assert len(entries) == 2
        d = dict(shared["W0"])
        assert len(d["task_ledger"]) == 0
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify TASK-024 is in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm `qw/state.py` signatures still match
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** following the scope and pattern above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-025-state-tracker-extensions.md`
8. **Update index** status to `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
