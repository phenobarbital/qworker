# Feature Specification: Fix QWorker Queue Handler — Consumer Resilience & Monitor

**Feature ID**: FEAT-004
**Date**: 2026-04-21
**Author**: Jesús Lara
**Status**: approved
**Target version**: next

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement

The `QueueManager.queue_handler()` consumer coroutines silently die when a task
raises an exception. Because `queue_handler` re-raises exceptions (`raise` on
lines 299, 317, 319, 332 of `qw/queues/manager.py`), the `while True` loop
exits and the `asyncio.Task` created by `fire_consumers()` is destroyed. Once
**all** consumer tasks are dead, the asyncio queue stops draining entirely —
new tasks accumulate in `queued` status indefinitely while the worker continues
to report `status=ALIVE`.

**Observed in production**: a worker showing 4 tasks in `queued` status (enqueued
between 06:19 and 12:00) with `Started = —`, while the last completed task
finished at 06:09:17 with `error`. The worker's health check passed because
it only verifies the process/event-loop, not the consumer tasks.

### Goals

1. **Level 1 — Survivor**: Make `queue_handler` resilient to task failures.
   Exceptions from task execution must be caught and logged without killing the
   consumer loop.
2. **Level 2 — Monitor**: Add a background `_consumer_monitor()` coroutine that
   periodically checks the health of consumer tasks and respawns any that have
   died (defense in depth).

### Non-Goals (explicitly out of scope)

- Changing the retry/re-queue logic (existing retry semantics stay as-is).
- Modifying the `TaskExecutor` internals.
- Adding external monitoring (Prometheus, etc.) — covered by future work.
- Changing the queue sizing / dynamic policy (FEAT-003 scope).

---

## 2. Architectural Design

### Overview

Two complementary changes to `QueueManager`:

1. **Wrap `queue_handler` exceptions** so the `while True` loop never exits
   due to a task error. The `finally` block's callback is also guarded.
2. **Add `_consumer_monitor()`** — a new background task (alongside
   `_shrink_monitor`) that periodically inspects the `self.consumers` list,
   detects `.done()` tasks, logs the cause if available, replaces them with
   fresh `queue_handler` instances, and optionally exposes the respawn count
   via the `snapshot()` dict.

### Component Diagram

```
QueueManager
  ├── fire_consumers()
  │     ├── queue_handler (N-1 tasks)  ← Level 1: made resilient
  │     ├── _shrink_monitor (1 task)   ← existing
  │     └── _consumer_monitor (1 task) ← Level 2: NEW
  │
  └── snapshot()  ← extended with consumer_alive / respawn_events
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QueueManager` | modifies | core changes happen here |
| `QueueManager.fire_consumers()` | modifies | add `_consumer_monitor` startup |
| `QueueManager.snapshot()` | extends | add consumer health fields |
| `QWorker.worker_check_state()` | reads | auto-picks up new snapshot fields |
| `StateTracker` | unchanged | no changes needed |
| `TaskExecutor` | unchanged | no changes needed |

### Data Models

No new data models required.

### New Public Interfaces

```python
# qw/queues/manager.py — new method
class QueueManager:
    async def _consumer_monitor(self) -> None:
        """Periodic background check: respawn dead consumer tasks."""
        ...
```

---

## 3. Module Breakdown

### Module 1: Resilient queue_handler

- **Path**: `qw/queues/manager.py` — method `queue_handler()` (line 273)
- **Responsibility**: Catch all exceptions from task execution and callback
  within the `while True` loop, log them, and continue consuming. Remove all
  bare `raise` statements that can kill the consumer.
- **Depends on**: nothing new

**Key change pattern:**

```python
async def queue_handler(self):
    while True:
        result = None
        task = await self.queue.get()
        try:
            # ... existing executor + result handling ...
            # Instead of `raise result` or `raise`, just assign result = exc
        except Exception as exc:
            self.logger.error(f"Task {task} failed: {exc}")
            result = exc
            # DO NOT re-raise — consumer must survive
        finally:
            self.queue.task_done()
            try:
                await self._callback(task, result=result)
            except Exception as cb_err:
                self.logger.error(f"Callback error for {task}: {cb_err}")
            if self._state:
                result_str = "error" if isinstance(result, BaseException) else "success"
                self._state.task_completed(str(task.id), result_str, source="queue")
```

### Module 2: Consumer Monitor

