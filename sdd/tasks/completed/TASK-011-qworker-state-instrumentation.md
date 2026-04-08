# TASK-011: QWorker State Instrumentation & TCP Info Command

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-009, TASK-010
**Assigned-to**: unassigned

---

## Context

> This is the core instrumentation task. QWorker receives the shared state dict (wired in
> TASK-010) and creates a StateTracker (from TASK-009). It instruments the three execution
> paths it owns (direct TCP, Redis Streams) and adds the `info` TCP socket command.
> Implements: Spec Module 3 (QWorker State Instrumentation).

---

## Scope

- Modify `qw/server.py`:
  - In `QWorker.__init__()`: if `self._shared_state` is not None, create a `StateTracker`
    instance: `self._state = StateTracker(self._shared_state, self._name, self._pid)`
  - In `QWorker.start()` (line 317): pass `self._state` to `QueueManager` constructor
    (QueueManager changes happen in TASK-012, but the parameter must be passed here)
  - **Instrument `connection_handler()` (line 652)** for direct TCP execution:
    - When `task.queued is False` (immediate execution at line 634-650):
      extract function name, call `self._state.task_executing(task_id, source="tcp")`
      before `executor.run()`, and `self._state.task_completed(task_id, result, source="tcp")`
      after
  - **Instrument `start_subscription()` (line 195)** for Redis Streams:
    - After deserializing the task (line 232): call
      `self._state.task_executing(task_id, source="redis")` before `executor.run()` (line 238)
    - After execution (line 242 success or line 247 error): call
      `self._state.task_completed(task_id, result, source="redis")`
  - **Add `worker_info()` method**: reads from `self._state.get_state()`, adds worker
    metadata (name, address, serving), returns JSON via `response_keepalive()`
  - **Extend `signature_validation()` (line 508-518)**: add `elif prefix == b'info':` branch
    that calls `self.worker_info(writer)`. For localhost connections (`127.0.0.1` or `::1`),
    allow the `info` command without signature auth. For remote connections, `info` still
    requires the full signature handshake — so `info` should be handled in a new branch
    that checks peer address
  - Guard all state updates with `if self._state:` to avoid errors when shared_state is None

**NOT in scope**:
- QueueManager instrumentation (TASK-012)
- Broker instrumentation (TASK-013)
- CLI / QClient.info() (TASK-014, TASK-015)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/server.py` | MODIFY | Add StateTracker init, instrument TCP+Redis paths, add info command |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Add to qw/server.py imports:
from qw.state import StateTracker  # CREATED in TASK-009

# Already imported in server.py:
from qw.utils.json import json_encoder  # line 40
from qw.wrappers import QueueWrapper    # line 43-44
from qw.executor import TaskExecutor    # line 46
import time                             # line 2
import os                               # line 1 (implicitly, for os.getpid)
```

### Existing Signatures to Use
```python
# qw/server.py:62 — QWorker.__init__ (after TASK-010 adds shared_state param)
class QWorker:
    def __init__(self, host, port, worker_id, name, event_loop, debug,
                 protocol, notify_empty_stream, empty_stream_minutes,
                 shared_state=None):  # TASK-010 adds this
        self._shared_state = shared_state  # TASK-010 adds this
        self._pid = os.getpid()          # line 85
        self._name = name or mp.current_process().name  # line 82

    async def start(self):               # line 313
        self.queue = QueueManager(worker_name=self._name)  # line 317
        # CHANGE: pass state_tracker to QueueManager

    async def signature_validation(self, reader, writer):  # line 487
        # prefix = await reader.readline()  # line 494
        # if prefix == b'health':            # line 508
        # elif prefix == b'check_state':     # line 514
        # else: signature check              # line 519+
        # CHANGE: add elif prefix == b'info':

    async def connection_handler(self, reader, writer):  # line 652
        # Immediate execution path (non-queued):
        # handle_queue_wrapper() at line 696 with task.queued False
        # at line 634-650: executor = TaskExecutor(task); result = await executor.run()

    async def handle_queue_wrapper(self, task, uid, writer):  # line 606
        # task.queued is True  → queue path (line 617-633)
        # task.queued is False → immediate execution (line 634-650)
        #   executor = TaskExecutor(task)  # line 638
        #   result = await executor.run()  # line 639

    async def start_subscription(self):  # line 195
        # Redis loop: lines 216-263
        # task = cloudpickle.loads(serialized_task)  # line 232
        # task_id = fn['uid']                        # line 229
        # executor = TaskExecutor(task)              # line 237
        # result = await executor.run()              # line 238

    async def worker_health(self, writer):          # line 420 — PATTERN TO FOLLOW
    async def response_keepalive(self, writer, status):  # line 403

# qw/state.py (from TASK-009)
class StateTracker:
    def __init__(self, shared_state, worker_name, pid): ...
    def task_queued(self, task_id, function_name): ...
    def task_executing(self, task_id, source): ...
    def task_completed(self, task_id, result, source): ...
    def get_state(self) -> dict: ...
    def _get_function_name(self, task) -> str: ...
```

