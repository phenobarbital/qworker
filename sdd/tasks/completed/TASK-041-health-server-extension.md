# TASK-041: Health Server Extension

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-037, TASK-039
**Assigned-to**: unassigned

---

## Context

Extends the existing HealthServer (`/supervisor/status` endpoint) to report
container backend status: connection state, active containers/pods, overflow
state, and resource monitor metrics.

Implements Spec Section 3 (Module 10). Final task in FEAT-006.

---

## Scope

- Modify `qw/health.py`:
  - `HealthServer.__init__` — accept optional `backend_dispatcher: BackendDispatcher`
  - `_supervisor_status()` — extend response with:
    - `backends`: dict of backend health statuses (from `health_check()`)
    - `overflow`: current overflow state from ResourceMonitor
    - `memory_percent`: current RAM usage
  - Only include backend info if dispatcher is wired in (backward compatible)
- Modify `qw/server.py` (or wherever HealthServer is instantiated):
  - Pass BackendDispatcher to HealthServer when available
- Write unit tests

**NOT in scope**: New endpoints (keep existing `/supervisor/status`)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/health.py` | MODIFY | Extend _supervisor_status with backend info |
| `tests/test_health_backends.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
# Extend the existing _supervisor_status method (health.py:229-293)
# Add backend info at the end of the response dict:

def _supervisor_status(self) -> tuple[str, str]:
    # ... existing worker status code ...

    # NEW: backend status (FEAT-006)
    backend_info = {}
    if self._dispatcher:
        try:
            # Note: health_check() is async but _supervisor_status is sync
            # Use asyncio.get_event_loop().run_until_complete() or
            # store cached health status updated periodically
            backend_info = {
                "overflow_active": self._dispatcher.is_overflowing(),
                "memory_percent": self._dispatcher.get_memory_percent(),
                "backends": self._dispatcher.get_health_summary(),
            }
        except Exception:
            backend_info = {"error": "failed to read backend status"}

    body = json_encoder({
        "workers": workers,
        "backends": backend_info,  # NEW
    })
    return HTTP_200, body
```

### Key Constraints
- Backward compatible: if no dispatcher, response is identical to current
- The `_supervisor_status` method is synchronous — must handle async health_check calls
  (cache results from a periodic background task, or use sync wrappers)
- Only extend the existing endpoint, don't add new routes
- Match existing response format (JSON, HTTP 200/503)

### References in Codebase
```python
# qw/health.py:64 — HealthServer class
class HealthServer:
    def __init__(
        self,
        queue: QueueManager,
        host: str = "0.0.0.0",
        port: int = 8080,
        worker_name: str = "",
        shared_state=None,
    ):

# qw/health.py:229-293 — _supervisor_status (current implementation)
def _supervisor_status(self) -> tuple[str, str]:
    # Returns {"workers": {...}} with per-worker status
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/health.py:64-91
class HealthServer:
    def __init__(
        self,
        queue: QueueManager,
        host: str = "0.0.0.0",
        port: int = 8080,
        worker_name: str = "",
        shared_state=None,
    ):
        self._queue = queue
        self._host = host
        self._port = port
        self._worker_name = worker_name
        self._shared_state = shared_state
        self._server: Optional[asyncio.AbstractServer] = None
        self.logger = logging.getLogger("QW.HealthServer")

# qw/health.py:229
    def _supervisor_status(self) -> tuple[str, str]:
        # Returns (status_code, json_body)
        # Body: {"workers": {name: {pid, status, heartbeat_age_s, ...}}}

# qw/health.py:158
    def _route(self, path: str) -> tuple[str, str]:
        if path in ("/health", "/health/ready"):
            return self._readiness()
        elif path == "/health/live":
            return self._liveness()
        elif path == "/supervisor/status":
            return self._supervisor_status()
```

### Does NOT Exist
- No `self._dispatcher` in HealthServer — add it
- No backend status in /supervisor/status response
- No memory_percent in any health response
- HealthServer `_supervisor_status` is SYNC, not async — health_check() on backends
  is async, so need a caching strategy

---

## Acceptance Criteria

- [ ] `/supervisor/status` includes `"backends"` key when dispatcher available
- [ ] `"backends"` includes `overflow_active`, `memory_percent`, per-backend health
- [ ] Without dispatcher, response is identical to current (backward compatible)
- [ ] No crash if backend health_check fails
- [ ] All tests pass: `pytest tests/test_health_backends.py -v`
- [ ] Existing health tests still pass (no regression)

---

## Test Specification

```python
import pytest
from unittest.mock import MagicMock
from qw.health import HealthServer


class TestHealthServerBackends:
    @pytest.fixture
    def health_with_dispatcher(self):
        queue = MagicMock()
        queue.snapshot.return_value = {
            "size": 1, "max_size": 4, "base_size": 4,
            "grow_margin": 2, "ceiling": 6, "grow_events": 0,
            "discard_events": 0, "full": False,
            "consumer_alive": 3, "consumer_total": 3,
            "respawn_events": 0,
        }
        dispatcher = MagicMock()
        dispatcher.is_overflowing.return_value = False
        dispatcher.get_memory_percent.return_value = 45.2
        dispatcher.get_health_summary.return_value = {
            "local": {"status": "ok"},
            "docker": {"status": "connected"},
        }
        return HealthServer(
            queue=queue,
            worker_name="test-worker",
            # Pass dispatcher via new param
        )

    def test_supervisor_status_without_dispatcher(self):
        queue = MagicMock()
        hs = HealthServer(queue=queue, worker_name="test")
        status, body = hs._supervisor_status()
        # Should not have "backends" key or it should be empty

    def test_supervisor_status_with_dispatcher(self, health_with_dispatcher):
        pass  # Verify backends key in response
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-037 and TASK-039 are completed
3. **Read** `qw/health.py` fully for current implementation
4. **Modify** `HealthServer.__init__` and `_supervisor_status`
5. **Run tests**: `pytest tests/test_health_backends.py -v`
6. **Run existing tests** to verify no regression
7. **Verify** acceptance criteria
8. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (sdd-worker)
**Date**: 2026-05-26
**Notes**: All 11 tests pass. HealthServer now accepts optional backend_dispatcher param. _supervisor_status includes a "backends" key when dispatcher is wired in, with overflow_active, memory_percent, and per-backend status dict. All 269 existing tests pass (no regression). Synchronous _get_backend_status() uses attribute access instead of async health_check() to avoid event loop issues in the sync _supervisor_status handler.
**Deviations from spec**: Used attribute inspection (_monitor, _local, _docker, _k8s) instead of helper methods (is_overflowing/get_memory_percent/get_health_summary) on the dispatcher — these methods don't exist on BackendDispatcher. The _get_backend_status() directly accesses the dispatcher's internal attributes for sync operation.
