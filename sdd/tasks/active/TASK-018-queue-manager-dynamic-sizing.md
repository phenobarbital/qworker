# TASK-018: Integrate dynamic sizing into QueueManager

**Feature**: dynamic-queue-sizing
**Spec**: `sdd/specs/dynamic-queue-sizing.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-016, TASK-017
**Assigned-to**: unassigned

---

## Context

Implements Module 2 of the spec — the operational heart of the feature.
`QueueManager.put()` must consult the `QueueSizePolicy` (TASK-017) to decide
between normal admit, warn-and-admit, grow-and-admit, or discard. A
background `_shrink_monitor` coroutine shrinks the queue back toward the
configured base once pressure subsides.

This task also adds the read-only accessors (`max_size`, `base_size`,
`grow_margin`, `snapshot()`) that TASK-019 will surface through
`worker_check_state` and the health endpoint.

---

## Scope

- Modify `QueueManager.__init__` (`qw/queues/manager.py:35`) to accept an
  optional `policy: QueueSizePolicy | None`. When `None`, build a default
  via `QueueSizePolicy.from_config(base_size=WORKER_QUEUE_SIZE)`.
- Replace the hardcoded `maxsize=WORKER_QUEUE_SIZE` at
  `qw/queues/manager.py:39` with `maxsize=self._policy.current_maxsize`.
- Rewrite `QueueManager.put()` (lines 105-127) to implement the decision
  tree described in spec §2 "Component Diagram":
  1. If `policy.should_warn(size)` transitions `False→True`, log warning
     once (hysteresis; do not spam).
  2. Try `put_nowait`. On `QueueFull`:
     - If `policy.can_grow()`: call `policy.register_grow()`,
       `self._apply_maxsize(policy.current_maxsize)`, retry `put_nowait`,
       log warning including `grow_events`, `size/max_size`, `task.id`.
     - Else: `policy.register_discard()`, log error with the same fields,
       re-raise `QueueFull`.
  3. When the admitted `size` exceeds the low watermark, call
     `policy.note_high_usage(time.monotonic())`.
- Add helper `_apply_maxsize(self, new_max: int)` that:
  - Assigns `self.queue._maxsize = new_max`.
  - On grow, wakes one waiter via
    `self.queue._wakeup_next(self.queue._putters)` (safe no-op when empty).
- Add `_shrink_monitor(self)` coroutine:
  - Loops with `await asyncio.sleep(max(1.0, shrink_cooldown / 5))`.
  - If `policy.can_shrink(size, now)` and `size < policy.current_maxsize`:
    call `policy.register_shrink()` + `_apply_maxsize(...)` + log info.
  - Stops when its task is cancelled (mirrors `queue_handler`).
- Update `fire_consumers()` (`qw/queues/manager.py:84`) to spawn
  `_shrink_monitor` alongside the consumer tasks. Append it to
  `self.consumers` so `empty_queue()` cancels it cleanly.
- Add public read-only accessors:
  - `@property max_size` → `self.queue._maxsize`
  - `@property base_size` → `self._policy.base_size`
  - `@property grow_margin` → `self._policy.grow_margin`
  - `snapshot()` → dict with keys `size`, `max_size`, `base_size`,
    `grow_margin`, `ceiling`, `grow_events`, `discard_events`, `full`.
- Keep `size()`, `empty()`, `full()` unchanged — they delegate to
  `self.queue` as today.
- Add unit tests in `tests/test_queue_manager_dynamic.py` covering every
  Module 2 row in spec §4.

**NOT in scope**:
- Changes to `qw/server.py` or `qw/health.py` — TASK-019.
- Changes to `StateTracker` — TASK-019 (optional counters there).
- Changes to `fire_consumers` consumer count (it must stay tied to the
  base size — spec §7 "Consumer count vs queue size").

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/manager.py` | MODIFY | Wire policy, rewrite put(), add shrink monitor + accessors |
| `tests/test_queue_manager_dynamic.py` | CREATE | Module 2 unit tests from spec §4 |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Already present in qw/queues/manager.py (verified 2026-04-16):
import asyncio                                           # line 1
import time                                              # line 2
from typing import Union                                 # line 3
from collections.abc import Awaitable, Callable         # line 4
import importlib                                         # line 5
from navconfig.logging import logging                    # line 6
from qw.exceptions import QWException                    # line 20
from ..conf import (
    WORKER_QUEUE_SIZE,                                   # line 22
    WORKER_RETRY_INTERVAL,                               # line 23
    WORKER_RETRY_COUNT,                                  # line 24
    WORKER_QUEUE_CALLBACK,                               # line 25
)
from ..executor import TaskExecutor                      # line 27
from ..wrappers.base import QueueWrapper                 # line 28

