# TASK-028: ProcessSupervisor Module

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-024, TASK-025
**Assigned-to**: unassigned

---

## Context

> Implements Module 5 from the spec — the core supervisor logic. This is the
> largest and most critical task. Creates a new `qw/supervisor.py` module
> containing the `ProcessSupervisor` class that runs as a daemon thread in
> the parent process, monitors worker heartbeats, manages the state machine
> (HEALTHY → DRAINING → DEAD → HEALTHY), kills stuck processes, rescues
> orphaned tasks via Redis streams, and spawns fresh replacements.

---

## Scope

- Create new module `qw/supervisor.py` with class `ProcessSupervisor(threading.Thread)`
- Implement the main supervisor loop (`run()`) that checks all workers every
  `SUPERVISOR_CHECK_INTERVAL` seconds
- Implement per-worker inspection (`_check_worker()`):
  - If `Process.is_alive() == False` → mark DEAD, rescue tasks, respawn
  - If heartbeat stale > `WORKER_HEARTBEAT_TIMEOUT` and status is "healthy" →
    mark "draining" via `set_status()`
  - If status is "draining" and heartbeat resumes → mark "healthy" (self-recovery)
  - If status is "draining" for > `WORKER_DRAIN_TIMEOUT` → kill + rescue + respawn
- Implement `_mark_draining(worker_name)` — set status to "draining"
- Implement `_mark_healthy(worker_name)` — set status back to "healthy"
- Implement `_rescue_tasks(worker_name)` — read task_ledger via `ledger_drain()`,
  re-submit each task to Redis stream via `xadd`
- Implement `_kill_and_respawn(idx, process, worker_name)`:
  - `Process.terminate()` (SIGTERM)
  - Wait `SUPERVISOR_KILL_GRACE` seconds
  - If still alive: `Process.kill()` (SIGKILL)
  - `Process.join(timeout=5)`
  - Spawn new `mp.Process(target=start_server, ...)` with same `worker_id`
  - Update `job_list[idx]`
- Implement `stop()` — signal the supervisor to exit its loop
- Write comprehensive unit tests

**NOT in scope**:
- Modifying `SpawnProcess` to start/stop the supervisor (TASK-029)
- The heartbeat writing in QWorker (TASK-027)
- The draining guard in QueueManager (TASK-026)
- The health endpoint (TASK-030)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/supervisor.py` | CREATE | ProcessSupervisor daemon thread |
| `tests/test_supervisor.py` | CREATE | Unit tests for supervisor logic |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from qw.server import start_server              # verified: qw/server.py:807
from qw.conf import (
    WORKER_HEALTH_PORT,                          # verified: qw/conf.py:44
    WORKER_REDIS,                                # verified: qw/conf.py:76
    REDIS_WORKER_STREAM,                         # verified: qw/conf.py:74
    # NEW (from TASK-024):
    WORKER_HEARTBEAT_TIMEOUT,
    WORKER_DRAIN_TIMEOUT,
    SUPERVISOR_CHECK_INTERVAL,
    SUPERVISOR_KILL_GRACE,
)
import threading                                 # stdlib
import time                                      # stdlib
import base64                                    # stdlib
import multiprocessing as mp                     # stdlib
from redis import asyncio as aioredis            # verified: qw/server.py:13
from navconfig.logging import logging            # verified: qw/server.py (used across all modules)
```

### Existing Signatures to Use
```python
# qw/server.py:807 — start_server function (target for mp.Process)
def start_server(
    num_worker: int,
    host: str,
    port: int,
    debug: bool,
    notify_empty: bool,
    health_port: int = WORKER_HEALTH_PORT,
    shared_state=None,
) -> None: ...

# qw/state.py (after TASK-025):
class StateTracker:
    def __init__(self, shared_state: dict, worker_name: str, pid: int): ...
    def update_heartbeat(self) -> None: ...
    def set_status(self, status: str, draining_since: float | None = None) -> None: ...
    def get_heartbeat(self) -> float: ...
    def get_status(self) -> str: ...
    def ledger_drain(self) -> list[dict]: ...

# qw/process.py — SpawnProcess (context for understanding how workers are spawned)
class SpawnProcess:
    def __init__(self, args):
        self._shared_state = self._manager.dict()      # line 93
    # Worker spawn pattern (lines 96-102):
    # p = mp.Process(
    #     target=start_server,
    #     name=f'{self.worker}_{i}',
    #     args=(i, args.host, args.port, args.debug, args.notify_empty,
    #           self._health_port, self._shared_state)
    # )

# multiprocessing.Process — stdlib
# process.is_alive() -> bool
# process.terminate() -> None  (sends SIGTERM)
# process.kill() -> None       (sends SIGKILL)
# process.join(timeout=None) -> None
# process.name -> str
# process.pid -> int | None
```

