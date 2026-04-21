# TASK-022: Wiring & Observability

**Feature**: fix-qworker-queue-handler
**Spec**: `sdd/specs/fix-qworker-queue-handler.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S
**Depends-on**: TASK-021
**Assigned-to**: unassigned

---

## Context

> Why this task exists. Its role in the broader feature.
> Reference the spec section it implements.

The consumer monitor needs to be wired into the QueueManager lifecycle to actually run. Additionally, to verify queue health from the outside, we need to expose consumer metrics via the existing `snapshot()` method. This task implements Module 3 of the spec.

---

## Scope

> Precisely what this task must implement. Nothing more.

- Update `QueueManager.__init__` in `qw/queues/manager.py` to add `self._respawn_events: int = 0`, `self._monitor_task: asyncio.Task | None = None`, and `self._monitor_interval: float = 60.0` (or get from `WORKER_CONSUMER_MONITOR_INTERVAL` if added to conf).
- Update `qw/conf.py` to add `WORKER_CONSUMER_MONITOR_INTERVAL` with a default of 60.
- Update `fire_consumers()` to create and start the `self._monitor_task = asyncio.create_task(self._consumer_monitor())`. Append it to `self.consumers` if required or track it independently.
- Update `empty_queue()` to cancel `self._monitor_task` alongside other consumer tasks.
- Update `snapshot()` to include `"consumer_alive"`, `"consumer_total"`, and `"respawn_events"` keys.

**NOT in scope**: Adding new external prometheus metrics.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/manager.py` | MODIFY | Add initialization, startup, cancellation, and metrics. |
| `qw/conf.py` | MODIFY | Add `WORKER_CONSUMER_MONITOR_INTERVAL` default. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.conf import WORKER_CONSUMER_MONITOR_INTERVAL
```

### Existing Signatures to Use
```python
# qw/queues/manager.py
class QueueManager:
    def __init__(self, worker_name: str, state_tracker=None, policy: QueueSizePolicy | None = None) -> None:
    async def fire_consumers(self) -> None:
    async def empty_queue(self) -> None:
    def snapshot(self) -> dict:
```

### Does NOT Exist
- ~~`QWorker.respawn_queue_handlers()`~~ — not a real method

---

## Implementation Notes

### Key Constraints
- Ensure `self._monitor_task` is handled carefully to avoid `NoneType` errors when cancelling.
- `consumer_alive` should count `self.consumers` elements that are not `.done()`.

---

## Acceptance Criteria

- [ ] `_monitor_task` is created and started in `fire_consumers()`.
- [ ] `_monitor_task` is cancelled correctly in `empty_queue()`.
- [ ] `snapshot()` returns consumer health data correctly.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: 
**Date**: 
**Notes**: 
**Deviations from spec**: 