# Added by this task:
from .policy import QueueSizePolicy                      # introduced in TASK-017
```

### Existing Signatures to Use
```python
# qw/queues/manager.py  (verified 2026-04-16, full re-read)
class QueueManager:
    def __init__(self, worker_name: str, state_tracker=None):        # line 35
        self.logger = logging.getLogger('QW.Queue')                  # line 36
        self.worker_name = worker_name                               # line 37
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=WORKER_QUEUE_SIZE                                # line 39
        )
        self.consumers: list = []                                    # line 41
        self._state = state_tracker                                  # line 53

    def size(self) -> int:                                            # line 75
    def empty(self) -> bool:                                          # line 78
    def full(self) -> bool:                                           # line 81
    async def fire_consumers(self) -> None:                           # line 84
        # spawns (WORKER_QUEUE_SIZE - 1) queue_handler tasks          # line 86
    async def empty_queue(self) -> None:                              # line 92
    async def put(self, task: QueueWrapper, id: str) -> bool:         # line 105
        # line 113: self.queue.put_nowait(task)
        # line 114-116: state tracking via self._state
        # line 123: raises asyncio.queues.QueueFull
    async def get(self) -> QueueWrapper:                              # line 129
    async def queue_handler(self) -> None:                            # line 142
```

```python
# qw/state.py  (verified 2026-04-16) — already called from put():
class StateTracker:
    def _get_function_name(self, task) -> str:           # line 45
    def task_queued(self, task_id: str,
                    function_name: str) -> None:         # line 98
# NOTE: no record_queue_grow / record_queue_discard in StateTracker today.
# Spec §3 Module 4 marks those as optional for TASK-019. Do NOT add here.
```

```python
# CPython asyncio internals used by this task (verified stable 3.9-3.12):
#   asyncio.Queue._maxsize     : int    — read/written directly
#   asyncio.Queue._putters     : collections.deque  — blocked putters
#   asyncio.Queue._wakeup_next : method — wakes one waiter from the deque
```

### Does NOT Exist
- ~~`asyncio.Queue.resize()`~~ — must mutate `_maxsize` directly.
- ~~`asyncio.Queue.set_maxsize()`~~ — does not exist.
- ~~`QueueManager.policy` as a public attribute~~ — store it as `self._policy`
  (private). Expose selected values via the documented properties only.
- ~~`QueueSizePolicy.logger`~~ — the policy does not log (per TASK-017).
  All logging happens in `QueueManager`.
- ~~`StateTracker.record_queue_grow()` / `.record_queue_discard()`~~ — NOT
  added in this task. Those are TASK-019 optional work.
- ~~A second consumer task on grow~~ — keep consumer count tied to base size
  (spec §7 "Consumer count vs queue size").
- ~~`asyncio.Queue.maxsize` property setter~~ — stdlib exposes a read-only
  `maxsize`; we mutate the private `_maxsize`.

---

## Implementation Notes

### Pattern to Follow
```python
# qw/queues/manager.py (partial — showing the new shapes)
from .policy import QueueSizePolicy

