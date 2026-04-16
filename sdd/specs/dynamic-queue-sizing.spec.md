# Feature Specification: Dynamic Queue Sizing

**Feature ID**: FEAT-003
**Date**: 2026-04-16
**Author**: Jesus Lara
**Status**: draft
**Target version**: 0.x (next minor)

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement

Each `QWorker` process owns a single `asyncio.Queue` with a fixed maximum size
fixed at startup from the `WORKER_QUEUE_SIZE` environment variable (default `4`,
see `qw/conf.py:19`). When a worker is under sustained enqueue pressure — bursts
of Redis stream tasks, parallel direct-TCP submissions, or broker spikes — the
queue fills up and `QueueManager.put()` raises `asyncio.QueueFull` at
`qw/queues/manager.py:123`, causing the task to be **discarded immediately**
with no warning window for the operator.

Three problems with this today:

1. **No elasticity**: a one-time burst of 5 tasks on a `maxsize=4` queue drops
   the 5th task outright, even when the worker could have absorbed it.
2. **No early warning**: tasks only start failing after the ceiling is hit;
   operators have no "80 % full" alert.
3. **No observability of pressure**: `check_state` (`qw/server.py:488-494`) and
   the health endpoint (`qw/health.py:138-154`) report `max_size` statically
   from `WORKER_QUEUE_SIZE`, so a grown queue is indistinguishable from the
   base configuration.

This feature introduces a **soft grow policy**: under pressure, the queue
temporarily expands up to a bounded margin (e.g. `+2` extra slots), logs a
warning as it approaches the hard ceiling, and shrinks back when pressure
subsides.

**Who is affected**: operators running qworker in production; any service
producing bursty task traffic into a single worker.

**Why now**: with FEAT-002 (observability) exposing queue depth via `qw info`,
the lack of elasticity becomes visible at exactly the moment the operator
asks "why are we dropping tasks?".

### Goals

- Allow `QueueManager` to temporarily grow its `asyncio.Queue` `maxsize` beyond
  the configured `WORKER_QUEUE_SIZE` by a bounded margin (default `+2`).
- Log a **warning** when the queue reaches a high-water mark (default `80 %`
  of current effective size) so operators have a lead time before discards.
- Log a distinct **error** when the queue reaches the absolute ceiling
  (base size + margin) and new tasks will be discarded.
- Shrink `maxsize` back toward the configured base when the queue has been
  idle/low-occupancy for a cooldown window (default `30 s`).
- Expose current effective `maxsize`, base size, margin, and grow events via
  the existing state-tracking surfaces (`check_state`, health endpoint,
  StateTracker).
- Keep the change backward-compatible: with margin `0`, behaviour is identical
  to today.

### Non-Goals (explicitly out of scope)

- Scaling the **number of worker processes** (autoscaling). This feature only
  resizes the in-process asyncio queue, not the worker pool.
- Cross-worker load balancing. Each `QueueManager` makes independent grow
  decisions based solely on its own queue state.
- Persistence of grow state across restarts. Margin and grow decisions reset
  on each worker boot.
- Adding a new CLI subcommand. Observability reuses existing `check_state`
  and the `qw info` output from FEAT-002.
- Resizing `BROKER_MANAGER_QUEUE_SIZE` (`qw/broker/producer.py:49-53`) — this
  spec is scoped to the per-worker `QueueManager` only.

---

## 2. Architectural Design

### Overview

`QueueManager` gains a small internal policy object — `QueueSizePolicy` —
that tracks three thresholds (warn, grow, ceiling) and decides on each
`put()` whether to:

- allow the put under the current `maxsize`,
- **grow** the queue by 1 slot (up to the configured margin) and log a warning,
- **discard** (re-raise `QueueFull`) once the ceiling is reached.

A background coroutine (`_shrink_monitor`) runs on the worker event loop and,
when the queue has stayed below the low-water mark for `cooldown_seconds`,
decreases `maxsize` by 1 slot until it reaches the configured base size.