### Does NOT Exist
- ~~`QWorker._state`~~ — does not exist yet; this task creates it
- ~~`QWorker.worker_info()`~~ — does not exist yet; this task creates it
- ~~`QueueWrapper.function_name`~~ — no such property; use StateTracker._get_function_name()
- ~~`QueueWrapper.enqueued_at`~~ — no such attribute; StateTracker records this externally
- ~~localhost detection in signature_validation~~ — does not exist; this task adds it

---

## Implementation Notes

### Pattern to Follow
```python
# worker_info follows the same pattern as worker_health (line 420):
async def worker_info(self, writer: asyncio.StreamWriter):
    if self._state:
        state = self._state.get_state()
    else:
        state = {"error": "State tracking not available"}
    status = {
        "worker": {
            "name": self.name,
            "address": self.server_address,
            "pid": self._pid
        },
        "state": state
    }
    await self.response_keepalive(status=status, writer=writer)

# Localhost detection for auth-free info:
# In signature_validation, check peer address from writer:
# addr = writer.get_extra_info("peername")
# is_localhost = addr and addr[0] in ("127.0.0.1", "::1")
```

### Key Constraints
- ALL state updates must be guarded with `if self._state:` — shared_state may be None
  in tests or if Manager wasn't created
- Extract task_id as `str(task.id)` (UUID → string) for JSON serialization
- For Redis stream tasks, `task_id` comes from `fn['uid']` (line 229), NOT from the task wrapper
- Function name extraction uses `self._state._get_function_name(task)` 
- Localhost auth bypass applies ONLY to the `info` command, not to `health` or `check_state`
- The `info` command response uses `json_encoder()` same as health/check_state

### References in Codebase
- `qw/server.py:420-436` — `worker_health()` as template for `worker_info()`
- `qw/server.py:508-518` — prefix dispatch in signature_validation
- `qw/server.py:606-650` — handle_queue_wrapper (immediate execution path)
- `qw/server.py:225-263` — Redis stream message processing loop

---

## Acceptance Criteria

- [ ] `StateTracker` created in `QWorker.__init__()` when shared_state is not None
- [ ] Direct TCP execution (non-queued tasks) updates state: executing → completed
- [ ] Redis Stream execution updates state: executing → completed
- [ ] `worker_info()` method returns aggregated state as JSON
- [ ] `info` TCP command dispatched in `signature_validation()`
- [ ] Localhost connections to `info` skip signature auth
- [ ] Remote connections to `info` require signature auth (same as health/check_state)
- [ ] All state updates guarded with `if self._state:`
- [ ] Existing health/check_state commands still work unchanged

---

## Test Specification

Integration tests for the TCP `info` command are covered in TASK-015. This task's
verification is via manual smoke testing that:
1. QWorker starts without errors
2. `health` and `check_state` TCP commands still work
3. State tracking doesn't crash on task execution

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies**:
   - TASK-009 completed: `qw/state.py` exists with StateTracker
   - TASK-010 completed: `start_server()` and `QWorker.__init__()` accept shared_state
3. **Verify the Codebase Contract**:
   - Read `qw/server.py` to confirm line numbers match (esp. signature_validation, connection_handler, start_subscription)
   - Read `qw/state.py` to confirm StateTracker API
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement** the changes to `qw/server.py`
6. **Verify** existing tests and health command still work
7. **Move this file** to `tasks/completed/TASK-011-qworker-state-instrumentation.md`
8. **Update index** → `"done"`

---

## Completion Note

**Completed by**: Claude Code (sdd-worker)
**Date**: 2026-04-08
**Notes**: All acceptance criteria met. Added StateTracker import, init in QWorker.__init__,
  state_tracker passed to QueueManager in start(), TCP task instrumentation in
  handle_queue_wrapper(), Redis task instrumentation in start_subscription(), worker_info()
  method, and 'info' prefix dispatch in signature_validation() with localhost bypass.
**Deviations from spec**: For remote 'info', signature auth reads an additional prefix+payload
  after the 'info' line (different protocol framing than health/check_state which have no
  additional data); this matches the spec's intent but uses a custom framing for remote auth.