### Does NOT Exist
- ~~`qw.supervisor`~~ — module does not exist; must be created
- ~~`ProcessSupervisor`~~ — class does not exist; must be created
- ~~`StateTracker.get_heartbeat_for(worker_name)`~~ — wrong API; must read shared_state directly
- ~~`self._state.set_status()`~~ — supervisor reads/writes shared_state dict directly, does NOT instantiate its own StateTracker (it operates on all workers, not one)

---

## Implementation Notes

### Pattern to Follow
```python
import threading
import time
import base64
import multiprocessing as mp
from redis import asyncio as aioredis
from navconfig.logging import logging
from .server import start_server
from .conf import (
    WORKER_HEARTBEAT_TIMEOUT,
    WORKER_DRAIN_TIMEOUT,
    SUPERVISOR_CHECK_INTERVAL,
    SUPERVISOR_KILL_GRACE,
    WORKER_HEALTH_PORT,
    WORKER_REDIS,
    REDIS_WORKER_STREAM,
)


class ProcessSupervisor(threading.Thread):
    """Monitors worker processes and manages lifecycle."""

    def __init__(
        self,
        shared_state,
        job_list: list,
        worker_name_prefix: str,
        host: str,
        port: int,
        debug: bool,
        notify_empty: bool,
        health_port: int,
        check_interval: float = SUPERVISOR_CHECK_INTERVAL,
        heartbeat_timeout: float = WORKER_HEARTBEAT_TIMEOUT,
        drain_timeout: float = WORKER_DRAIN_TIMEOUT,
    ):
        super().__init__(daemon=True, name="QW.Supervisor")
        self._shared_state = shared_state
        self._job_list = job_list
        self._prefix = worker_name_prefix
        self._host = host
        self._port = port
        self._debug = debug
        self._notify_empty = notify_empty
        self._health_port = health_port
        self._check_interval = check_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._drain_timeout = drain_timeout
        self._stop_event = threading.Event()
        self.logger = logging.getLogger("QW.Supervisor")

    def run(self):
        """Main supervisor loop."""
        self.logger.info("Supervisor started (interval=%ss)", self._check_interval)
        while not self._stop_event.is_set():
            try:
                for idx, process in enumerate(self._job_list):
                    worker_name = f"{self._prefix}_{idx}"
                    self._check_worker(idx, process, worker_name)
            except Exception:
                self.logger.exception("Supervisor check cycle error")
            self._stop_event.wait(timeout=self._check_interval)
        self.logger.info("Supervisor stopped")

    def stop(self):
        self._stop_event.set()
```

### Key Constraints

1. **Supervisor does NOT instantiate StateTracker**. It reads/writes `shared_state`
   dict directly because it operates across ALL workers. The dict keys are worker
   names, values are the state dicts initialized by each worker's StateTracker.

2. **Reading shared state** (thread-safe via Manager proxy):
   ```python
   d = dict(self._shared_state.get(worker_name, {}))
   heartbeat = d.get("heartbeat", 0.0)
   status = d.get("status", "healthy")
   draining_since = d.get("draining_since")
   ```

3. **Writing shared state** (full-value replacement):
   ```python
   d = dict(self._shared_state[worker_name])
   d["status"] = "draining"
   d["draining_since"] = time.time()
   self._shared_state[worker_name] = d
   ```

4. **Redis xadd for task rescue** — must use a synchronous Redis connection
   (supervisor runs in a thread, not an asyncio event loop):
   ```python
   import redis
   r = redis.Redis.from_url(WORKER_REDIS)
   for entry in ledger_entries:
       r.xadd(REDIS_WORKER_STREAM, {
           "task": entry["payload"],
           "uid": entry["task_id"],
       })
   r.close()
   ```
   Note: use `redis` (sync), NOT `redis.asyncio`. Import at method level to
   avoid adding a top-level sync redis dependency.

5. **Respawn with same worker_id**: The new `mp.Process` must use the same
   `num_worker` (idx) so the worker gets the same name and slot in `job_list`.

6. **Filter out non-worker processes**: `job_list` might contain the NotifyWorker
   process. Only check processes whose name starts with `_prefix`.