Growing and shrinking mutate the private `asyncio.Queue._maxsize` attribute
and, on grow, wake any pending putters via `_wakeup_next(self.queue._putters)`.
This is a controlled use of private asyncio internals; see
§7 *Known Risks / Gotchas*.

### Component Diagram

```
QueueManager.put(task)
        │
        ▼
QueueSizePolicy.admit()
        │
        ├── room available         ──→ queue.put_nowait(task)         (normal)
        │
        ├── at warn threshold      ──→ log.warning(...)               (soft alert)
        │                              queue.put_nowait(task)
        │
        ├── full, margin available ──→ policy.grow() → _maxsize += 1
        │                              queue.put_nowait(task)
        │                              log.warning("queue grew to N/M")
        │
        └── full, no margin left   ──→ raise QueueFull                (discard)
                                       log.error("ceiling reached")

QueueManager._shrink_monitor()  (background task)
        │
        ▼
every shrink_interval:
    if size <= low_watermark for cooldown_seconds:
        policy.shrink() → _maxsize -= 1
        log.info("queue shrunk back to N")
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QueueManager` (`qw/queues/manager.py:31`) | extends | Constructor gains policy; `put()` consults it; `fire_consumers()` starts `_shrink_monitor`. |
| `WORKER_QUEUE_SIZE` (`qw/conf.py:19`) | reused | Remains the **base** size. |
| New config: `WORKER_QUEUE_GROW_MARGIN`, `WORKER_QUEUE_WARN_THRESHOLD`, `WORKER_QUEUE_SHRINK_COOLDOWN` | new | Added in `qw/conf.py`. |
| `StateTracker` (`qw/state.py:13`) | extends | New optional counters: `grow_events`, `discard_events`, `current_maxsize`. |
| `QWorker.worker_check_state()` (`qw/server.py:476-499`) | modifies | Report current effective `maxsize` + base + margin (replace hardcoded `WORKER_QUEUE_SIZE`). |
| `HealthServer._readiness()` (`qw/health.py:138-154`) | modifies | Include dynamic `maxsize` in body; readiness considers ceiling, not base. |

### Data Models

```python
# qw/queues/policy.py  (new module)
from dataclasses import dataclass, field
import time

@dataclass
class QueueSizePolicy:
    """Decides when to grow/shrink the underlying asyncio.Queue."""

    base_size: int                         # configured WORKER_QUEUE_SIZE
    grow_margin: int = 2                   # extra slots allowed above base
    warn_ratio: float = 0.80               # warn when size/maxsize >= this
    shrink_cooldown: float = 30.0          # seconds of low usage before shrink
    low_watermark_ratio: float = 0.50      # "low usage" threshold

    # --- runtime state ---
    current_maxsize: int = 0               # set from base_size in __post_init__
    grow_events: int = 0
    discard_events: int = 0
    _last_high_usage_at: float = field(default_factory=time.monotonic)

    def ceiling(self) -> int:
        return self.base_size + self.grow_margin

    def should_warn(self, current_size: int) -> bool: ...
    def can_grow(self) -> bool: ...
    def can_shrink(self, current_size: int, now: float) -> bool: ...
```

### New Public Interfaces

```python
# qw/queues/manager.py (modified)
class QueueManager:
    def __init__(
        self,
        worker_name: str,
        state_tracker=None,
        policy: "QueueSizePolicy | None" = None,  # new, optional
    ):
        ...

    # Public read-only accessors for observability
    @property
    def max_size(self) -> int:
        """Current effective maxsize (base + any grown slots)."""

    @property
    def base_size(self) -> int:
        """The configured WORKER_QUEUE_SIZE — never changes at runtime."""

    @property
    def grow_margin(self) -> int:
        """Max additional slots the queue may grow into."""

    def snapshot(self) -> dict:
        """Pressure snapshot for observability endpoints."""
        # {"size", "max_size", "base_size", "grow_margin", "ceiling",
        #  "grow_events", "discard_events", "full"}
```

Nothing else in `QueueManager`'s public surface changes — `put()`, `get()`,
`size()`, `full()`, `empty()`, `fire_consumers()`, `empty_queue()` keep their
existing signatures.

