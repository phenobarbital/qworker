# TASK-010: SpawnProcess Manager Integration

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-009
**Assigned-to**: unassigned

---

## Context

> SpawnProcess is the parent orchestrator that spawns all worker processes. This task adds
> a `multiprocessing.Manager` to it and passes the shared state dict to each worker process
> via the `start_server()` function signature.
> Implements: Spec Module 2 (SpawnProcess Manager Integration).

---

## Scope

- Modify `qw/process.py`:
  - In `SpawnProcess.__init__()`, create a `multiprocessing.Manager()` instance and a shared
    `DictProxy` (stored as `self._manager` and `self._shared_state`)
  - Pass `self._shared_state` as a new 6th argument to `start_server()` in the worker spawn
    loop (line 95, the `args=` tuple)
  - In `terminate()`, call `self._manager.shutdown()` during cleanup
- Modify `qw/server.py`:
  - Update `start_server()` signature (line 740) to accept `shared_state` as 6th parameter
  - Pass `shared_state` to `QWorker.__init__()` as a new keyword argument
  - Update `QWorker.__init__()` (line 62) to accept and store `shared_state` parameter
    (stored as `self._shared_state`). Do NOT create a StateTracker here yet — that happens
    in TASK-011

**NOT in scope**:
- Creating StateTracker instances in QWorker (TASK-011)
- Instrumenting task lifecycle events (TASK-011, TASK-012)
- QueueManager changes (TASK-012)
- CLI changes (TASK-014)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/process.py` | MODIFY | Add Manager creation, pass shared_state to start_server |
| `qw/server.py` | MODIFY | Update start_server signature, QWorker.__init__ to accept shared_state |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import multiprocessing as mp  # qw/process.py:3 — already imported
# Manager is accessed as mp.Manager() — no additional import needed
```

### Existing Signatures to Use
```python
# qw/process.py:61
class SpawnProcess:
    def __init__(self, args):  # line 62
        # Worker spawn loop at lines 90-103:
        # p = mp.Process(
        #     target=start_server,
        #     name=f'{self.worker}_{i}',
        #     args=(i, args.host, args.port, args.debug, args.notify_empty, )
        # )
        # CHANGE: args tuple gets shared_state as 6th element

    def terminate(self):  # line 259
        # CHANGE: add self._manager.shutdown() here

# qw/server.py:740
def start_server(num_worker, host, port, debug: bool, notify_empty: bool):
    # CHANGE: add shared_state parameter
    # Currently creates QWorker at line 752:
    # worker = QWorker(
    #     host=host, port=port, event_loop=loop, debug=debug,
    #     worker_id=num_worker, notify_empty_stream=notify_empty
    # )
    # CHANGE: pass shared_state=shared_state to QWorker

# qw/server.py:62
class QWorker:
    def __init__(self, host, port, worker_id, name, event_loop, debug,
                 protocol, notify_empty_stream, empty_stream_minutes):  # line 62-73
        # CHANGE: add shared_state=None parameter
        # Store as self._shared_state = shared_state
```

### Does NOT Exist
- ~~`SpawnProcess.manager`~~ — does not exist yet; this task creates `self._manager`
- ~~`SpawnProcess._shared_state`~~ — does not exist yet; this task creates it
- ~~`QWorker._shared_state`~~ — does not exist yet; this task creates it
- ~~`start_server` shared_state parameter~~ — does not exist yet; this task adds it

---

## Implementation Notes

### Pattern to Follow
```python
# In SpawnProcess.__init__, BEFORE the worker spawn loop:
self._manager = mp.Manager()
self._shared_state = self._manager.dict()

# In the worker spawn loop, update the args tuple:
args=(i, args.host, args.port, args.debug, args.notify_empty, self._shared_state)

# In terminate(), add before stopping redis:
try:
    self._manager.shutdown()
except Exception:
    pass
```

### Key Constraints
- `mp.Manager()` MUST be created before `mp.Process()` calls — the shared dict must exist
  before workers start
- The Manager internally spawns a server process — this is expected and adds one process
  to the total count
- `start_server()` signature change is backward-incompatible within this codebase, but
  that's acceptable since it's only called from `SpawnProcess.__init__()`
- `QWorker.__init__()` should default `shared_state=None` for backward compatibility
  with any external usage

### References in Codebase
- `qw/process.py:90-103` — worker spawn loop (modify args tuple)
- `qw/process.py:259-284` — terminate() method (add manager shutdown)
- `qw/server.py:740-779` — start_server() function (add parameter, pass to QWorker)
- `qw/server.py:62-90` — QWorker.__init__() (accept and store shared_state)

---

## Acceptance Criteria

- [ ] `SpawnProcess.__init__()` creates `self._manager` and `self._shared_state`
- [ ] `self._shared_state` is passed to `start_server()` as 6th argument
- [ ] `start_server()` accepts and forwards `shared_state` to `QWorker`
- [ ] `QWorker.__init__()` accepts `shared_state=None` and stores as `self._shared_state`
- [ ] `SpawnProcess.terminate()` calls `self._manager.shutdown()`
- [ ] Existing functionality (worker spawning, TCP connections) is unaffected
- [ ] `qw` can still start without errors (manual smoke test)

---

## Test Specification

No new test file for this task — the changes are structural (wiring). Verification is via
smoke-testing that qworker starts and the existing `health`/`check_state` commands still work.
Integration tests in TASK-015 will verify end-to-end.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies** — verify TASK-009 is completed (qw/state.py exists)
3. **Verify the Codebase Contract**:
   - Read `qw/process.py` lines 62-103 and 259-284 — confirm SpawnProcess structure
   - Read `qw/server.py` lines 740-779 — confirm start_server signature
   - Read `qw/server.py` lines 62-90 — confirm QWorker.__init__ signature
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement** the changes to `qw/process.py` and `qw/server.py`
6. **Verify** qworker can still start (no import errors)
7. **Move this file** to `tasks/completed/TASK-010-spawnprocess-manager.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none | describe if any