class QueueManager:
    def __init__(
        self,
        worker_name: str,
        state_tracker=None,
        policy: QueueSizePolicy | None = None,
    ) -> None:
        self.logger = logging.getLogger('QW.Queue')
        self.worker_name = worker_name
        self._policy = policy or QueueSizePolicy.from_config(
            base_size=WORKER_QUEUE_SIZE
        )
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=self._policy.current_maxsize
        )
        self.consumers: list = []
        self._callback = self.get_callback(WORKER_QUEUE_CALLBACK)
        self._state = state_tracker
        self._warn_active: bool = False   # hysteresis flag
        self._shrink_task: asyncio.Task | None = None

    # ---------------- read-only accessors ----------------
    @property
    def max_size(self) -> int:
        return self.queue._maxsize

    @property
    def base_size(self) -> int:
        return self._policy.base_size

    @property
    def grow_margin(self) -> int:
        return self._policy.grow_margin

    def snapshot(self) -> dict:
        sz = self.queue.qsize()
        return {
            "size": sz,
            "max_size": self.max_size,
            "base_size": self._policy.base_size,
            "grow_margin": self._policy.grow_margin,
            "ceiling": self._policy.ceiling(),
            "grow_events": self._policy.grow_events,
            "discard_events": self._policy.discard_events,
            "full": self.queue.full(),
        }

    # ---------------- internal: resize ----------------
    def _apply_maxsize(self, new_max: int) -> None:
        self.queue._maxsize = new_max
        # Release one blocked putter (no-op if none queued)
        if self.queue._putters:
            try:
                self.queue._wakeup_next(self.queue._putters)
            except Exception:  # defensive: never break the event loop
                self.logger.debug("wakeup_next swallowed", exc_info=True)

    # ---------------- rewritten put() ----------------
    async def put(self, task: QueueWrapper, id: str) -> bool:
        size = self.queue.qsize()
        # Warn transition (hysteresis)
        if self._policy.should_warn(size) and not self._warn_active:
            self._warn_active = True
            self.logger.warning(
                f"Queue near limit: size={size}/{self.max_size} "
                f"task_id={task.id}"
            )
        elif not self._policy.should_warn(size) and self._warn_active:
            self._warn_active = False

        try:
            self.queue.put_nowait(task)
        except asyncio.queues.QueueFull:
            if self._policy.can_grow():
                self._policy.register_grow()
                self._apply_maxsize(self._policy.current_maxsize)
                self.queue.put_nowait(task)
                self.logger.warning(
                    f"Queue grew: max_size={self.max_size} "
                    f"base={self.base_size} margin_used="
                    f"{self.max_size - self.base_size} "
                    f"grow_events={self._policy.grow_events} "
                    f"task_id={task.id}"
                )
            else:
                self._policy.register_discard()
                self.logger.error(
                    f"Queue at ceiling: max_size={self.max_size} "
                    f"discard_events={self._policy.discard_events} "
                    f"task_id={task.id}"
                )
                raise

        # State tracking (unchanged behaviour)
        if self._state:
            fn_name = self._state._get_function_name(task)
            self._state.task_queued(str(task.id), fn_name)

        # Cooldown reset on sustained pressure
        if self.queue.qsize() / max(1, self.max_size) > \
                self._policy.low_watermark_ratio:
            self._policy.note_high_usage(time.monotonic())

        await asyncio.sleep(0.1)
        self.logger.info(
            f'Task {task!s} with id {id} was queued at {int(time.time())}'
        )
        return True

    # ---------------- shrink monitor ----------------
    async def _shrink_monitor(self) -> None:
        interval = max(1.0, self._policy.shrink_cooldown / 5)
        while True:
            try:
                await asyncio.sleep(interval)
                size = self.queue.qsize()
                now = time.monotonic()
                if self._policy.can_shrink(size, now) and \
                        size < self._policy.current_maxsize:
                    self._policy.register_shrink()
                    self._apply_maxsize(self._policy.current_maxsize)
                    self.logger.info(
                        f"Queue shrunk: max_size={self.max_size}"
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("shrink_monitor error")

    # ---------------- fire_consumers (modified) ----------------
    async def fire_consumers(self) -> None:
        for _ in range(self._policy.base_size - 1):
            t = asyncio.create_task(self.queue_handler())
            self.consumers.append(t)
        self._shrink_task = asyncio.create_task(self._shrink_monitor())
        self.consumers.append(self._shrink_task)
```

### Key Constraints
- Preserve the existing `await asyncio.sleep(0.1)` and the `logger.info`
  call that announces enqueue — tests and operators rely on that log line.
- Preserve the state-tracker hook and its call order. It must still run
  after a successful admit (grow or normal).
- Consumer count stays tied to `base_size`, NOT `current_maxsize`.
- Hysteresis: warn log fires only on the `under→over` transition; no
  warn log on every put while above the threshold.
- `_apply_maxsize` must be safe to call even when `_putters` is empty.
- The shrink monitor must be cancellable via existing `empty_queue()`
  logic — so it lives in `self.consumers`.

### References in Codebase
- `qw/queues/manager.py:84` — existing `fire_consumers` pattern.
- `qw/queues/manager.py:105-127` — current `put()` to replace.
- Spec §7 — Known Risks / Gotchas.

---

## Acceptance Criteria

- [ ] `QueueManager(worker_name="x")` still works (backward compat — default
      policy built from config).
- [ ] `QueueManager(worker_name="x", policy=QueueSizePolicy(base_size=4,
      grow_margin=0))` matches today's behaviour exactly: 5th put raises
      `QueueFull`.
- [ ] With `base_size=4, grow_margin=2`: 6 rapid `put()` calls succeed;
      the 7th raises `QueueFull`. After the 5th, `max_size == 5`.
- [ ] Exactly one `warning` log is emitted per grow event; exactly one
      `error` log per discard event.
- [ ] `snapshot()` returns a dict with keys `size`, `max_size`, `base_size`,
      `grow_margin`, `ceiling`, `grow_events`, `discard_events`, `full`.
- [ ] `_shrink_monitor` reduces `max_size` back to `base_size` after the
      cooldown window elapses with size below the low watermark.
- [ ] All unit tests pass: `pytest tests/test_queue_manager_dynamic.py -v`.
- [ ] Pre-existing worker tests still pass: `pytest tests/ -v`.
- [ ] `ruff check qw/queues/` clean.

---

## Test Specification

```python
# tests/test_queue_manager_dynamic.py
import asyncio
import pytest

from qw.queues import QueueManager, QueueSizePolicy
from qw.wrappers import FuncWrapper


def _noop():
    return "ok"


def _mk_task() -> FuncWrapper:
    return FuncWrapper(host="local", func=_noop)


@pytest.fixture
def policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4,
        grow_margin=2,
        warn_ratio=0.8,
        shrink_cooldown=0.2,
        low_watermark_ratio=0.5,
    )