---

## 3. Module Breakdown

### Module 1: `qw/queues/policy.py`
- **Path**: `qw/queues/policy.py` (new file)
- **Responsibility**: Dataclass `QueueSizePolicy` encapsulating thresholds
  and decision methods (`should_warn`, `can_grow`, `can_shrink`,
  `register_grow`, `register_discard`). Pure logic, no asyncio dependencies.
- **Depends on**: nothing (stdlib `dataclasses`, `time`).

### Module 2: `qw/queues/manager.py` (modified)
- **Path**: `qw/queues/manager.py`
- **Responsibility**:
  1. Accept an optional `policy: QueueSizePolicy` in `__init__`.
  2. In `put()`, consult the policy:
     - warn at `warn_ratio`;
     - on `QueueFull`, attempt grow-and-retry if margin remains;
     - otherwise re-raise after bumping `discard_events`.
  3. Add `_shrink_monitor()` coroutine started from `fire_consumers()`.
  4. Expose `max_size`, `base_size`, `grow_margin`, and `snapshot()`.
- **Depends on**: Module 1.

### Module 3: `qw/conf.py` (modified)
- **Path**: `qw/conf.py`
- **Responsibility**: Add three new env-backed settings:
  - `WORKER_QUEUE_GROW_MARGIN` (int, default `2`)
  - `WORKER_QUEUE_WARN_THRESHOLD` (float, default `0.80`)
  - `WORKER_QUEUE_SHRINK_COOLDOWN` (int, default `30`)
- **Depends on**: none.

### Module 4: Observability surfaces (modified)
- **Path**: `qw/server.py` (`worker_check_state`), `qw/health.py`
  (`_readiness`), `qw/state.py` (optional counters).
- **Responsibility**: Replace hardcoded `WORKER_QUEUE_SIZE` with
  `self.queue.max_size`; add `base_size`, `grow_margin`, `grow_events`,
  `discard_events` to responses.
- **Depends on**: Module 2.

### Module 5: Tests
- **Path**: `tests/queues/test_policy.py`, `tests/queues/test_manager_dynamic.py`
- **Responsibility**: Unit + integration tests as specified in §4.
- **Depends on**: Modules 1 & 2.

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_policy_default_initial_maxsize` | Module 1 | `QueueSizePolicy(base_size=4)` → `current_maxsize == 4`, `ceiling == 6`. |
| `test_policy_should_warn_at_threshold` | Module 1 | With `base_size=4`, `warn_ratio=0.8`: warns at size ≥ 4 (4/5 = 0.8). |
| `test_policy_can_grow_until_margin` | Module 1 | Grow 2 times with margin=2; third call returns `False`. |
| `test_policy_can_shrink_after_cooldown` | Module 1 | Simulated time past `shrink_cooldown` + low usage → `True`. |
| `test_policy_counters` | Module 1 | `register_grow` / `register_discard` increment counters. |
| `test_manager_put_warns_at_threshold` | Module 2 | Caplog captures warning at 80 % fill. |
| `test_manager_put_grows_on_full` | Module 2 | With `margin=2`, `base=4`: 5th put succeeds; `max_size == 5`. |
| `test_manager_put_discards_at_ceiling` | Module 2 | 7th put raises `QueueFull`; `discard_events == 1`. |
| `test_manager_backward_compat_margin_zero` | Module 2 | With `margin=0`, 5th put raises immediately (today's behaviour). |
| `test_manager_shrinks_after_cooldown` | Module 2 | After cooldown with low usage, `max_size` returns to `base_size`. |
| `test_manager_snapshot_fields` | Module 2 | `snapshot()` contains all documented keys. |

### Integration Tests

| Test | Description |
|---|---|
| `test_worker_check_state_reports_dynamic_maxsize` | Start a worker with a grown queue; `check_state` reply includes `base_size`, `grow_margin`, and `max_size` > base. |
| `test_health_readiness_uses_ceiling` | Fill queue to base; `/health/ready` still returns 200 if margin is available; returns 503 only at ceiling. |
| `test_concurrent_put_never_exceeds_ceiling` | 20 concurrent puts on `base=4 margin=2`: at most 6 succeed, rest raise `QueueFull`; no race condition grows past ceiling. |

### Test Data / Fixtures

```python
# tests/queues/conftest.py
import pytest
from qw.queues.policy import QueueSizePolicy
from qw.queues.manager import QueueManager

