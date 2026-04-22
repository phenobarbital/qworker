# TASK-031: Supervisor Integration Tests

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-024, TASK-025, TASK-026, TASK-027, TASK-028, TASK-029, TASK-030
**Assigned-to**: unassigned

---

## Context

> Implements Module 8 from the spec. This is the final task that validates the
> entire supervisor system end-to-end. Writes integration tests that exercise
> the full lifecycle: worker crash detection, task rescue, draining behavior,
> drain timeout kill, process respawn, and health endpoint reporting.

---

## Scope

- Create `tests/test_supervisor_integration.py` with end-to-end tests
- Test scenarios:
  1. **Worker crash recovery**: Kill a worker process, verify supervisor detects
     death, rescues ledger tasks to Redis stream, spawns replacement,
     replacement serves traffic
  2. **Stuck worker drain cycle**: Simulate stuck event loop (no heartbeat),
     verify draining is set within `HEARTBEAT_TIMEOUT`, verify new tasks are
     rejected, verify kill after `DRAIN_TIMEOUT`
  3. **Draining client retry**: Send task to draining worker, verify client
     receives error, verify retry reaches a healthy worker
  4. **Self-recovery**: Simulate temporary heartbeat pause then resume, verify
     worker returns to healthy
  5. **Health endpoint reporting**: Verify `/health/ready` returns 503 for
     draining workers and `/supervisor/status` returns correct per-worker data
- Ensure all existing tests still pass

**NOT in scope**:
- Implementing any new functionality (all modules must be complete)
- Performance or load testing

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_supervisor_integration.py` | CREATE | Integration tests for full supervisor lifecycle |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.supervisor import ProcessSupervisor      # from TASK-028
from qw.state import StateTracker                # verified: qw/state.py:13
from qw.queues.manager import QueueManager       # verified: qw/queues/manager.py:33
from qw.server import start_server, QWorker      # verified: qw/server.py:807, :49
from qw.health import HealthServer               # verified: qw/health.py:39
from qw.conf import (
    WORKER_HEARTBEAT_INTERVAL,                   # from TASK-024
    WORKER_HEARTBEAT_TIMEOUT,                    # from TASK-024
    WORKER_DRAIN_TIMEOUT,                        # from TASK-024
    SUPERVISOR_CHECK_INTERVAL,                   # from TASK-024
    WORKER_REDIS,                                # verified: qw/conf.py:76
    REDIS_WORKER_STREAM,                         # verified: qw/conf.py:74
)
import multiprocessing as mp                     # stdlib
import time                                      # stdlib
import asyncio                                   # stdlib
import base64                                    # stdlib
import cloudpickle                               # dependency
import pytest                                    # test dependency
from unittest.mock import MagicMock, patch       # stdlib
```

### Existing Signatures to Use
```python
# qw/supervisor.py (from TASK-028):
class ProcessSupervisor(threading.Thread):
    def __init__(self, shared_state, job_list, worker_name_prefix,
                 host, port, debug, notify_empty, health_port, ...): ...
    def run(self) -> None: ...
    def stop(self) -> None: ...
    def _check_worker(self, idx, process, worker_name) -> None: ...
    def _rescue_tasks(self, worker_name) -> None: ...
    def _kill_and_respawn(self, idx, process, worker_name) -> None: ...

# qw/state.py (after TASK-025):
class StateTracker:
    def update_heartbeat(self) -> None: ...
    def set_status(self, status: str, draining_since: float | None = None) -> None: ...
    def get_heartbeat(self) -> float: ...
    def get_status(self) -> str: ...
    def ledger_add(self, task_id: str, payload: str) -> None: ...
    def ledger_remove(self, task_id: str) -> None: ...
    def ledger_drain(self) -> list[dict]: ...

# qw/health.py (after TASK-030):
class HealthServer:
    def __init__(self, queue, host, port, worker_name, shared_state=None): ...
    def _readiness(self) -> tuple[str, str]: ...
    def _supervisor_status(self) -> tuple[str, str]: ...
```

### Does NOT Exist
- ~~`ProcessSupervisor.check_all()`~~ — wrong method; the loop is in `run()`
- ~~`QWorker.simulate_stuck()`~~ — no such test helper; simulate by not updating heartbeat

---

## Implementation Notes

### Key Constraints
- Integration tests must use `multiprocessing.Manager().dict()` for shared state
  (not plain dicts) to test real cross-process behavior
- Use `unittest.mock.patch` for Redis to avoid requiring a live Redis server
- Worker processes in tests should be mocked (`MagicMock`) to avoid actually
  spawning servers on test ports
- Tests that involve timing (heartbeat staleness, drain timeout) should use
  manipulated timestamps in shared state rather than real `time.sleep()` to
  keep tests fast
- Use `pytest.mark.asyncio` for any async test methods

### Test Design Patterns
```python
# Pattern: simulate a dead worker
mock_process = MagicMock()
mock_process.is_alive.return_value = False
mock_process.name = "Worker-8888_0"

# Pattern: simulate a stuck worker (stale heartbeat)
shared_state["Worker-8888_0"]["heartbeat"] = time.time() - 60  # 60s stale

# Pattern: simulate drain timeout
shared_state["Worker-8888_0"]["status"] = "draining"
shared_state["Worker-8888_0"]["draining_since"] = time.time() - 400  # 400s ago

# Pattern: simulate recovery
shared_state["Worker-8888_0"]["heartbeat"] = time.time()  # fresh
# status still "draining" — supervisor should detect fresh heartbeat and recover
```

