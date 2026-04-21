# TASK-023: Consumer Resilience Tests

**Feature**: fix-qworker-queue-handler
**Spec**: `sdd/specs/fix-qworker-queue-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M
**Depends-on**: TASK-020, TASK-021, TASK-022
**Assigned-to**: unassigned

---

## Context

> Why this task exists. Its role in the broader feature.
> Reference the spec section it implements.

To ensure our consumer loops are completely resilient and the monitor respawns dead tasks accurately, we need to build automated tests (Module 4) to empirically verify the changes from the previous tasks.

---

## Scope

> Precisely what this task must implement. Nothing more.

- Create `tests/test_consumer_resilience.py`.
- Write tests:
  - `test_consumer_survives_task_exception`
  - `test_consumer_survives_callback_error`
  - `test_monitor_detects_dead_consumer`
  - `test_snapshot_includes_consumer_health`
- Verify overall integration, i.e., queue still drains successfully even if some tasks error.

**NOT in scope**: Implementation changes in `qw/queues/manager.py`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_consumer_resilience.py` | CREATE | Contains all tests required by spec. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pytest
from qw.queues.manager import QueueManager
```

### Does NOT Exist
- ~~`QueueManager.consumer_health()`~~ — does not exist

---

## Implementation Notes

### Key Constraints
- Use standard `pytest` fixtures for QueueWrappers.
- Use `asyncio.sleep` to allow event loop to switch contexts and process tasks in tests.

---

## Acceptance Criteria

- [ ] All new unit tests run and pass without breaking the event loop.
- [ ] `test_consumer_resilience.py` has no linting errors.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: 
**Date**: 
**Notes**: 
**Deviations from spec**: 