@pytest.fixture
def small_policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4,
        grow_margin=2,
        warn_ratio=0.8,
        shrink_cooldown=0.1,
        low_watermark_ratio=0.5,
    )

@pytest.fixture
def manager(small_policy) -> QueueManager:
    return QueueManager(worker_name="test", policy=small_policy)
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `QueueSizePolicy` dataclass exists in `qw/queues/policy.py` with the
      public attributes listed in §2.
- [ ] `QueueManager.__init__` accepts an optional `policy` parameter and
      falls back to defaults built from `qw/conf.py`.
- [ ] When `WORKER_QUEUE_GROW_MARGIN=0`, behaviour is **byte-identical** to
      today (verified by existing test suite passing unchanged).
- [ ] With `base=4 margin=2`: a sequence of 6 rapid `put()` calls all succeed;
      the 7th raises `asyncio.QueueFull`.
- [ ] A warning is logged exactly once per growth event, and once per
      discard event, each with the task id and current `size/max_size`.
- [ ] After a quiet window ≥ `shrink_cooldown` with size below the low
      watermark, `QueueManager.max_size` shrinks back to `base_size`.
- [ ] `check_state` TCP response and the `/health/ready` HTTP body both
      include `base_size`, `grow_margin`, `max_size`, and `grow_events`.
- [ ] All new unit tests pass (`pytest tests/queues/ -v`).
- [ ] All existing tests pass unchanged.
- [ ] No breaking changes to `QueueManager`'s public method signatures.
- [ ] No regressions in task throughput benchmarks (if present); policy
      overhead on `put()` must be < 50 µs on the reference machine.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# Confirmed by reading the listed files on 2026-04-16:
from qw.conf import WORKER_QUEUE_SIZE                 # qw/conf.py:19
from qw.conf import WORKER_RETRY_INTERVAL             # qw/conf.py:22
from qw.conf import WORKER_RETRY_COUNT                # qw/conf.py:23
from qw.conf import WORKER_QUEUE_CALLBACK             # qw/conf.py:32
from qw.queues import QueueManager                    # qw/queues/__init__.py re-export
from qw.wrappers.base import QueueWrapper             # qw/wrappers/base.py
from qw.executor import TaskExecutor                  # qw/executor/__init__.py:16
from qw.state import StateTracker                     # qw/state.py:13
from navconfig.logging import logging                 # used in qw/queues/manager.py:6
```

### Existing Class Signatures

```python
# qw/queues/manager.py  (verified by reading the file in full)
class QueueManager:
    def __init__(self, worker_name: str, state_tracker=None):        # line 35
        self.logger = logging.getLogger('QW.Queue')                  # line 36
        self.worker_name = worker_name                               # line 37
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=WORKER_QUEUE_SIZE                                # line 39
        )
        self.consumers: list = []                                    # line 41
        self._callback: Union[Callable, Awaitable] = ...             # line 46
        self._state = state_tracker                                  # line 53

    def size(self) -> int:                                            # line 75
    def empty(self) -> bool:                                          # line 78
    def full(self) -> bool:                                           # line 81
    async def fire_consumers(self) -> None:                           # line 84
        # spawns (WORKER_QUEUE_SIZE - 1) queue_handler tasks          # line 86
    async def empty_queue(self) -> None:                              # line 92
    async def put(self, task: QueueWrapper, id: str) -> bool:         # line 105
        # raises asyncio.queues.QueueFull at line 123
    async def get(self) -> QueueWrapper:                              # line 129
    async def queue_handler(self) -> None:                            # line 142
