# TASK-037: Resource Monitor

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-040
**Assigned-to**: unassigned

---

## Context

Monitors local RAM usage via `psutil` and provides overflow/recovery decisions
with hysteresis logic. Used by the BackendDispatcher (TASK-039) to decide when
to route tasks to K8s instead of local execution.

Implements Spec Section 3 (Module 6).

---

## Scope

- Implement `qw/backends/monitor.py` with `ResourceMonitor`:
  - `get_memory_percent() -> float` — current RAM usage (0-100) via `psutil.virtual_memory()`
  - `should_overflow() -> bool` — True when RAM > high threshold AND not already overflowing
  - `should_recover() -> bool` — True when RAM < low threshold AND currently overflowing
  - Internal hysteresis state: `_overflowing: bool`
  - Configurable thresholds from `RESOURCE_THRESHOLD` (high, existing) and
    `RESOURCE_RECOVER_THRESHOLD` (low, new from TASK-040)
- Handle optional import: if `psutil` not installed, `should_overflow()` always returns False
- Write unit tests with mock memory readings

**NOT in scope**: Backend dispatcher integration (TASK-039), config vars (TASK-040 creates them)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/monitor.py` | CREATE | ResourceMonitor class |
| `qw/backends/__init__.py` | MODIFY | Export ResourceMonitor |
| `tests/test_resource_monitor.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

class ResourceMonitor:
    def __init__(
        self,
        high_threshold: float = 90.0,
        low_threshold: float = 75.0,
    ):
        self.logger = logging.getLogger('QW.Backend.Monitor')
        self._high = high_threshold
        self._low = low_threshold
        self._overflowing: bool = False
        self._override_memory: float | None = None  # For testing

    def get_memory_percent(self) -> float:
        if self._override_memory is not None:
            return self._override_memory
        if not HAS_PSUTIL:
            return 0.0  # Can't measure — assume no pressure
        return psutil.virtual_memory().percent

    def should_overflow(self) -> bool:
        if not HAS_PSUTIL and self._override_memory is None:
            return False
        mem = self.get_memory_percent()
        if not self._overflowing and mem >= self._high:
            self._overflowing = True
            self.logger.warning(
                "RAM at %.1f%% (>= %.1f%%) — overflow activated",
                mem, self._high,
            )
        return self._overflowing

    def should_recover(self) -> bool:
        if self._overflowing:
            mem = self.get_memory_percent()
            if mem <= self._low:
                self._overflowing = False
                self.logger.info(
                    "RAM at %.1f%% (<= %.1f%%) — overflow deactivated",
                    mem, self._low,
                )
                return True
        return False
```

### Key Constraints
- `psutil.virtual_memory().percent` respects cgroup limits on modern kernels
- Hysteresis: once overflowing, stays overflowing until RAM < low threshold
- Between thresholds, state doesn't change (prevents flapping)
- `psutil` is optional — graceful degradation
- Must support `_override_memory` for testing

### References in Codebase
```python
# qw/conf.py:35-36 — existing config vars
RESOURCE_THRESHOLD = config.getint('RESOURCE_THRESHOLD', fallback=90)
CHECK_RESOURCE_USAGE = config.getboolean('CHECK_RESOURCE_USAGE', fallback=True)

# New (TASK-040 will add):
# RESOURCE_RECOVER_THRESHOLD = config.getint('RESOURCE_RECOVER_THRESHOLD', fallback=75)
# RESOURCE_OVERFLOW_ENABLED = config.getboolean('RESOURCE_OVERFLOW_ENABLED', fallback=False)
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/conf.py:35
RESOURCE_THRESHOLD = config.getint('RESOURCE_THRESHOLD', fallback=90)
# qw/conf.py:36
CHECK_RESOURCE_USAGE = config.getboolean('CHECK_RESOURCE_USAGE', fallback=True)
```

### Does NOT Exist
- No `psutil` in current dependencies — optional extra
- No existing resource monitoring in `qw/` — `RESOURCE_THRESHOLD` is defined but unused
- `RESOURCE_RECOVER_THRESHOLD` does NOT exist yet — TASK-040 adds it
- No `qw/monitor.py` or `qw/utils/monitor.py` — put in `qw/backends/monitor.py`

---

## Acceptance Criteria

- [ ] `from qw.backends.monitor import ResourceMonitor`
- [ ] `should_overflow()` returns True when memory > high threshold
- [ ] `should_recover()` returns True when memory < low threshold after overflow
- [ ] Hysteresis: between thresholds, state doesn't flip
- [ ] Without psutil: `should_overflow()` returns False, no crash
- [ ] `_override_memory` allows testing without real psutil
- [ ] All tests pass: `pytest tests/test_resource_monitor.py -v`

---

## Test Specification

```python
import pytest
from qw.backends.monitor import ResourceMonitor


class TestResourceMonitor:
    @pytest.fixture
    def monitor(self):
        m = ResourceMonitor(high_threshold=90.0, low_threshold=75.0)
        m._override_memory = 50.0
        return m

    def test_no_overflow_at_low_memory(self, monitor):
        monitor._override_memory = 50.0
        assert monitor.should_overflow() is False

    def test_overflow_at_high_memory(self, monitor):
        monitor._override_memory = 95.0
        assert monitor.should_overflow() is True

    def test_stays_overflowing_between_thresholds(self, monitor):
        monitor._override_memory = 95.0
        monitor.should_overflow()  # triggers overflow
        monitor._override_memory = 80.0  # between 75-90
        assert monitor.should_overflow() is True  # still overflowing
        assert monitor.should_recover() is False

    def test_recovers_below_low_threshold(self, monitor):
        monitor._override_memory = 95.0
        monitor.should_overflow()  # triggers overflow
        monitor._override_memory = 70.0
        assert monitor.should_recover() is True
        assert monitor.should_overflow() is False

    def test_no_flap(self, monitor):
        monitor._override_memory = 95.0
        monitor.should_overflow()
        monitor._override_memory = 80.0
        monitor.should_recover()  # False — still above 75
        assert monitor._overflowing is True

    def test_memory_percent(self, monitor):
        monitor._override_memory = 42.5
        assert monitor.get_memory_percent() == 42.5
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-040 is completed (config vars exist)
3. **Implement** `qw/backends/monitor.py`
4. **Run tests**: `pytest tests/test_resource_monitor.py -v`
5. **Verify** acceptance criteria
6. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (sdd-worker)
**Date**: 2026-05-26
**Notes**: All 16 tests pass. ResourceMonitor implements hysteresis logic with HAS_PSUTIL guard for optional dependency. _override_memory enables testing without real psutil. Also added is_overflowing property and reset() method.
**Deviations from spec**: Added is_overflowing property and reset() method as convenient extras; they don't deviate from spec requirements.
