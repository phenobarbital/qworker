# TASK-030: HealthServer Draining Status & /supervisor/status Endpoint

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-025, TASK-027
**Assigned-to**: unassigned

---

## Context

> Implements Module 7 from the spec. Enhances the HealthServer to:
> 1. Return 503 on `/health/ready` when the worker is draining (so K8s/ALB
>    stops routing traffic).
> 2. Expose a new `/supervisor/status` endpoint returning per-worker health
>    data (status, heartbeat age, task_ledger depth) for Grafana dashboards.

---

## Scope

- Modify `HealthServer.__init__()` to accept an optional `shared_state`
  parameter and a `worker_status_fn` callable (or direct access to draining state)
- Modify `HealthServer._readiness()`:
  - Check if the worker is draining. If so, return 503 with
    `"status": "draining"` regardless of queue state.
- Modify `HealthServer._route()`:
  - Add route for path `/supervisor/status`
- Add `HealthServer._supervisor_status()` method:
  - Read `shared_state` for all workers
  - Return JSON with per-worker info: pid, status, heartbeat_age_s,
    draining_since (ISO format or null), task_ledger_depth, queue_size
- Modify `QWorker.start()` to pass `shared_state` to HealthServer constructor
- Write unit tests for draining readiness and the new endpoint

**NOT in scope**:
- The supervisor logic (TASK-028)
- The StateTracker methods (TASK-025)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/health.py` | MODIFY | Add `shared_state` param, draining check, `/supervisor/status` route |
| `qw/server.py` | MODIFY | Pass `shared_state` to HealthServer constructor (line 367) |
| `tests/test_health_supervisor.py` | CREATE | Unit tests for draining readiness and supervisor status endpoint |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.health import HealthServer              # verified: qw/health.py:39
from qw.queues import QueueManager              # verified: qw/queues/__init__.py:1
from datamodel.parsers.json import json_encoder  # verified: qw/health.py:18
import asyncio                                   # stdlib, already imported: qw/health.py:12
import time                                      # stdlib
```

### Existing Signatures to Use
```python
# qw/health.py:39
class HealthServer:
    def __init__(                                               # line 49
        self,
        queue: QueueManager,
        host: str = "0.0.0.0",
        port: int = 8080,
        worker_name: str = "",
    ):
        self._queue = queue                                     # line 56
        self._host = host                                       # line 57
        self._port = port                                       # line 58
        self._worker_name = worker_name                         # line 59
        self._server: Optional[asyncio.AbstractServer] = None   # line 60
        self.logger = logging.getLogger("QW.HealthServer")      # line 61

    async def start(self) -> None: ...                          # line 65
    async def stop(self) -> None: ...                           # line 77
    def _route(self, path: str) -> tuple[str, str]: ...         # line 128
    def _readiness(self) -> tuple[str, str]: ...                # line 138
    def _liveness(self) -> tuple[str, str]: ...                 # line 165

# qw/health.py — module-level constants:
HTTP_200 = "200 OK"                                             # line 22
HTTP_404 = "404 Not Found"                                      # line 23
HTTP_503 = "503 Service Unavailable"                            # line 24

# qw/server.py — where HealthServer is created (line 367):
self._health_server = HealthServer(
    queue=self.queue,
    host=self.host,
    port=self._health_port,
    worker_name=self._name,
)
```

### Does NOT Exist
- ~~`HealthServer.__init__(shared_state=...)`~~ — param does not exist; must be added
- ~~`HealthServer._supervisor_status()`~~ — method does not exist; must be created
- ~~`HealthServer._is_draining`~~ — no such attribute; draining check uses shared_state

---

## Implementation Notes

### Pattern to Follow
```python
# Extended __init__:
def __init__(
    self,
    queue: QueueManager,
    host: str = "0.0.0.0",
    port: int = 8080,
    worker_name: str = "",
    shared_state=None,          # NEW
):
    # ... existing code ...
    self._shared_state = shared_state

# Extended _route:
def _route(self, path: str) -> tuple[str, str]:
    if path in ("/health", "/health/ready"):
        return self._readiness()
    elif path == "/health/live":
        return self._liveness()
    elif path == "/supervisor/status":
        return self._supervisor_status()
    else:
        body = json_encoder({"error": "not found"})
        return HTTP_404, body

# Modified _readiness — add draining check BEFORE queue check:
def _readiness(self) -> tuple[str, str]:
    # Check draining status first
    if self._shared_state is not None:
        try:
            d = dict(self._shared_state.get(self._worker_name, {}))
            if d.get("status") == "draining":
                body = json_encoder({
                    "status": "draining",
                    "worker": self._worker_name,
                })
                return HTTP_503, body
        except Exception:
            pass
    # ... existing queue-based readiness logic ...

# New _supervisor_status:
def _supervisor_status(self) -> tuple[str, str]:
    if self._shared_state is None:
        body = json_encoder({"error": "shared state not available"})
        return HTTP_503, body
    now = time.time()
    workers = {}
    for name, raw_state in self._shared_state.items():
        d = dict(raw_state)
        heartbeat = d.get("heartbeat", 0.0)
        workers[name] = {
            "pid": d.get("pid"),
            "status": d.get("status", "unknown"),
            "heartbeat_age_s": round(now - heartbeat, 1) if heartbeat > 0 else None,
            "draining_since": d.get("draining_since"),
            "task_ledger_depth": len(d.get("task_ledger", [])),
            "queue_size": len(d.get("queue", [])),
        }
    body = json_encoder({"workers": workers})
    return HTTP_200, body
```