```

```python
# qw/state.py  (verified by reading the file in full)
class StateTracker:
    MAX_COMPLETED = 10                                                # line 25
    def __init__(self, shared_state: dict, worker_name: str,
                 pid: int) -> None:                                   # line 27
    def _get_function_name(self, task) -> str:                        # line 45
    def task_queued(self, task_id: str, function_name: str) -> None:  # line 98
    def task_executing(self, task_id: str, source: str) -> None:      # line 122
    def task_completed(self, task_id: str, result: str,
                       source: str) -> None:                          # line 167
    def get_state(self) -> dict:                                      # line 220
    def get_all_states(self) -> dict:                                 # line 226
```

```python
# qw/server.py  (verified — relevant excerpts)
class QWorker:
    def __init__(self, ..., shared_state=None): ...                   # line 66
        self.queue = None                                             # line 83
    async def start(self) -> None:                                    # line 332
        self.queue = QueueManager(
            worker_name=self._name, state_tracker=self._state)        # line 336
    async def worker_check_state(
        self, writer: asyncio.StreamWriter) -> None:                  # line 476
        # line 489: "max_size": WORKER_QUEUE_SIZE  ← HARDCODED, must change
```

```python
# qw/health.py  (verified — relevant excerpts)
class HealthServer:
    def _readiness(self) -> tuple[str, str]:                          # line 138
        queue_full = self._queue.full()                               # line 140
        queue_size = self._queue.size()                               # line 141
        # body only reports "size" and "full"; must add max_size fields
```

```python
# qw/conf.py  (verified)
WORKER_QUEUE_SIZE = config.getint('WORKER_QUEUE_SIZE', fallback=4)    # line 19
# No existing WORKER_QUEUE_GROW_* settings.
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `QueueSizePolicy` | `QueueManager.__init__` | constructor kwarg `policy` | new, added in Module 2 |
| `QueueManager.put()` grow path | `QueueSizePolicy.can_grow()` + `asyncio.Queue._maxsize` | direct attribute mutation on the stdlib Queue | `asyncio.queues.Queue._maxsize` (CPython 3.9+ private attr) |
| `QueueManager._shrink_monitor` | `QueueManager.fire_consumers` | `asyncio.create_task` from inside `fire_consumers` | `qw/queues/manager.py:84` |
| `QWorker.worker_check_state` | `QueueManager.snapshot()` | method call replacing hardcoded dict | `qw/server.py:488-494` |
| `HealthServer._readiness` | `QueueManager.snapshot()` | method call | `qw/health.py:138-154` |

### Does NOT Exist (Anti-Hallucination)

- ~~`asyncio.Queue.resize()`~~ — **does not exist** in stdlib. Resizing must
  mutate the private `_maxsize` attribute directly.
- ~~`asyncio.Queue.set_maxsize()`~~ — **does not exist**.
- ~~`QueueManager.max_size` property~~ — does not exist yet, added by this spec.
- ~~`QueueManager.base_size`~~ — does not exist yet, added by this spec.
- ~~`QueueManager.snapshot()`~~ — does not exist yet, added by this spec.
- ~~`QueueManager._shrink_monitor`~~ — does not exist yet, added by this spec.
- ~~`QueueManager.policy`~~ — does not exist yet, added by this spec.
- ~~`WORKER_QUEUE_GROW_MARGIN` / `WORKER_QUEUE_WARN_THRESHOLD` /
  `WORKER_QUEUE_SHRINK_COOLDOWN`~~ — not in `qw/conf.py` today; added by
  this spec.
- ~~`StateTracker.record_queue_grow()`~~ — not in `qw/state.py`. If added
  later, it must follow the full-value replacement pattern documented at
  `qw/state.py:7-9`.
- ~~`QueueManager.resize()`~~ — do not add a public resize API; resizing is
  an internal policy-driven decision, not user-facing.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Dataclass, not Pydantic**, for `QueueSizePolicy` — it has no I/O boundary
  and no validation beyond type hints; dataclasses are zero-overhead and
  match the async-hot-path use case.
- Keep policy methods **pure and synchronous** — no awaits inside decision
  code. The policy is consulted on every `put()`.