- **Path**: `qw/queues/manager.py` — new method `_consumer_monitor()`
- **Responsibility**: Periodically scan `self.consumers`, detect `.done()`
  tasks, log the reason (via `.exception()` or `.result()`), respawn with a
  new `queue_handler()` task, and track respawn count.
- **Depends on**: Module 1 (but functions independently as defense-in-depth)

**Key design:**

```python
async def _consumer_monitor(self) -> None:
    """Defense-in-depth: respawn any dead consumer tasks."""
    while True:
        try:
            await asyncio.sleep(self._monitor_interval)
            alive = []
            for t in self.consumers:
                if t is self._monitor_task:
                    alive.append(t)
                    continue
                if t is self._shrink_task:
                    # Also check shrink_task health
                    if t.done():
                        exc = t.exception() if not t.cancelled() else None
                        self.logger.warning(
                            f"_shrink_monitor died: {exc}, respawning"
                        )
                        new_t = asyncio.create_task(self._shrink_monitor())
                        self._shrink_task = new_t
                        alive.append(new_t)
                        self._respawn_events += 1
                    else:
                        alive.append(t)
                    continue
                if t.done():
                    exc = t.exception() if not t.cancelled() else None
                    self.logger.warning(
                        f"Consumer died: {exc}, respawning"
                    )
                    new_t = asyncio.create_task(self.queue_handler())
                    alive.append(new_t)
                    self._respawn_events += 1
                else:
                    alive.append(t)
            self.consumers = alive
        except asyncio.CancelledError:
            break
        except Exception:
            self.logger.exception("_consumer_monitor error")
```

### Module 3: Wiring & Observability

- **Path**: `qw/queues/manager.py` — methods `__init__`, `fire_consumers`, `snapshot`, `empty_queue`
- **Responsibility**: Initialize monitor state, start the monitor task,
  expose respawn count and alive-consumer count in `snapshot()`.
- **Depends on**: Module 2

**Changes:**

- `__init__`: add `self._respawn_events: int = 0`, `self._monitor_task: asyncio.Task | None = None`, `self._monitor_interval: float = 5.0`
- `fire_consumers`: after existing loop, start `self._monitor_task = asyncio.create_task(self._consumer_monitor())`; append to `self.consumers`
- `snapshot`: add `"consumer_alive"`, `"consumer_total"`, `"respawn_events"` keys
- `empty_queue`: cancel `_monitor_task` alongside other consumers

### Module 4: Tests

- **Path**: `tests/test_consumer_resilience.py` [NEW]
- **Responsibility**: Verify consumers survive task errors and monitor respawns dead ones.
- **Depends on**: Module 1, Module 2

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_consumer_survives_task_exception` | Module 1 | Enqueue a task that raises; verify consumer still processes next task |
| `test_consumer_survives_callback_error` | Module 1 | Mock callback to raise; verify consumer continues |
| `test_consumer_survives_runtime_error` | Module 1 | Enqueue task returning RuntimeError; verify loop continues |
| `test_monitor_detects_dead_consumer` | Module 2 | Manually cancel a consumer task; verify monitor respawns it |
| `test_monitor_increments_respawn_count` | Module 2 | Kill a consumer; check `snapshot()["respawn_events"] > 0` |
| `test_snapshot_includes_consumer_health` | Module 3 | Verify `consumer_alive` and `consumer_total` in snapshot |

### Integration Tests

| Test | Description |
|---|---|
| `test_queue_drains_after_error` | Submit N tasks where task #1 errors; verify tasks #2..N complete |

### Test Data / Fixtures

```python
@pytest.fixture
def failing_task():
    """A QueueWrapper that always raises RuntimeError."""
    ...

@pytest.fixture
def succeeding_task():
    """A QueueWrapper that returns successfully."""
    ...
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `queue_handler` never exits its `while True` loop due to a task exception
- [ ] `_callback` errors in `finally` do not kill the consumer
- [ ] `_consumer_monitor` runs as a background task and respawns dead consumers
- [ ] `snapshot()` exposes `consumer_alive`, `consumer_total`, and `respawn_events`
- [ ] All unit tests pass (`pytest tests/test_consumer_resilience.py -v`)
- [ ] No breaking changes to existing public API
- [ ] `qw info` output correctly shows consumer health data

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**

### Verified Imports

