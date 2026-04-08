# TASK-012: QueueManager State Instrumentation

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-009, TASK-011
**Assigned-to**: unassigned

---

## Context

> QueueManager handles the asyncio queue (deferred execution path). This task instruments
> it to report task state changes to the shared StateTracker: when tasks are queued, when
> they start executing, and when they complete (with success/error and retry tracking).
> Implements: Spec Module 4 (QueueManager State Instrumentation).

---

## Scope

- Modify `qw/queues/manager.py`:
  - Update `QueueManager.__init__()` to accept an optional `state_tracker` parameter
    (default None). Store as `self._state`
  - In `put()` (line 103): after `queue.put_nowait(task)`, call
    `self._state.task_queued(str(task.id), self._state._get_function_name(task))`
  - In `queue_handler()` (line 137):
    - After `task = await self.queue.get()` (line 141): call
      `self._state.task_executing(str(task.id), source="queue")`
    - In the `finally` block (line 195-199): call
      `self._state.task_completed(str(task.id), result_str, source="queue")`
      where `result_str` is "success" if result is not an exception, "error" otherwise
  - Guard ALL state calls with `if self._state:`

**NOT in scope**:
- Changes to QWorker (TASK-011 already passes state_tracker to QueueManager)
- Broker instrumentation (TASK-013)
- CLI (TASK-014)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/manager.py` | MODIFY | Accept state_tracker, instrument put() and queue_handler() |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# No new imports needed in manager.py — StateTracker is passed as a constructor arg
# The type can be referenced for type hints if desired:
# from qw.state import StateTracker  # (optional, for type annotation only)
```

### Existing Signatures to Use
```python
# qw/queues/manager.py:31
class QueueManager:
    def __init__(self, worker_name: str):  # line 35
        self.logger = logging.getLogger('QW.Queue')       # line 36
        self.worker_name = worker_name                     # line 37
        self.queue: asyncio.Queue = asyncio.Queue(         # line 38
            maxsize=WORKER_QUEUE_SIZE                      # line 39
        )
        self.consumers: list = []                          # line 41
        self._callback: Union[Callable, Awaitable]         # line 46
        # CHANGE: add state_tracker=None parameter, store as self._state

    async def put(self, task: QueueWrapper, id: str):  # line 103
        # self.queue.put_nowait(task)  # line 111
        # CHANGE: after put_nowait, call self._state.task_queued(...)

    async def queue_handler(self):  # line 137
        # while True:
        #     task = await self.queue.get()            # line 141
        #     # CHANGE: call self._state.task_executing(str(task.id), "queue") here
        #     executor = TaskExecutor(task)             # line 147
        #     result = await executor.run()             # line 148
        #     # ... retry logic lines 164-179 ...
        #     finally:
        #         self.queue.task_done()                # line 197
        #         await self._callback(task, result=result)  # line 198-199
        #         # CHANGE: call self._state.task_completed(...) here

# qw/state.py (from TASK-009)
class StateTracker:
    def task_queued(self, task_id: str, function_name: str) -> None: ...
    def task_executing(self, task_id: str, source: str) -> None: ...
    def task_completed(self, task_id: str, result: str, source: str) -> None: ...
    def _get_function_name(self, task) -> str: ...

# qw/wrappers/base.py:10
class QueueWrapper:
    @property
    def id(self) -> uuid.UUID:  # line 58
    retries: int = 0            # line 25
```

### Does NOT Exist
- ~~`QueueManager._state`~~ — does not exist yet; this task adds it
- ~~`QueueWrapper.function_name`~~ — no such property
- ~~`QueueWrapper.enqueued_at`~~ — no such attribute

---

## Implementation Notes

### Pattern to Follow
```python
# In __init__, add state_tracker parameter:
def __init__(self, worker_name: str, state_tracker=None):
    # ... existing code ...
    self._state = state_tracker

# In put(), after queue.put_nowait(task):
if self._state:
    fn_name = self._state._get_function_name(task)
    self._state.task_queued(str(task.id), fn_name)

# In queue_handler(), after task = await self.queue.get():
if self._state:
    self._state.task_executing(str(task.id), source="queue")

# In queue_handler() finally block, after self._callback:
if self._state:
    result_str = "error" if isinstance(result, BaseException) else "success"
    self._state.task_completed(str(task.id), result_str, source="queue")
```

### Key Constraints
- Guard ALL state calls with `if self._state:` — QueueManager must work without state tracking
- The `put()` method receives `id` as a parameter but also `task.id` exists — use `str(task.id)`
  for consistency with how the task UUID is generated
- In `queue_handler`, when a task is retried (lines 164-179), it gets re-queued via
  `await self.queue.put(task)`. This will trigger another `queue.get()` and another
  `task_executing` call — which is correct behavior (the task re-enters the queue cycle)
- Keep the `task_completed` call in `finally` so it always runs even on exception

### References in Codebase
- `qw/queues/manager.py:103-122` — put() method
- `qw/queues/manager.py:137-200` — queue_handler() method

---

## Acceptance Criteria

- [ ] `QueueManager.__init__()` accepts optional `state_tracker` parameter
- [ ] `put()` calls `state.task_queued()` after adding task to queue
- [ ] `queue_handler()` calls `state.task_executing()` when task is dequeued
- [ ] `queue_handler()` calls `state.task_completed()` in finally block with correct result
- [ ] All state calls guarded with `if self._state:`
- [ ] QueueManager works normally when state_tracker is None (backward compatible)
- [ ] Retry path correctly re-records task state on re-queue

---

## Test Specification

No new test file — QueueManager instrumentation is best tested via integration tests
in TASK-015. Verification is by confirming QueueManager still processes tasks normally
and state updates appear in the shared dict during integration testing.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies**:
   - TASK-009 completed: `qw/state.py` exists with StateTracker
   - TASK-011 completed: QWorker passes state_tracker to QueueManager constructor
3. **Verify the Codebase Contract**:
   - Read `qw/queues/manager.py` — confirm __init__, put(), queue_handler() line numbers
   - Read `qw/state.py` — confirm StateTracker API
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement** the changes to `qw/queues/manager.py`
6. **Verify** no import errors, existing functionality unbroken
7. **Move this file** to `tasks/completed/TASK-012-queue-manager-instrumentation.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none | describe if any
