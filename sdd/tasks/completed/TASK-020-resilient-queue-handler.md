# TASK-020: Resilient queue_handler

**Feature**: fix-qworker-queue-handler
**Spec**: `sdd/specs/fix-qworker-queue-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> Why this task exists. Its role in the broader feature.
> Reference the spec section it implements.

The `QueueManager.queue_handler()` consumer coroutines silently die when a task raises an exception. Because `queue_handler` re-raises exceptions (`raise` on lines 299, 317, 319, 332 of `qw/queues/manager.py`), the `while True` loop exits and the `asyncio.Task` created by `fire_consumers()` is destroyed. This task implements Module 1 of the spec to wrap task execution and callback execution in try-except blocks, ensuring the consumer loop survives exceptions.

---

## Scope

> Precisely what this task must implement. Nothing more.

- Modify `qw/queues/manager.py` method `queue_handler()`.
- Wrap task execution (where `self.queue.get()` returns a task to be awaited) inside a `try/except Exception as exc:` block.
- Log the exception with `self.logger.error()` instead of raising it.
- Assign the exception object to `result` so `_callback` receives it as the outcome.
- Protect the `await self._callback(task, result=result)` call inside the `finally:` block with another `try/except Exception as cb_err:` block to prevent callback failures from crashing the loop.
- Remove all bare `raise` statements within `queue_handler` that would exit the loop upon task failure.

**NOT in scope**: Do not implement `_consumer_monitor()` yet. Do not change retry logic.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/manager.py` | MODIFY | Update `queue_handler()` to catch exceptions. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.queues.manager import QueueManager
```

### Existing Signatures to Use
```python
# qw/queues/manager.py
class QueueManager:
    async def queue_handler(self):
    self.queue: asyncio.Queue
    self._state: StateTracker | None
    self._callback: Callable | Awaitable
```

### Does NOT Exist
- ~~`QueueManager.restart_consumers()`~~ — does not exist
- ~~`QueueManager._monitor_task`~~ — does not exist yet
- ~~`QueueManager.consumer_health()`~~ — does not exist
- ~~`StateTracker.consumer_died()`~~ — not a real method

---

## Implementation Notes

### Key Constraints
- Must use `self.logger.error(f"Task {task} failed: {exc}")` for logging task exceptions.
- The state must still be correctly reported via `self._state.task_completed()`.
- Do not let the `queue_handler` loop break unless the queue itself is explicitly stopped or cancelled.

---

## Acceptance Criteria

- [ ] `queue_handler` catches task exceptions and continues the loop.
- [ ] `queue_handler` catches callback exceptions in the `finally` block and continues.
- [ ] No bare `raise` statements in `queue_handler` that could terminate the consumer loop.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: Claude
**Date**: 2026-04-21
**Notes**: Wrapped queue_handler execution in try/except and prevented re-raising exceptions so the consumer loop survives errors. Also wrapped callback execution with try/except in finally block.
**Deviations from spec**: None
