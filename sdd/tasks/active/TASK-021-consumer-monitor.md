# TASK-021: Consumer Monitor

**Feature**: fix-qworker-queue-handler
**Spec**: `sdd/specs/fix-qworker-queue-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M
**Depends-on**: TASK-020
**Assigned-to**: unassigned

---

## Context

> Why this task exists. Its role in the broader feature.
> Reference the spec section it implements.

Even with a resilient `queue_handler()`, tasks might still die due to unforeseen bugs or memory issues. This task implements Module 2 of the spec: a background `_consumer_monitor()` coroutine that periodically checks the health of all running consumer tasks, logs any failures, and respawns them to ensure queue processing remains active.

---

## Scope

> Precisely what this task must implement. Nothing more.

- Add a new async method `_consumer_monitor(self) -> None` to `QueueManager` in `qw/queues/manager.py`.
- Within a `while True:` loop, await `asyncio.sleep(self._monitor_interval)`.
- Iterate through `self.consumers` to detect any `.done()` tasks.
- Skip checking `self._monitor_task`.
- Check if `self._shrink_task` is done; if so, respawn it using `asyncio.create_task(self._shrink_monitor())`, assign to `self._shrink_task`, increment `self._respawn_events` and log.
- For other done tasks in `self.consumers`, log the failure using `t.exception()` (ensure `t.cancelled()` is checked first).
- Respawn dead consumers using `asyncio.create_task(self.queue_handler())` and append to the active consumers list.
- Increment `self._respawn_events` for each replaced consumer.
- Maintain the list of active consumers back to `self.consumers`.

**NOT in scope**: Wiring up the monitor task startup or snapshot metrics (done in next task).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/manager.py` | MODIFY | Add `_consumer_monitor()` logic. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import asyncio
from qw.queues.manager import QueueManager
```

### Existing Signatures to Use
```python
# qw/queues/manager.py
class QueueManager:
    async def _shrink_monitor(self) -> None:
    async def queue_handler(self):
    self.consumers: list
    self._shrink_task: asyncio.Task | None
```

### Does NOT Exist
- ~~`QueueManager.restart_consumers()`~~ — does not exist

---

## Implementation Notes

### Key Constraints
- Must handle `asyncio.CancelledError` properly to break the monitor loop.
- Use `self.logger.warning()` for dead consumer respawns.
- Catch broad exceptions `except Exception:` around the monitor loop body to ensure the monitor itself doesn't crash from internal bugs.

---

## Acceptance Criteria

- [ ] `_consumer_monitor` periodically checks `self.consumers` and `self._shrink_task`.
- [ ] Dead tasks are replaced with new `asyncio.Task` instances.
- [ ] `self._respawn_events` is incremented.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: 
**Date**: 
**Notes**: 
**Deviations from spec**: 
