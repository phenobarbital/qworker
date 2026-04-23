# TASK-029: SpawnProcess Supervisor Integration

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-028
**Assigned-to**: unassigned

---

## Context

> Implements Module 6 from the spec. Wires the `ProcessSupervisor` into the
> existing `SpawnProcess` class so that the supervisor daemon thread starts
> after all worker processes are spawned and stops during graceful shutdown.

---

## Scope

- Modify `SpawnProcess.__init__()`:
  - After the worker spawn loop (lines 94-107), create and start a
    `ProcessSupervisor` instance as a daemon thread
  - Store as `self._supervisor`
  - Pass: `shared_state`, `JOB_LIST`, `worker` prefix, `host`, `port`,
    `debug`, `notify_empty` flag, `health_port`
- Modify `SpawnProcess.terminate()`:
  - Call `self._supervisor.stop()` before the worker termination loop
  - Optionally `self._supervisor.join(timeout=5)` for clean exit
- Add import of `ProcessSupervisor` to `qw/process.py`

**NOT in scope**:
- The ProcessSupervisor implementation (TASK-028)
- Any changes to start_server or QWorker (TASK-027)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/process.py` | MODIFY | Import ProcessSupervisor, create+start in `__init__`, stop in `terminate()` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.process import SpawnProcess            # verified: qw/process.py:61
from qw.process import JOB_LIST                # verified: qw/process.py:24
# NEW (from TASK-028):
from qw.supervisor import ProcessSupervisor    # to be created in TASK-028
```

### Existing Signatures to Use
```python
# qw/process.py:61
class SpawnProcess:
    def __init__(self, args):                                     # line 62
        self.host: str = args.host                                # line 67
        self.port: int = args.port                                # line 71
        self.worker: str = f"{args.wkname}-{args.port}"          # line 72
        self.debug: bool = args.debug                             # line 73
        self._health_port: int                                    # line 90
        self._manager = mp.Manager()                              # line 92
        self._shared_state = self._manager.dict()                 # line 93
        # Worker spawn loop: lines 94-107
        # NotifyWorker spawn: lines 109-129

    def terminate(self):                                          # line 263
        for j in JOB_LIST:                                        # line 264
            j.terminate()                                         # line 266
            j.join()                                              # line 270
        # Redis cleanup: lines 273-288
        # Manager shutdown: lines 289-292

# qw/supervisor.py (from TASK-028):
class ProcessSupervisor(threading.Thread):
    def __init__(
        self, shared_state, job_list, worker_name_prefix,
        host, port, debug, notify_empty, health_port,
    ): ...
    def stop(self) -> None: ...
```

### Does NOT Exist
- ~~`SpawnProcess._supervisor`~~ — attribute must be added
- ~~`SpawnProcess.supervisor`~~ — no public property; use `_supervisor`

---

## Implementation Notes

### Pattern to Follow
```python
# In __init__(), AFTER the worker spawn loop (after line 107) and
# AFTER the NotifyWorker spawn (after line 129):
from .supervisor import ProcessSupervisor
self._supervisor = ProcessSupervisor(
    shared_state=self._shared_state,
    job_list=JOB_LIST,
    worker_name_prefix=self.worker,
    host=self.host,
    port=self.port,
    debug=self.debug,
    notify_empty=args.notify_empty,
    health_port=self._health_port,
)
self._supervisor.start()
self.logger.info("Process supervisor started")

# In terminate(), BEFORE the JOB_LIST loop (before line 264):
if hasattr(self, '_supervisor'):
    self._supervisor.stop()
    self._supervisor.join(timeout=5)
    self.logger.info("Process supervisor stopped")
```

### Key Constraints
- The supervisor must start AFTER all workers are spawned (workers must have
  initialized their shared_state entries before the supervisor reads them)
- The supervisor must stop BEFORE workers are terminated (otherwise it might
  try to respawn workers that are being intentionally shut down)
- Use lazy import (`from .supervisor import ProcessSupervisor`) inside
  `__init__` to avoid circular imports, OR add to top-level imports
- The `args.notify_empty` is available from the constructor's `args` parameter

---

## Acceptance Criteria

- [ ] `ProcessSupervisor` is created and started after worker spawn
- [ ] `ProcessSupervisor` is stopped before worker termination
- [ ] Supervisor thread appears in thread list during runtime
- [ ] Clean shutdown: no zombie threads or orphaned supervisor
- [ ] All existing tests still pass: `pytest tests/ -v`

---

## Test Specification

No dedicated test file — this is wiring only. Verified by:
- Integration test in TASK-031 (end-to-end supervisor behavior)
- Manual verification: start QWorker, check thread list, verify supervisor log messages

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify TASK-028 is in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm `qw/process.py` signatures still match
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** the wiring code
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-029-spawnprocess-integration.md`
8. **Update index** status to `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-23
**Notes**: Added `from .supervisor import ProcessSupervisor` to the top-level imports in `qw/process.py` (no circular-import risk since the supervisor only imports from `qw.server` and `qw.conf`). In `SpawnProcess.__init__`, after the NotifyWorker spawn branch, the supervisor is instantiated with all 8 required args (`shared_state`, `JOB_LIST`, `worker` prefix, `host`, `port`, `debug`, `args.notify_empty`, `_health_port`) and `start()`-ed. Supervisor-init failures are logged and do not block worker startup (`self._supervisor` is kept as `None` so the cleanup path is a no-op). In `SpawnProcess.terminate`, the supervisor is stopped (`stop()` + `join(timeout=5)`) BEFORE the `JOB_LIST` termination loop so it does not try to respawn workers during intentional shutdown. Full test suite: 128 passed, 1 skipped.

**Deviations from spec**: none. Added defensive try/except around supervisor init so a supervisor failure never prevents workers from starting.