### References in Codebase
- `qw/process.py:96-102` — worker spawn pattern to replicate
- `qw/state.py:6-8` — Manager proxy mutation rules
- `qw/server.py:807` — `start_server()` function signature

---

## Acceptance Criteria

- [ ] `ProcessSupervisor` is a daemon thread
- [ ] Detects dead workers (`is_alive() == False`) and rescues + respawns
- [ ] Detects stale heartbeats and marks workers as "draining"
- [ ] Allows draining workers to self-recover if heartbeat resumes
- [ ] Kills workers draining longer than `WORKER_DRAIN_TIMEOUT`
- [ ] Rescues orphaned tasks from ledger and pushes to Redis stream
- [ ] Respawns new process with same `worker_id`
- [ ] `stop()` cleanly terminates the supervisor loop
- [ ] All tests pass: `pytest tests/test_supervisor.py -v`

---

## Test Specification

```python
# tests/test_supervisor.py
import time
import multiprocessing as mp
import pytest
from unittest.mock import MagicMock, patch
from qw.supervisor import ProcessSupervisor


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


class TestSupervisorDetection:
    def test_detects_dead_process(self, shared):
        """Supervisor detects is_alive()==False and triggers respawn."""
        shared["W_0"] = {
            "pid": 99999, "heartbeat": time.time(), "status": "healthy",
            "draining_since": None, "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.name = "W_0"
        # Test that _check_worker handles dead process
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_process],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
        )
        with patch.object(sup, '_kill_and_respawn') as mock_respawn:
            sup._check_worker(0, mock_process, "W_0")
            mock_respawn.assert_called_once()

    def test_detects_stale_heartbeat(self, shared):
        """Supervisor marks draining when heartbeat is stale."""
        shared["W_0"] = {
            "pid": 12345, "heartbeat": time.time() - 60, "status": "healthy",
            "draining_since": None, "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mock_process.name = "W_0"
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_process],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
            heartbeat_timeout=30,
        )
        sup._check_worker(0, mock_process, "W_0")
        d = dict(shared["W_0"])
        assert d["status"] == "draining"
        assert d["draining_since"] is not None

    def test_recovery_from_draining(self, shared):
        """Worker recovers if heartbeat resumes while draining."""
        shared["W_0"] = {
            "pid": 12345, "heartbeat": time.time(),  # fresh heartbeat
            "status": "draining", "draining_since": time.time() - 10,
            "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_process],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
            heartbeat_timeout=30,
        )
        sup._check_worker(0, mock_process, "W_0")
        d = dict(shared["W_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None

    def test_drain_timeout_triggers_kill(self, shared):
        """Worker draining too long is killed and respawned."""
        shared["W_0"] = {
            "pid": 12345, "heartbeat": time.time() - 600,
            "status": "draining", "draining_since": time.time() - 400,
            "task_ledger": [],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[mock_process],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
            drain_timeout=300,
        )
        with patch.object(sup, '_kill_and_respawn') as mock_kill:
            sup._check_worker(0, mock_process, "W_0")
            mock_kill.assert_called_once()


class TestTaskRescue:
    def test_rescue_tasks_pushes_to_redis(self, shared):
        """Rescued tasks are pushed to Redis stream."""
        shared["W_0"] = {
            "pid": 12345, "heartbeat": 0, "status": "healthy",
            "draining_since": None,
            "task_ledger": [
                {"task_id": "id-1", "payload": "cGF5bG9hZDE=", "enqueued_at": 1000},
                {"task_id": "id-2", "payload": "cGF5bG9hZDI=", "enqueued_at": 1001},
            ],
            "queue": [], "tcp_executing": [], "redis_executing": [],
            "broker_executing": [], "completed": [],
        }
        sup = ProcessSupervisor(
            shared_state=shared, job_list=[],
            worker_name_prefix="W", host="127.0.0.1", port=8888,
            debug=False, notify_empty=False, health_port=8080,
        )
        with patch('redis.Redis') as MockRedis:
            mock_r = MagicMock()
            MockRedis.from_url.return_value = mock_r
            sup._rescue_tasks("W_0")
            assert mock_r.xadd.call_count == 2
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — verify TASK-024 and TASK-025 are in `tasks/completed/`
3. **Verify the Codebase Contract** — confirm `start_server` signature, `shared_state` schema
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** following the scope and constraints above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-028-process-supervisor.md`
8. **Update index** status to `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
