# TASK-017: Implement QueueSizePolicy dataclass + unit tests

**Feature**: dynamic-queue-sizing
**Spec**: `sdd/specs/dynamic-queue-sizing.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-016
**Assigned-to**: unassigned

---

## Context

Implements Module 1 of the spec. Pure decision logic — no asyncio, no I/O —
so it can be unit-tested exhaustively with simulated time. Every `put()` in
`QueueManager` (TASK-018) will consult this policy, so correctness here is
load-bearing.

---

## Scope

- Create `qw/queues/policy.py` with:
  - `QueueSizePolicy` dataclass exposing the attributes and methods listed in
    §2 "Data Models" and §2 "New Public Interfaces" of the spec.
  - Default values sourced from `qw/conf.py` (TASK-016 constants) via a
    `classmethod` constructor `from_config(base_size: int)`.
- Create `tests/test_queue_policy.py` covering every unit test row listed in
  §4 for Module 1.
- Ensure the policy is **pure**: no awaits, no I/O, no logging inside policy
  methods. `QueueManager` (TASK-018) will log around the decisions.

**NOT in scope**:
- Modifying `QueueManager` — TASK-018.
- Mutating `asyncio.Queue._maxsize` — TASK-018.
- Observability wiring — TASK-019.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/queues/policy.py` | CREATE | `QueueSizePolicy` dataclass |
| `qw/queues/__init__.py` | MODIFY | Re-export `QueueSizePolicy` alongside `QueueManager` |
| `tests/test_queue_policy.py` | CREATE | Unit tests for the policy |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Added in TASK-016 (verify before import):
from qw.conf import (
    WORKER_QUEUE_SIZE,           # qw/conf.py:19  (pre-existing)
    WORKER_QUEUE_GROW_MARGIN,    # qw/conf.py, added by TASK-016
    WORKER_QUEUE_WARN_THRESHOLD, # qw/conf.py, added by TASK-016
    WORKER_QUEUE_SHRINK_COOLDOWN # qw/conf.py, added by TASK-016
)

# Stdlib only for the policy itself:
from dataclasses import dataclass, field
import time
```

### Existing Signatures to Use
```python
# qw/queues/__init__.py  (verified 2026-04-16)
# Already re-exports QueueManager. Add QueueSizePolicy to the same list.

# Required dataclass skeleton (from spec §2 "Data Models"):
@dataclass
class QueueSizePolicy:
    base_size: int
    grow_margin: int = 2
    warn_ratio: float = 0.80
    shrink_cooldown: float = 30.0
    low_watermark_ratio: float = 0.50
    current_maxsize: int = 0
    grow_events: int = 0
    discard_events: int = 0
    _last_high_usage_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None: ...      # set current_maxsize = base_size
    def ceiling(self) -> int: ...             # base_size + grow_margin
    def should_warn(self, current_size: int) -> bool: ...
    def can_grow(self) -> bool: ...           # current_maxsize < ceiling()
    def can_shrink(self, current_size: int, now: float) -> bool: ...
    def register_grow(self) -> None: ...      # current_maxsize += 1; grow_events += 1
    def register_shrink(self) -> None: ...    # current_maxsize -= 1 (floor at base_size)
    def register_discard(self) -> None: ...   # discard_events += 1
    def note_high_usage(self, now: float) -> None: ...  # update _last_high_usage_at