@pytest.fixture
def zero_margin_policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4, grow_margin=0, warn_ratio=0.8,
        shrink_cooldown=0.2, low_watermark_ratio=0.5,
    )


class TestQueueManagerDynamic:
    async def test_put_warns_at_threshold(self, policy, caplog):
        qm = QueueManager(worker_name="w", policy=policy)
        caplog.set_level("WARNING", logger="QW.Queue")
        # Fill to 80 % (4 items, ratio 4/4=1.0 > 0.8)
        for _ in range(4):
            await qm.put(_mk_task(), id="x")
        assert any("near limit" in r.message for r in caplog.records)

    async def test_put_grows_on_full(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        for _ in range(6):
            await qm.put(_mk_task(), id="x")
        assert qm.max_size == 6  # grew from 4 to 6
        assert qm.snapshot()["grow_events"] == 2

    async def test_put_discards_at_ceiling(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        for _ in range(6):
            await qm.put(_mk_task(), id="x")
        with pytest.raises(asyncio.QueueFull):
            await qm.put(_mk_task(), id="x")
        assert qm.snapshot()["discard_events"] == 1

    async def test_backward_compat_margin_zero(self, zero_margin_policy):
        qm = QueueManager(worker_name="w", policy=zero_margin_policy)
        for _ in range(4):
            await qm.put(_mk_task(), id="x")
        with pytest.raises(asyncio.QueueFull):
            await qm.put(_mk_task(), id="x")
        assert qm.max_size == 4  # never grew

    async def test_snapshot_fields(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        snap = qm.snapshot()
        assert set(snap.keys()) == {
            "size", "max_size", "base_size", "grow_margin",
            "ceiling", "grow_events", "discard_events", "full",
        }

    async def test_shrinks_after_cooldown(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        # Grow to max
        for _ in range(6):
            await qm.put(_mk_task(), id="x")
        assert qm.max_size == 6
        # Drain below low watermark
        while not qm.queue.empty():
            qm.queue.get_nowait()
            qm.queue.task_done()
        # Start the shrink monitor (fire_consumers spawns it)
        await qm.fire_consumers()
        # Wait for one or more shrink ticks
        await asyncio.sleep(1.0)
        # Cleanup
        await qm.empty_queue()
        assert qm.max_size <= 5  # shrank at least once
```

---

## Agent Instructions

When you pick up this task:

1. Confirm TASK-016 and TASK-017 are done (check `sdd/tasks/completed/`).
2. Re-read `qw/queues/manager.py` to confirm line numbers before editing.
   If the file has drifted, update the contract here before coding.
3. Follow the pattern in "Implementation Notes". Use `self.logger` with the
   exact prefixes shown (tests grep for `"near limit"`, `"grew"`, `"ceiling"`,
   `"shrunk"`).
4. Run `source .venv/bin/activate && pytest tests/test_queue_manager_dynamic.py
   tests/ -v`. All tests (new + existing) must pass.
5. Move this file to `sdd/tasks/completed/TASK-018-queue-manager-dynamic-sizing.md`
   and update `.index.json`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