### References in Codebase
- `tests/test_state_tracker.py` — existing test patterns with Manager fixtures
- `tests/test_consumer_resilience.py` — existing integration test patterns
- `tests/test_queue_manager_dynamic.py` — existing QueueManager test patterns

---

## Acceptance Criteria

- [ ] `test_worker_crash_recovery_e2e` — dead worker triggers rescue + respawn
- [ ] `test_stuck_worker_drain_cycle` — stale heartbeat → draining → timeout kill
- [ ] `test_draining_client_retry` — draining worker rejects tasks
- [ ] `test_self_recovery` — heartbeat resumes → healthy again
- [ ] `test_health_ready_503_when_draining` — `/health/ready` returns 503
- [ ] `test_supervisor_status_endpoint` — `/supervisor/status` returns correct data
- [ ] All existing tests still pass: `pytest tests/ -v`
- [ ] New tests pass: `pytest tests/test_supervisor_integration.py -v`

---

## Test Specification

```python
# tests/test_supervisor_integration.py
import time
import multiprocessing as mp
import pytest
from unittest.mock import MagicMock, patch
from qw.supervisor import ProcessSupervisor
from qw.state import StateTracker
from qw.health import HealthServer, HTTP_200, HTTP_503


def _make_worker_state(pid, heartbeat=None, status="healthy",
                       draining_since=None, task_ledger=None):
    """Helper to create a valid worker state dict."""
    return {
        "pid": pid,
        "heartbeat": heartbeat or time.time(),
        "status": status,
        "draining_since": draining_since,
        "task_ledger": task_ledger or [],
        "queue": [],
        "tcp_executing": [],
        "redis_executing": [],
        "broker_executing": [],
        "completed": [],
    }


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


class TestWorkerCrashRecoveryE2E:
    def test_dead_worker_triggers_rescue_and_respawn(self, shared):
        """Full cycle: dead process → rescue tasks → respawn."""
        shared["W_0"] = _make_worker_state(
            pid=99999,
            task_ledger=[
                {"task_id": "orphan-1", "payload": "cGF5bG9hZA==", "enqueued_at": 1000},
            ],
        )
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        mock_proc.name = "W_0"
        job_list = [mock_proc]

        sup = ProcessSupervisor(
            shared_state=shared, job_list=job_list,
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
        )
        with patch.object(sup, '_rescue_tasks') as mock_rescue, \
             patch.object(sup, '_kill_and_respawn') as mock_respawn:
            sup._check_worker(0, mock_proc, "W_0")
            mock_rescue.assert_called_once_with("W_0")
            mock_respawn.assert_called_once()


class TestStuckWorkerDrainCycle:
    def test_stale_heartbeat_marks_draining(self, shared):
        shared["W_0"] = _make_worker_state(
            pid=12345, heartbeat=time.time() - 60,
        )
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_proc],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
            heartbeat_timeout=30,
        )
        sup._check_worker(0, mock_proc, "W_0")
        assert dict(shared["W_0"])["status"] == "draining"

    def test_drain_timeout_kills(self, shared):
        shared["W_0"] = _make_worker_state(
            pid=12345, heartbeat=time.time() - 600,
            status="draining", draining_since=time.time() - 400,
        )
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_proc],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
            drain_timeout=300,
        )
        with patch.object(sup, '_kill_and_respawn') as mock_kill:
            sup._check_worker(0, mock_proc, "W_0")
            mock_kill.assert_called_once()


class TestSelfRecovery:
    def test_heartbeat_resumes_restores_healthy(self, shared):
        shared["W_0"] = _make_worker_state(
            pid=12345, heartbeat=time.time(),
            status="draining", draining_since=time.time() - 10,
        )
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_proc],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
            heartbeat_timeout=30,
        )
        sup._check_worker(0, mock_proc, "W_0")
        d = dict(shared["W_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None


class TestHealthEndpoints:
    def test_readiness_503_when_draining(self, shared):
        shared["W_0"] = _make_worker_state(
            pid=1234, status="draining",
        )
        mock_q = MagicMock()
        mock_q.snapshot.return_value = {
            "size": 0, "max_size": 4, "base_size": 4, "grow_margin": 2,
            "ceiling": 6, "grow_events": 0, "discard_events": 0, "full": False,
        }
        hs = HealthServer(
            queue=mock_q, worker_name="W_0", shared_state=shared
        )
        status, body = hs._readiness()
        assert status == HTTP_503
        assert '"draining"' in body

    def test_supervisor_status_returns_workers(self, shared):
        now = time.time()
        shared["W_0"] = _make_worker_state(pid=1234, heartbeat=now)
        shared["W_1"] = _make_worker_state(
            pid=1235, heartbeat=now - 45,
            status="draining", draining_since=now - 30,
        )
        mock_q = MagicMock()
        hs = HealthServer(
            queue=mock_q, worker_name="W_0", shared_state=shared
        )
        status, body = hs._supervisor_status()
        assert status == HTTP_200
        assert '"W_0"' in body
        assert '"W_1"' in body
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify ALL prior FEAT-005 tasks are in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm all module signatures match
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** the integration test file
6. **Run ALL tests**: `pytest tests/ -v`
7. **Verify** all acceptance criteria are met
8. **Move this file** to `tasks/completed/TASK-031-integration-tests.md`
9. **Update index** status to `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