- Use `self.logger` (already instantiated at `qw/queues/manager.py:36`).
  Warning messages must include: task id, `size`, `max_size`, and whether
  this was a warn / grow / discard event, to make log grep trivial.
- Respect the StateTracker update pattern documented at `qw/state.py:7-9`:
  proxies require full-value replacement, not nested mutation.

### Known Risks / Gotchas

- **Private `_maxsize` mutation**. `asyncio.Queue` exposes no public resize
  API; we rely on `queue._maxsize = N`. CPython has kept this attribute
  stable since 3.4, but:
  1. Wrap the mutation in a single helper (`_apply_maxsize`) so any future
     upstream change has exactly one place to update.
  2. On grow, call `self.queue._wakeup_next(self.queue._putters)` to release
     one blocked putter (matching how `get_nowait` releases putters). Since
     this worker uses `put_nowait`, there are no blocked putters today, but
     this makes the helper safe for future users that might `await put()`.
  3. Document the private-API dependency in a module-level comment.
- **Race between grow and discard under concurrent puts**. Because the
  event loop is single-threaded, `put_nowait` + policy decision + `_maxsize`
  mutation is atomic from the loop's perspective. Tests must verify this
  on a real event loop (not mocked) — see
  `test_concurrent_put_never_exceeds_ceiling`.
- **Shrink while items queued**. `asyncio.Queue` does not reject a smaller
  `_maxsize` when `qsize() > _maxsize`; it just refuses new `put_nowait`
  until the size falls below. Our shrink policy only fires when size is
  already below the low watermark, so this is a non-issue, but the helper
  must still refuse to shrink below the current size.
- **Consumer count vs queue size**. `fire_consumers()` at line 86 starts
  `WORKER_QUEUE_SIZE - 1` consumer tasks. We deliberately **do not** spawn
  extra consumers on grow — growing absorbs bursts, it does not increase
  parallelism. The consumer count stays bound to the base size.
- **Observability cardinality**. Do not emit a log line per put at the warn
  threshold; emit once per transition (`under→over` and `over→under`) to
  avoid log spam. Counters in the policy make this easy.
- **Tests must avoid `time.sleep`**. Use `small_policy` with
  `shrink_cooldown=0.1` and `asyncio.sleep(0.15)` or
  `monkeypatch.setattr(time, "monotonic", ...)` for deterministic shrink
  assertions.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| (none) | — | Feature uses only stdlib (`asyncio`, `dataclasses`, `time`). |

---

## 8. Open Questions

- [ ] Should `grow_margin` be expressible as a ratio (e.g. `0.5` = 50 % of
      base) in addition to an integer, for deployments with large base
      sizes? — *Owner: Jesus*
- [ ] Should discard events also be published to a Redis stream (operator
      alerting) in addition to logs? — *Owner: Jesus*
- [ ] Should the policy consult CPU / memory (e.g. pause grow when
      `RESOURCE_THRESHOLD` exceeded, see `qw/conf.py:20-21`)? — *Owner: Jesus*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks in one worktree).
- **Rationale**: The policy and its consumer (`QueueManager`) are tightly
  coupled — Module 1 must exist before Module 2 can compile, and Modules 3-4
  depend on Module 2's new `snapshot()` API. Parallelising would cause
  constant merge churn for minimal wall-clock savings.
- **Task order** (enforced sequentially):
  1. Config constants (Module 3).
  2. `QueueSizePolicy` + unit tests (Module 1).
  3. `QueueManager` integration + unit tests (Module 2).
  4. Observability surfaces (Module 4) + integration tests.
  5. Documentation + changelog.
- **Cross-feature dependencies**: None blocking. FEAT-002
  (`expose-queue-threads`) is complementary — `qw info` will display the
  new `max_size`/`base_size` fields for free once `snapshot()` is wired
  into `worker_check_state`. If FEAT-002 merges first, nothing changes
  here; if this merges first, FEAT-002 picks up the richer fields.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-16 | Jesus Lara | Initial draft (scaffolded via /sdd-spec). |