```python
from qw.queues.manager import QueueManager        # verified: qw/queues/manager.py:32
from qw.executor import TaskExecutor               # verified: qw/executor/__init__.py:16
from qw.wrappers.base import QueueWrapper           # verified: qw/wrappers/base.py
from qw.exceptions import QWException               # verified: qw/exceptions.py
from qw.conf import (                               # verified: qw/conf.py
    WORKER_QUEUE_SIZE,          # line 19, default=4
    WORKER_RETRY_INTERVAL,      # line 34, default=10
    WORKER_RETRY_COUNT,         # line 35, default=2
    WORKER_QUEUE_CALLBACK,      # conf.py
)
from qw.state import StateTracker                   # verified: qw/state.py:13
```

### Existing Class Signatures

```python
# qw/queues/manager.py
class QueueManager:
    def __init__(self, worker_name: str, state_tracker=None,
                 policy: QueueSizePolicy | None = None) -> None:  # line 35
    async def fire_consumers(self) -> None:       # line 148
    async def queue_handler(self):                 # line 273
    async def _shrink_monitor(self) -> None:       # line 240
    async def empty_queue(self) -> None:            # line 156
    def snapshot(self) -> dict:                     # line 84
    async def put(self, task: QueueWrapper, id: str) -> bool:  # line 171

    # Instance attributes:
    self.queue: asyncio.Queue                       # line 46
    self.consumers: list                            # line 49
    self._state: StateTracker | None                # line 61
    self._shrink_task: asyncio.Task | None          # line 65
    self._callback: Callable | Awaitable            # line 54
    self._policy: QueueSizePolicy                   # line 43
    self._warn_active: bool                         # line 63
```

```python
# qw/state.py
class StateTracker:
    def task_queued(self, task_id: str, function_name: str) -> None:   # line 98
    def task_executing(self, task_id: str, source: str) -> None:       # line 122
    def task_completed(self, task_id: str, result: str, source: str) -> None:  # line 167
```

```python
# qw/server.py
class QWorker:
    async def start(self):                          # line 331
        # calls self.queue.fire_consumers() at line 386
    async def worker_check_state(self, writer):     # line 475
        # calls self.queue.snapshot() at line 478
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `_consumer_monitor()` | `self.consumers` | list iteration + `.done()` check | `manager.py:49` |
| `_consumer_monitor()` | `self.queue_handler()` | `asyncio.create_task()` | `manager.py:273` |
| `fire_consumers()` | `_consumer_monitor()` | `asyncio.create_task()` | `manager.py:148` |
| `snapshot()` | `_respawn_events` | new instance attribute | to be added |

### Does NOT Exist (Anti-Hallucination)

- ~~`QueueManager.restart_consumers()`~~ — does not exist
- ~~`QueueManager._monitor_task`~~ — does not exist yet (to be added)
- ~~`QueueManager.consumer_health()`~~ — does not exist
- ~~`StateTracker.consumer_died()`~~ — not a real method
- ~~`QWorker.respawn_queue_handlers()`~~ — not a real method

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- Matches the existing `_shrink_monitor()` coroutine pattern (background `while True` + `asyncio.CancelledError` exit)
- Uses `self.logger` for all logging (existing convention)
- Atomic state updates via full-assignment to `self._state` proxy (documented in `state.py` header)

### Known Risks / Gotchas

| Risk | Mitigation |
|---|---|
| `asyncio.Task.exception()` raises `CancelledError` if task was cancelled (not failed) | Check `t.cancelled()` before calling `.exception()` |
| Calling `.exception()` on a task that hasn't been awaited consumes the exception — if not called, Python logs "Task exception was never retrieved" | The monitor explicitly calls `.exception()` |
| Race between monitor detecting dead consumer and queue_handler actually exiting | Harmless — monitor only acts on `.done()` tasks |
| The `finally` block in `queue_handler` does `await self._callback(...)` which could hang | Consider wrapping with `asyncio.wait_for` with a timeout (optional improvement) |

### External Dependencies

No new external dependencies required.

---

## 8. Open Questions

- [x] Should the monitor also respawn `_shrink_monitor` if it dies? — **Yes**, included in the design.
- [x] Should we add a cap on respawn attempts per consumer (e.g., max 10 respawns before alerting)? — *Owner: Jesús*: Max 5 respawns per consumer before alerting, adding a notification callback to be used for alerting.
- [x] Should `_monitor_interval` be configurable via env var (e.g., `WORKER_CONSUMER_MONITOR_INTERVAL`)? — *Owner: Jesús*: Default 60 seconds, configurable via env var (added into `qw/conf.py`)

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-21 | Jesús Lara / AI | Initial draft |