```

### Does NOT Exist
- ~~`QueueSizePolicy.resize(int)`~~ — do not add a free-form resize method;
  use `register_grow` / `register_shrink` only.
- ~~`QueueSizePolicy.from_env()`~~ — use `from_config(base_size)` classmethod
  that reads `qw.conf` values, not environment directly.
- ~~Pydantic `BaseModel`~~ — use stdlib `dataclasses`. Spec §7 is explicit:
  "Dataclass, not Pydantic, for `QueueSizePolicy`".
- ~~`asyncio` anywhere in `policy.py`~~ — policy is synchronous-only.
- ~~`qw.queues.policy.QueueSizePolicy.logger`~~ — policy does not log.

---

## Implementation Notes

### Semantics (must match spec §2 exactly)

- `should_warn(size)`: returns `True` when `size / current_maxsize >=
  warn_ratio` **and** `current_maxsize > 0`. Callers use this to decide
  whether to emit a warning log; the policy itself does not track "warned
  already" — that's `QueueManager`'s job (hysteresis in TASK-018).
- `can_grow()`: returns `True` iff `current_maxsize < ceiling()`.
- `can_shrink(size, now)`: returns `True` iff ALL of:
  - `current_maxsize > base_size`
  - `size / current_maxsize <= low_watermark_ratio` (guard against /0)
  - `now - _last_high_usage_at >= shrink_cooldown`
- `register_grow()`: must refuse when `current_maxsize == ceiling()` — raise
  `RuntimeError("grow margin exhausted")`. `QueueManager` guards with
  `can_grow()` first; the raise is a safety net.
- `register_shrink()`: floors at `base_size`.
- `note_high_usage(now)`: called by `QueueManager` whenever size exceeds the
  low-water mark, so the shrink cooldown resets.

### Pattern to Follow
```python
# qw/queues/policy.py
"""Queue-size policy for dynamic growth/shrink decisions.

Pure synchronous logic; no asyncio and no I/O. The owning QueueManager
is responsible for mutating the underlying asyncio.Queue._maxsize and
for logging around the decisions returned by this policy.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import time


@dataclass
class QueueSizePolicy:
    base_size: int
    grow_margin: int = 2
    warn_ratio: float = 0.80
    shrink_cooldown: float = 30.0
    low_watermark_ratio: float = 0.50
    current_maxsize: int = 0
    grow_events: int = 0
    discard_events: int = 0
    _last_high_usage_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.base_size < 1:
            raise ValueError("base_size must be >= 1")
        if self.grow_margin < 0:
            raise ValueError("grow_margin must be >= 0")
        if not (0.0 < self.warn_ratio <= 1.0):
            raise ValueError("warn_ratio must be in (0, 1]")
        if not (0.0 < self.low_watermark_ratio < 1.0):
            raise ValueError("low_watermark_ratio must be in (0, 1)")
        self.current_maxsize = self.base_size

    @classmethod
    def from_config(cls, base_size: int) -> "QueueSizePolicy":
        from qw.conf import (
            WORKER_QUEUE_GROW_MARGIN,
            WORKER_QUEUE_WARN_THRESHOLD,
            WORKER_QUEUE_SHRINK_COOLDOWN,
        )
        return cls(
            base_size=base_size,
            grow_margin=WORKER_QUEUE_GROW_MARGIN,
            warn_ratio=WORKER_QUEUE_WARN_THRESHOLD,
            shrink_cooldown=float(WORKER_QUEUE_SHRINK_COOLDOWN),
        )
```

### Key Constraints
- Type hints on every attribute and return value.
- Keep methods O(1) — they run on the hot path.
- Do not import `qw.queues.manager` (would create a circular import).
- Do not cache policy instances at module level.

### References in Codebase
- Spec §2 "Data Models" — authoritative field list.
- Spec §7 — gotchas around concurrency and observability cardinality.

---

## Acceptance Criteria

- [ ] `from qw.queues import QueueSizePolicy` works.
- [ ] `QueueSizePolicy(base_size=4).current_maxsize == 4`.
- [ ] `QueueSizePolicy(base_size=4, grow_margin=2).ceiling() == 6`.
- [ ] Invalid arguments raise `ValueError` during `__post_init__`.
- [ ] All unit tests in `tests/test_queue_policy.py` pass.
- [ ] `pytest tests/test_queue_policy.py -v` shows zero failures.
- [ ] No asyncio, no logging, no navconfig imports inside `policy.py` itself
      (only `from_config` may reach into `qw.conf`).

---

## Test Specification

```python
# tests/test_queue_policy.py
import pytest
from qw.queues import QueueSizePolicy


@pytest.fixture
def policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4,
        grow_margin=2,
        warn_ratio=0.8,
        shrink_cooldown=10.0,
        low_watermark_ratio=0.5,
    )


class TestQueueSizePolicy:
    def test_policy_default_initial_maxsize(self, policy):
        assert policy.current_maxsize == 4
        assert policy.ceiling() == 6

    def test_policy_should_warn_at_threshold(self, policy):
        # maxsize=4, warn_ratio=0.8 → warn at size >= 4 (since 3/4=0.75 < 0.8)
        assert policy.should_warn(3) is False
        assert policy.should_warn(4) is True

    def test_policy_can_grow_until_margin(self, policy):
        assert policy.can_grow() is True
        policy.register_grow()
        assert policy.current_maxsize == 5
        policy.register_grow()
        assert policy.current_maxsize == 6
        assert policy.can_grow() is False

    def test_policy_register_grow_past_ceiling_raises(self, policy):
        policy.register_grow()
        policy.register_grow()
        with pytest.raises(RuntimeError):
            policy.register_grow()

    def test_policy_can_shrink_after_cooldown(self, policy):
        policy.register_grow()  # maxsize=5
        # Simulate time past cooldown AND size below low watermark
        policy.note_high_usage(now=0.0)
        # size=2, maxsize=5 → ratio=0.4 <= 0.5, elapsed=11.0 > 10.0
        assert policy.can_shrink(current_size=2, now=11.0) is True

    def test_policy_cannot_shrink_before_cooldown(self, policy):
        policy.register_grow()
        policy.note_high_usage(now=0.0)
        assert policy.can_shrink(current_size=2, now=5.0) is False  # elapsed=5 < 10

    def test_policy_cannot_shrink_at_base(self, policy):
        # current_maxsize == base_size, can_shrink must be False
        assert policy.can_shrink(current_size=0, now=1e9) is False

    def test_policy_register_shrink_floors_at_base(self, policy):
        policy.register_grow()
        policy.register_shrink()
        policy.register_shrink()  # already at base, should not go below
        assert policy.current_maxsize == policy.base_size

    def test_policy_counters(self, policy):
        policy.register_grow()
        policy.register_discard()
        assert policy.grow_events == 1
        assert policy.discard_events == 1

    def test_policy_rejects_invalid_config(self):
        with pytest.raises(ValueError):
            QueueSizePolicy(base_size=0)
        with pytest.raises(ValueError):
            QueueSizePolicy(base_size=4, grow_margin=-1)
        with pytest.raises(ValueError):
            QueueSizePolicy(base_size=4, warn_ratio=1.5)

    def test_policy_from_config_picks_up_defaults(self, monkeypatch):
        import qw.conf as c
        monkeypatch.setattr(c, "WORKER_QUEUE_GROW_MARGIN", 3, raising=False)
        monkeypatch.setattr(c, "WORKER_QUEUE_WARN_THRESHOLD", 0.9, raising=False)
        monkeypatch.setattr(c, "WORKER_QUEUE_SHRINK_COOLDOWN", 5, raising=False)
        p = QueueSizePolicy.from_config(base_size=4)
        assert p.grow_margin == 3
        assert p.warn_ratio == 0.9
        assert p.shrink_cooldown == 5.0
```

---

## Agent Instructions

When you pick up this task:

1. Confirm TASK-016 is done (the three new `WORKER_QUEUE_*` constants exist
   in `qw/conf.py`). If not, do not proceed.
2. Read spec §2 "Data Models", §2 "New Public Interfaces", §4 (Module 1 rows),
   and §7 for constraints.
3. Create `qw/queues/policy.py`. Keep it pure (no asyncio imports, no logging).
4. Add `QueueSizePolicy` to `qw/queues/__init__.py` exports alongside
   `QueueManager`.
5. Write `tests/test_queue_policy.py` with every test in the specification
   above. Add further edge-case tests if useful.
6. Run `source .venv/bin/activate && pytest tests/test_queue_policy.py -v`.
7. Move this file to `sdd/tasks/completed/TASK-017-queue-size-policy.md`
   and update `.index.json`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