### Key Constraints
- `shared_state` is optional — HealthServer must still work without it
  (backwards compatible)
- The `/supervisor/status` endpoint reads ALL workers' state, not just
  the current worker
- `_readiness()` must check draining BEFORE the existing queue capacity
  check — a draining worker returns 503 even if its queue has room
- `time.time()` must be imported (already available in module scope)
- Update the HealthServer instantiation in `qw/server.py:367` to pass
  `shared_state=self._shared_state`

---

## Acceptance Criteria

- [ ] `/health/ready` returns 503 with `"status": "draining"` when worker is draining
- [ ] `/health/ready` still works normally when not draining (existing behavior preserved)
- [ ] `/supervisor/status` returns JSON with per-worker status, heartbeat_age_s,
  draining_since, task_ledger_depth, queue_size
- [ ] `/supervisor/status` returns 503 when shared_state is not available
- [ ] HealthServer still works when `shared_state=None` (backwards compatible)
- [ ] All existing tests still pass: `pytest tests/ -v`
- [ ] New tests pass: `pytest tests/test_health_supervisor.py -v`

---

## Test Specification

```python
# tests/test_health_supervisor.py
import time
import multiprocessing as mp
import pytest
from unittest.mock import MagicMock
from qw.health import HealthServer, HTTP_200, HTTP_503


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


@pytest.fixture
def mock_queue():
    q = MagicMock()
    q.snapshot.return_value = {
        "size": 1, "max_size": 4, "base_size": 4, "grow_margin": 2,
        "ceiling": 6, "grow_events": 0, "discard_events": 0, "full": False,
    }
    return q


class TestReadinessDraining:
    def test_returns_503_when_draining(self, shared, mock_queue):
        shared["W0"] = {
            "pid": 1234, "status": "draining", "heartbeat": time.time(),
            "draining_since": time.time(), "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._readiness()
        assert status == HTTP_503
        assert '"draining"' in body

    def test_returns_200_when_healthy(self, shared, mock_queue):
        shared["W0"] = {
            "pid": 1234, "status": "healthy", "heartbeat": time.time(),
            "draining_since": None, "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, _ = hs._readiness()
        assert status == HTTP_200

    def test_works_without_shared_state(self, mock_queue):
        hs = HealthServer(queue=mock_queue, worker_name="W0")
        status, _ = hs._readiness()
        assert status == HTTP_200


class TestSupervisorStatus:
    def test_returns_all_workers(self, shared, mock_queue):
        now = time.time()
        shared["W0"] = {
            "pid": 1234, "status": "healthy", "heartbeat": now,
            "draining_since": None, "task_ledger": [{"id": "1"}],
            "queue": [{"id": "q1"}], "tcp_executing": [],
            "redis_executing": [], "broker_executing": [], "completed": [],
        }
        shared["W1"] = {
            "pid": 1235, "status": "draining", "heartbeat": now - 45,
            "draining_since": now - 30, "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._supervisor_status()
        assert status == HTTP_200
        assert '"W0"' in body
        assert '"W1"' in body
        assert '"draining"' in body

    def test_returns_503_without_shared_state(self, mock_queue):
        hs = HealthServer(queue=mock_queue, worker_name="W0")
        status, _ = hs._supervisor_status()
        assert status == HTTP_503
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify TASK-025 and TASK-027 are in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm `qw/health.py` signatures still match
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** following the scope and pattern above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-030-health-draining-supervisor-endpoint.md`
8. **Update index** status to `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-23
**Notes**: `qw/health.py` — added `import time` and `shared_state=None` parameter to `HealthServer.__init__`. `_readiness()` now performs the draining check FIRST: reads `dict(self._shared_state.get(self._worker_name, {}))` and returns 503 with `{"status": "draining", "worker": ..., "draining_since": ...}` before the existing queue-ceiling logic. When `shared_state` is None or the read fails, the old behavior is preserved (fully backwards compatible). `_route()` now dispatches `/supervisor/status` to a new `_supervisor_status()` method. `_supervisor_status()` walks all `self._shared_state.items()` and returns `{"workers": {<name>: {pid, status, heartbeat_age_s, draining_since, task_ledger_depth, queue_size}}}`. `heartbeat_age_s` is `None` when heartbeat is 0 (worker has not emitted one yet). Returns 503 with an `{"error": ..., "workers": {}}` body when `shared_state` is None. Updated the docstring top-level block to document the new endpoint. `qw/server.py` — extended the HealthServer constructor call with `shared_state=self._shared_state`. Created `tests/test_health_supervisor.py` with 11 tests across 3 test classes: readiness draining (5 — draining→503, healthy→200, no state, queue-full existing behavior, draining-beats-full), supervisor status (4 — multi-worker shape, none heartbeat, no state, route dispatch), and route fall-through (2 — 404 + liveness unaffected). Full suite: 139 passed, 1 skipped.

**Deviations from spec**: none — pattern followed exactly; added `draining_since` to the readiness-503 response body for debuggability.
