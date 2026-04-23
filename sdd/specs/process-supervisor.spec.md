# Feature Specification: Process Supervisor

**Feature ID**: FEAT-005
**Date**: 2026-04-23
**Author**: Jesus Lara
**Status**: approved
**Target version**: 2.1.0

---

## 1. Motivation & Business Requirements

### Problem Statement

QWorker spawns N worker processes (`mp.Process`) sharing a single TCP port via
`SO_REUSEPORT`. Each process owns an independent asyncio event loop and an
in-process `asyncio.Queue`. When a worker process dies or becomes stuck (event
loop blocked, deadlock, runaway task), three things go wrong:

1. **Orphaned queue** — tasks already sitting in the dead process's
   `asyncio.Queue` are lost forever because that memory is inaccessible from
   other processes.
2. **Silent black hole** — while the OS may eventually reclaim the dead
   process's socket, a *stuck* (alive but not processing) worker keeps its
   socket open. The kernel continues routing new TCP connections to it via
   `SO_REUSEPORT`, and those tasks are accepted into a queue that will never
   drain.
3. **No automatic recovery** — the parent process (`SpawnProcess`) does a
   fire-and-forget spawn. It never checks whether children are still healthy
   and never replaces dead workers.

### Goals

- **G1**: Detect dead or stuck worker processes within 30 seconds.
- **G2**: Stop routing new tasks to a stuck worker (mark it "draining") so
  clients fail fast and are re-routed to healthy workers via `SO_REUSEPORT`.
- **G3**: After a configurable timeout (default 5 minutes) of being in
  "draining" state with no recovery, forcefully kill the stuck process and
  spawn a fresh replacement with the same `worker_id`.
- **G4**: Rescue orphaned tasks from dead/killed workers by maintaining a
  serialized task ledger in shared state, and re-submit rescued tasks via
  Redis streams.
- **G5**: Allow a draining worker to self-recover — if its event loop unblocks
  and heartbeats resume, the Supervisor promotes it back to "healthy".

### Non-Goals (explicitly out of scope)

- Changing the `SO_REUSEPORT` load-balancing model (kernel-level).
- Adding inter-process task stealing (live queue migration between healthy
  workers).
- Replacing the in-memory `asyncio.Queue` with a distributed queue (Redis
  streams remain a secondary transport, not the primary path).
- Persisting the task ledger to disk or Redis (shared memory is sufficient;
  if the entire host dies, all processes are lost anyway).
- Modifying the Redis stream consumer group logic (`start_subscription`).

---

## 2. Architectural Design

### Overview

A **Supervisor thread** runs inside the parent `SpawnProcess` and periodically
inspects every worker process through two channels:

1. **`Process.is_alive()`** — OS-level liveness check.
2. **Heartbeat timestamps** — each worker writes `time.time()` to the existing
   `multiprocessing.Manager().dict()` (`shared_state`) on a fixed interval.

Each worker also maintains a **task ledger** in `shared_state` — a list of
serialized task payloads for every item currently in its `asyncio.Queue`. When
a task completes (or is discarded), the entry is removed. If a worker dies,
the Supervisor can read the ledger and re-submit those tasks via Redis streams.

Workers check a **status flag** in `shared_state` before accepting new queued
tasks. When the Supervisor sets `status = "draining"`, the worker rejects
incoming queue requests with a `QWException`, causing the TCP client to retry
and get routed to a different worker by the kernel.

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  SpawnProcess  (parent, main thread)                                │
│                                                                     │
│  ┌──────────────────────────────────┐                               │
│  │  SupervisorThread                │  reads heartbeats, checks     │
│  │  (daemon thread, 10s interval)   │  Process.is_alive(), manages  │
│  │                                  │  worker lifecycle              │
│  │  States: HEALTHY → DRAINING →    │                               │
│  │          DEAD → HEALTHY (new)    │                               │
│  └──────────┬───────────────────────┘                               │
│             │ reads/writes                                          │
│             ▼                                                       │
│  ┌──────────────────────────────────┐                               │
│  │  shared_state (Manager().dict()) │                               │
│  │                                  │                               │
│  │  worker_name → {                 │                               │
│  │    "pid": int,                   │                               │
│  │    "heartbeat": float,           │  ← written by worker          │
│  │    "status": str,                │  ← written by supervisor      │
│  │    "draining_since": float|None, │  ← written by supervisor      │
│  │    "task_ledger": [...],         │  ← written by worker          │
│  │    "queue": [...],               │  (existing)                   │
│  │    "tcp_executing": [...],       │  (existing)                   │
│  │    ...                           │                               │
│  │  }                               │                               │
│  └──────────────────────────────────┘                               │
│             ▲                                                       │
│             │ reads/writes                                          │
│  ┌──────────┴───────────────────────────────────────────────────┐   │
│  │  Worker Process 0 ─┬─ QWorker ─┬─ _heartbeat_loop()         │   │
│  │                    │           ├─ _is_draining() check       │   │
│  │                    │           └─ QueueManager               │   │
│  │                    │              ├─ put() → ledger write     │   │
│  │                    │              └─ queue_handler() → ledger │   │
│  │                    │                  remove on complete      │   │
│  │                    └─ TCP server (SO_REUSEPORT :8888)        │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  Worker Process 1 ─┬─ (same structure)                       │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  Worker Process 2 ─┬─ ...                                    │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  Worker Process 3 ─┬─ ...                                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Task Rescue Path (on worker death):                                │
│  Supervisor reads task_ledger → base64-decode → xadd to Redis      │
│  stream → healthy workers consume via existing consumer group       │
└─────────────────────────────────────────────────────────────────────┘
```

### Worker State Machine

```
                  heartbeat OK (within timeout)
    ┌──────────────────────────────────────────┐
    │                                          │
    ▼                                          │
 HEALTHY ──── heartbeat stale > HEARTBEAT_TIMEOUT ──→ DRAINING
    │                                                     │
    │                                                     │ draining_since > DRAIN_TIMEOUT
    │         Process.is_alive() == False                  ▼
    └──────────────────────────────────────────────────→ DEAD
                                                          │
                                                          │ rescue tasks + kill (if needed) + respawn
                                                          ▼
                                                       HEALTHY (new process, same worker_id)
```

Transitions:
- **HEALTHY → DRAINING**: Supervisor detects stale heartbeat. Sets
  `status="draining"` and `draining_since=time.time()`.
- **DRAINING → HEALTHY**: Heartbeat resumes within drain timeout. Supervisor
  clears `draining_since` and sets `status="healthy"`.
- **DRAINING → DEAD**: `draining_since` exceeds `WORKER_DRAIN_TIMEOUT` (5 min)
  OR `Process.is_alive()` returns False.
- **HEALTHY → DEAD**: `Process.is_alive()` returns False (process crashed).
- **DEAD → HEALTHY**: Supervisor rescues tasks from ledger, kills process (if
  still alive), spawns a replacement `mp.Process` with the same `worker_id`.

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `SpawnProcess` (`qw/process.py`) | extends | Supervisor thread is created and started here |
| `QWorker` (`qw/server.py`) | modifies | Add heartbeat loop + draining check |
| `StateTracker` (`qw/state.py`) | extends | Add heartbeat/status/ledger fields + methods |
| `QueueManager` (`qw/queues/manager.py`) | modifies | Add ledger write in `put()`, ledger remove in `queue_handler()` |
| `HealthServer` (`qw/health.py`) | extends | Report draining status in `/health/ready` |
| `conf.py` (`qw/conf.py`) | modifies | New configuration constants |

### Data Models

```python
# Task ledger entry stored in shared_state[worker_name]["task_ledger"]
# Plain dicts (not Pydantic) because they must be picklable for Manager proxy.
task_ledger_entry = {
    "task_id": str,           # UUID string
    "payload": str,           # base64-encoded cloudpickle.dumps(task)
    "enqueued_at": float,     # time.time()
}

# Worker state entry in shared_state (extended from existing StateTracker format)
worker_state = {
    "pid": int,               # existing
    "heartbeat": float,       # NEW: time.time() of last heartbeat
    "status": str,            # NEW: "healthy" | "draining"
    "draining_since": float | None,  # NEW: timestamp when draining started
    "task_ledger": list[dict],       # NEW: list of task_ledger_entry
    "queue": list,            # existing
    "tcp_executing": list,    # existing
    "redis_executing": list,  # existing
    "broker_executing": list, # existing
    "completed": list,        # existing
}
```

### New Public Interfaces

```python
# qw/supervisor.py — new module
class ProcessSupervisor(threading.Thread):
    """Monitors worker processes and manages lifecycle.

    Runs as a daemon thread in the parent SpawnProcess.
    """
    def __init__(
        self,
        shared_state: dict,           # multiprocessing.Manager().dict()
        job_list: list,               # list of mp.Process
        worker_name_prefix: str,      # e.g. "Worker-8888"
        host: str,
        port: int,
        debug: bool,
        notify_empty: bool,
        health_port: int,
        check_interval: float = 10.0,
        heartbeat_timeout: float = 30.0,
        drain_timeout: float = 300.0,
    ): ...

    def run(self) -> None:
        """Main supervisor loop (called by Thread.start())."""
        ...

    def stop(self) -> None:
        """Signal the supervisor to stop."""
        ...
```

---

## 3. Module Breakdown

### Module 1: Configuration Constants
- **Path**: `qw/conf.py`
- **Responsibility**: Add new configuration parameters for the supervisor.
- **Depends on**: nothing new

New constants:
```python
WORKER_HEARTBEAT_INTERVAL = config.getint('WORKER_HEARTBEAT_INTERVAL', fallback=5)
WORKER_HEARTBEAT_TIMEOUT = config.getint('WORKER_HEARTBEAT_TIMEOUT', fallback=30)
WORKER_DRAIN_TIMEOUT = config.getint('WORKER_DRAIN_TIMEOUT', fallback=300)
SUPERVISOR_CHECK_INTERVAL = config.getint('SUPERVISOR_CHECK_INTERVAL', fallback=10)
SUPERVISOR_KILL_GRACE = config.getint('SUPERVISOR_KILL_GRACE', fallback=10)
```

### Module 2: StateTracker Extensions
- **Path**: `qw/state.py`
- **Responsibility**: Extend the shared state schema with heartbeat, status,
  draining_since, and task_ledger fields. Provide methods for heartbeat
  updates and task ledger operations.
- **Depends on**: Module 1

New methods:
- `update_heartbeat()` — write current timestamp.
- `set_status(status: str, draining_since: float | None)` — set worker status.
- `get_heartbeat() -> float` — read heartbeat for this worker.
- `get_status() -> str` — read status for this worker.
- `ledger_add(task_id: str, payload: str)` — add serialized task to ledger.
- `ledger_remove(task_id: str)` — remove completed task from ledger.
- `ledger_drain() -> list[dict]` — read and clear the entire ledger (used by
  supervisor during rescue).

### Module 3: QueueManager Ledger Integration
- **Path**: `qw/queues/manager.py`
- **Responsibility**: Write to task ledger on `put()`, remove from task ledger
  on `queue_handler()` completion. Check draining status and reject tasks when
  draining.
- **Depends on**: Module 2

Changes:
- `put()`: After successful `put_nowait()`, call
  `self._state.ledger_add(task_id, serialized_payload)`.
- `queue_handler()`: In the `finally` block, call
  `self._state.ledger_remove(task_id)`.

### Module 4: QWorker Heartbeat & Draining
- **Path**: `qw/server.py`
- **Responsibility**: Add heartbeat background task to QWorker. Add draining
  check in `connection_handler` and `handle_queue_wrapper` before accepting
  new queued tasks.
- **Depends on**: Module 2

Changes:
- `start()`: Create `asyncio.create_task(self._heartbeat_loop())`.
- `_heartbeat_loop()`: New async method. Writes heartbeat every
  `WORKER_HEARTBEAT_INTERVAL` seconds.
- `_is_draining()`: New sync method. Reads `status` from shared state.
- `connection_handler()`: Before `self.queue.put()`, check `_is_draining()`.
  If draining, return error via `closing_writer` so client retries on another
  worker.
- `handle_queue_wrapper()`: Same draining check before `self.queue.put()`.
- `shutdown()`: Cancel heartbeat task.

### Module 5: ProcessSupervisor
- **Path**: `qw/supervisor.py` (new file)
- **Responsibility**: The core supervisor logic. Runs as a daemon thread in
  the parent process. Monitors heartbeats, manages worker state transitions,
  kills stuck processes, rescues orphaned tasks, spawns replacements.
- **Depends on**: Module 1, Module 2

Key methods:
- `run()` — main loop, runs every `SUPERVISOR_CHECK_INTERVAL`.
- `_check_worker(idx, process, worker_name)` — inspect one worker: liveness,
  heartbeat freshness, draining timeout.
- `_mark_draining(worker_name)` — set status to "draining" in shared state.
- `_mark_healthy(worker_name)` — set status back to "healthy".
- `_rescue_tasks(worker_name)` — read task_ledger, re-submit via Redis stream.
- `_kill_and_respawn(idx, process, worker_name)` — terminate/kill process,
  join, spawn new `mp.Process` with same args, update `job_list[idx]`.
- `_respawn_worker(idx, worker_name)` — create and start a new `mp.Process`.

### Module 6: SpawnProcess Integration
- **Path**: `qw/process.py`
- **Responsibility**: Create and start the `ProcessSupervisor` thread after
  spawning workers. Stop it on `terminate()`.
- **Depends on**: Module 5

Changes:
- `__init__()`: After the worker spawn loop, create and start
  `ProcessSupervisor` as a daemon thread.
- `terminate()`: Call `supervisor.stop()` before terminating workers.

### Module 7: HealthServer Enhancement
- **Path**: `qw/health.py`
- **Responsibility**: Report draining status in the `/health/ready` endpoint.
  A draining worker should return 503 so external load balancers also stop
  routing to it. Expose a new `/supervisor/status` endpoint for Grafana
  dashboards.
- **Depends on**: Module 4, Module 2

Changes:
- `__init__()`: Accept an optional `shared_state` parameter (the
  `Manager().dict()` proxy) so the health server can read all workers' status.
- `_readiness()`: Check if worker status is "draining" (passed via
  QueueManager or a flag). If draining, return 503 with
  `"status": "draining"`.
- `_route()`: Add route for `/supervisor/status`.
- `_supervisor_status()`: New method. Reads `shared_state` and returns a JSON
  response with per-worker info:
  ```json
  {
    "workers": {
      "Worker-8888_0": {
        "pid": 12345,
        "status": "healthy",
        "heartbeat_age_s": 3.2,
        "draining_since": null,
        "task_ledger_depth": 2,
        "queue_size": 1
      },
      "Worker-8888_1": {
        "pid": 12346,
        "status": "draining",
        "heartbeat_age_s": 45.7,
        "draining_since": "2026-04-23T14:30:00Z",
        "task_ledger_depth": 0,
        "queue_size": 3
      }
    }
  }
  ```
  This endpoint is available on all workers (not just worker_id==0) since each
  worker's HealthServer can read the shared state. However, only worker_id==0
  runs the HealthServer by default, so in practice it is a single endpoint.

### Module 8: Tests
- **Path**: `tests/test_supervisor.py` (new file),
  `tests/test_heartbeat.py` (new file)
- **Responsibility**: Unit and integration tests for all new functionality.
- **Depends on**: Modules 1–7

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_heartbeat_updates_shared_state` | Module 2 | `update_heartbeat()` writes timestamp to shared state |
| `test_heartbeat_freshness` | Module 2 | `get_heartbeat()` returns the most recent timestamp |
| `test_status_transitions` | Module 2 | `set_status()` correctly sets healthy/draining |
| `test_ledger_add_remove` | Module 2 | `ledger_add()` + `ledger_remove()` maintain correct entries |
| `test_ledger_drain` | Module 2 | `ledger_drain()` returns all entries and clears the ledger |
| `test_queue_put_writes_ledger` | Module 3 | After `put()`, task_ledger contains serialized entry |
| `test_queue_handler_removes_ledger` | Module 3 | After task completion, ledger entry is removed |
| `test_draining_rejects_queue_put` | Module 3 | When status is "draining", `put()` raises or rejects |
| `test_supervisor_detects_dead_process` | Module 5 | Process with `is_alive()==False` triggers rescue+respawn |
| `test_supervisor_detects_stale_heartbeat` | Module 5 | Stale heartbeat marks worker as draining |
| `test_supervisor_drain_timeout_kills` | Module 5 | After DRAIN_TIMEOUT, supervisor kills and respawns |
| `test_supervisor_recovery_from_draining` | Module 5 | Heartbeat resumes → status returns to healthy |
| `test_supervisor_rescue_tasks_via_redis` | Module 5 | Orphaned ledger entries are pushed to Redis stream |
| `test_supervisor_respawn_same_worker_id` | Module 5 | New process gets same worker_id as dead one |
| `test_health_reports_draining` | Module 7 | `/health/ready` returns 503 when worker is draining |
| `test_supervisor_status_endpoint` | Module 7 | `/supervisor/status` returns per-worker status JSON with heartbeat_age, draining_since, task_ledger_depth |

### Integration Tests

| Test | Description |
|---|---|
| `test_worker_crash_recovery_e2e` | Kill a worker process, verify supervisor detects death, rescues tasks, spawns replacement, replacement serves traffic |
| `test_stuck_worker_drain_cycle` | Simulate stuck event loop (no heartbeat), verify draining, verify rejection of new tasks, verify kill after timeout |
| `test_draining_client_retry` | Send task to draining worker, verify client gets error, retry reaches healthy worker |

### Test Data / Fixtures

```python
@pytest.fixture
def shared_state():
    """Create a real multiprocessing.Manager().dict() for testing."""
    manager = mp.Manager()
    state = manager.dict()
    yield state
    manager.shutdown()

@pytest.fixture
def mock_worker_state(shared_state):
    """Pre-populate a worker entry in shared state."""
    shared_state["TestWorker_0"] = {
        "pid": 12345,
        "heartbeat": time.time(),
        "status": "healthy",
        "draining_since": None,
        "task_ledger": [],
        "queue": [],
        "tcp_executing": [],
        "redis_executing": [],
        "broker_executing": [],
        "completed": [],
    }
    return shared_state

@pytest.fixture
def sample_ledger_entry():
    """A serialized task ledger entry for rescue testing."""
    from qw.wrappers import FuncWrapper
    task = FuncWrapper(some_async_func, arg1, arg2)
    return {
        "task_id": str(uuid.uuid4()),
        "payload": base64.b64encode(cloudpickle.dumps(task)).decode(),
        "enqueued_at": time.time(),
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] All unit tests pass (`pytest tests/test_supervisor.py tests/test_heartbeat.py -v`)
- [ ] All existing tests still pass (`pytest tests/ -v`)
- [ ] Supervisor detects a killed worker (`kill -9 <pid>`) within 30 seconds
  and spawns a replacement.
- [ ] Supervisor marks a stuck worker (simulated via `time.sleep()` in the
  event loop) as "draining" within `HEARTBEAT_TIMEOUT` seconds.
- [ ] A draining worker rejects new queued tasks; clients retry and succeed on
  a healthy worker.
- [ ] A worker draining for longer than `DRAIN_TIMEOUT` is forcefully killed
  and replaced.
- [ ] Orphaned tasks from a dead worker's task ledger appear in the Redis
  stream and are consumed by a healthy worker.
- [ ] A draining worker that recovers (heartbeat resumes) is promoted back to
  "healthy" and starts accepting tasks again.
- [ ] `/health/ready` returns 503 for draining workers.
- [ ] No breaking changes to existing TCP protocol, Redis stream protocol, or
  public API.
- [ ] Supervisor thread is a daemon thread and does not prevent clean shutdown.
- [ ] All new configuration parameters have sensible defaults and are
  documented.
- [ ] `qw info` TCP command shows the new fields (heartbeat, status,
  draining_since) for each worker.
- [ ] `/supervisor/status` HTTP endpoint returns per-worker status with
  heartbeat age, draining_since, and task_ledger depth (for Grafana).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
from qw.server import start_server             # verified: qw/server.py:807
from qw.server import QWorker                   # verified: qw/server.py:49
from qw.state import StateTracker               # verified: qw/state.py:13
from qw.queues import QueueManager              # verified: qw/queues/__init__.py:1
from qw.queues import QueueSizePolicy           # verified: qw/queues/__init__.py:2
from qw.queues.manager import QueueManager      # verified: qw/queues/manager.py:33
from qw.queues.policy import QueueSizePolicy    # verified: qw/queues/policy.py:13
from qw.health import HealthServer              # verified: qw/health.py:39
from qw.wrappers import QueueWrapper            # verified: qw/wrappers/__init__.py:10
from qw.wrappers import FuncWrapper             # verified: qw/wrappers/__init__.py:9
from qw.wrappers.base import QueueWrapper       # verified: qw/wrappers/base.py:10
from qw.executor import TaskExecutor            # verified: qw/executor/__init__.py:16
from qw.exceptions import QWException           # verified: qw/exceptions.py:15
from qw.exceptions import DiscardedTask         # verified: qw/exceptions.py:47
from qw.process import SpawnProcess             # verified: qw/process.py:61
from qw.process import JOB_LIST                 # verified: qw/process.py:24
from qw.conf import (                           # verified: qw/conf.py
    WORKER_DEFAULT_HOST,                        # line 15
    WORKER_DEFAULT_PORT,                        # line 16
    WORKER_DEFAULT_QTY,                         # line 18
    WORKER_QUEUE_SIZE,                          # line 19
    WORKER_HEALTH_ENABLED,                      # line 43
    WORKER_HEALTH_PORT,                         # line 44
    WORKER_REDIS,                               # line 76
    REDIS_WORKER_STREAM,                        # line 74
    REDIS_WORKER_GROUP,                         # line 73
    WORKER_USE_STREAMS,                         # line 72
)
import multiprocessing as mp                    # stdlib
import cloudpickle                              # dependency
import base64                                   # stdlib
import threading                                # stdlib
import time                                     # stdlib
from redis import asyncio as aioredis           # dependency, used in qw/server.py:13
from navconfig.logging import logging           # verified: used across all modules
from datamodel.parsers.json import json_encoder # verified: qw/server.py:36
```

### Existing Class Signatures

```python
# qw/process.py — SpawnProcess
class SpawnProcess:                                                    # line 61
    def __init__(self, args): ...                                      # line 62
        self.loop: asyncio.AbstractEventLoop                           # line 64
        self.host: str                                                 # line 67
        self.port: int                                                 # line 71
        self.worker: str  # f"{args.wkname}-{args.port}"              # line 72
        self.debug: bool                                               # line 73
        self._health_port: int                                         # line 90
        self._manager: mp.managers.SyncManager                        # line 92
        self._shared_state: dict  # Manager().dict() proxy            # line 93
        # Worker spawn loop: lines 94-107
        # JOB_LIST: module-level list[mp.Process]                      # line 24
    def start(self): ...                                               # line 231
    def terminate(self): ...                                           # line 263

# qw/server.py — start_server (module-level function)
def start_server(                                                      # line 807
    num_worker: int,
    host: str,
    port: int,
    debug: bool,
    notify_empty: bool,
    health_port: int = WORKER_HEALTH_PORT,
    shared_state=None,
) -> None: ...

# qw/server.py — QWorker
class QWorker:                                                         # line 49
    def __init__(                                                      # line 60
        self,
        host: str,
        port: int,
        worker_id: int = None,
        name: str = '',
        event_loop: asyncio.AbstractEventLoop = None,
        debug: bool = False,
        protocol: Any = None,
        notify_empty_stream: bool = False,
        empty_stream_minutes: int = 5,
        health_port: int = WORKER_HEALTH_PORT,
        shared_state=None,
    ): ...
        self._id: int = worker_id                                      # line 78
        self._running: bool = True                                     # line 81
        self._name: str                                                # line 84
        self._loop: asyncio.AbstractEventLoop                          # line 85
        self._pid: int = os.getpid()                                   # line 87
        self._shared_state = shared_state                              # line 89
        self._state: Optional[StateTracker]                            # line 90
        self.queue: QueueManager  # set in start()                     # line 330
        self._server: Callable                                         # line 86
    async def start(self): ...                                         # line 326
        # Creates QueueManager at line 330
        # Creates subscription_task at line 332
        # Creates TCP server at lines 336-352 (reuse_port=True)
        # Starts health server (worker_id==0 only) at line 365
        # Calls queue.fire_consumers() at line 381
        # Calls _server.serve_forever() at line 383
    async def shutdown(self): ...                                      # line 387
    async def connection_handler(self, reader, writer): ...            # line 719
        # Queued tasks: self.queue.put(task, id=task_uuid) at line 771
    async def handle_queue_wrapper(self, task, uid, writer): ...       # line 666
        # Queued tasks: self.queue.put(task, id=task.id) at line 679
    async def closing_writer(self, writer, result): ...                # line 791

# qw/state.py — StateTracker
class StateTracker:                                                    # line 13
    MAX_COMPLETED: int = 10                                            # line 25
    def __init__(                                                      # line 27
        self,
        shared_state: dict,
        worker_name: str,
        pid: int,
    ): ...
        self._state = shared_state                                     # line 28
        self._worker_name = worker_name                                # line 29
        self._pid = pid                                                # line 30
        # Initializes shared_state[worker_name] dict at lines 32-39
    def task_queued(self, task_id: str, function_name: str) -> None: ... # line 98
    def task_executing(self, task_id: str, source: str) -> None: ...   # line 122
    def task_completed(self, task_id: str, result: str, source: str) -> None: ... # line 167
    def get_state(self) -> dict: ...                                   # line 220
    def get_all_states(self) -> dict: ...                              # line 226

# qw/queues/manager.py — QueueManager
class QueueManager:                                                    # line 33
    def __init__(                                                      # line 36
        self,
        worker_name: str,
        state_tracker=None,
        policy: QueueSizePolicy | None = None,
    ) -> None: ...
        self.queue: asyncio.Queue                                      # line 47
        self.consumers: list                                           # line 50
        self._state = state_tracker                                    # line 62
        self._warn_active: bool                                        # line 64
        self.worker_name: str                                          # line 43
    async def put(self, task: QueueWrapper, id: str) -> bool: ...      # line 190
        # put_nowait at line 219
        # State tracking at lines 242-244
    async def queue_handler(self): ...                                 # line 350
        # task = await self.queue.get() at line 354
        # state.task_executing at line 359
        # self.queue.task_done() at line 413
        # state.task_completed at lines 420-422
    async def fire_consumers(self) -> None: ...                        # line 165
    async def empty_queue(self) -> None: ...                           # line 175
    def snapshot(self) -> dict: ...                                    # line 90

# qw/health.py — HealthServer
class HealthServer:                                                    # line 39
    def __init__(                                                      # line 49
        self,
        queue: QueueManager,
        host: str = "0.0.0.0",
        port: int = 8080,
        worker_name: str = "",
    ): ...
    async def start(self) -> None: ...                                 # line 65
    async def stop(self) -> None: ...                                  # line 77
    def _readiness(self) -> tuple[str, str]: ...                       # line 138
    def _liveness(self) -> tuple[str, str]: ...                        # line 165

# qw/wrappers/base.py — QueueWrapper
class QueueWrapper:                                                    # line 10
    _queued: bool = True                                               # line 11
    _debug: bool = False                                               # line 12
    def __init__(self, coro=None, *args, **kwargs): ...                # line 14
        self._id: uuid.UUID                                            # line 17
        self.retries: int = 0                                          # line 25
        self.coro = coro                                               # line 27
    @property
    def id(self) -> uuid.UUID: ...                                     # line 58
    @property
    def queued(self) -> bool: ...                                      # line 42
    def set_loop(self, event_loop): ...                                # line 65

# qw/exceptions.py
class QWException(Exception):                                          # line 15
    def __init__(self, message: str, status: int = 400, **kwargs): ... # line 20
class DiscardedTask(QWException):                                      # line 47
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `ProcessSupervisor.__init__()` | `SpawnProcess.__init__()` | Created as daemon thread after worker spawn loop | `qw/process.py:94-107` |
| `ProcessSupervisor.stop()` | `SpawnProcess.terminate()` | Called before worker termination | `qw/process.py:263` |
| `ProcessSupervisor._check_worker()` | `shared_state[worker_name]` | Reads heartbeat, status, task_ledger | `qw/state.py:32-39` |
| `ProcessSupervisor._rescue_tasks()` | Redis `xadd` | Pushes serialized tasks to `REDIS_WORKER_STREAM` | `qw/server.py:225` (existing consumer) |
| `ProcessSupervisor._respawn_worker()` | `start_server()` | `mp.Process(target=start_server, ...)` | `qw/server.py:807` |
| `StateTracker.update_heartbeat()` | `QWorker._heartbeat_loop()` | Called every `WORKER_HEARTBEAT_INTERVAL` seconds | `qw/server.py:326` (in `start()`) |
| `StateTracker.ledger_add()` | `QueueManager.put()` | Called after successful `put_nowait()` | `qw/queues/manager.py:219` |
| `StateTracker.ledger_remove()` | `QueueManager.queue_handler()` | Called in `finally` block after task completion | `qw/queues/manager.py:411-422` |
| `QWorker._is_draining()` | `shared_state[worker_name]["status"]` | Read check before queuing | `qw/server.py:771,679` |
| `HealthServer._readiness()` | `QWorker._is_draining()` | Returns 503 when draining | `qw/health.py:138` |
| `HealthServer._supervisor_status()` | `shared_state` (all workers) | Reads heartbeat/status/ledger for Grafana | `qw/health.py` (new) |

### Does NOT Exist (Anti-Hallucination)

- ~~`qw.supervisor`~~ — does not exist yet; must be created as a new module
- ~~`StateTracker.update_heartbeat()`~~ — does not exist; must be added
- ~~`StateTracker.set_status()`~~ — does not exist; must be added
- ~~`StateTracker.ledger_add()`~~ — does not exist; must be added
- ~~`StateTracker.ledger_remove()`~~ — does not exist; must be added
- ~~`StateTracker.ledger_drain()`~~ — does not exist; must be added
- ~~`QWorker._heartbeat_loop()`~~ — does not exist; must be added
- ~~`QWorker._is_draining()`~~ — does not exist; must be added
- ~~`SpawnProcess.supervisor`~~ — attribute does not exist; must be added
- ~~`shared_state[name]["heartbeat"]`~~ — field not in current schema (lines 32-39)
- ~~`shared_state[name]["status"]`~~ — field not in current schema
- ~~`shared_state[name]["draining_since"]`~~ — field not in current schema
- ~~`shared_state[name]["task_ledger"]`~~ — field not in current schema
- ~~`WORKER_HEARTBEAT_INTERVAL`~~ — not in `qw/conf.py`; must be added
- ~~`WORKER_HEARTBEAT_TIMEOUT`~~ — not in `qw/conf.py`; must be added
- ~~`WORKER_DRAIN_TIMEOUT`~~ — not in `qw/conf.py`; must be added
- ~~`SUPERVISOR_CHECK_INTERVAL`~~ — not in `qw/conf.py`; must be added
- ~~`SUPERVISOR_KILL_GRACE`~~ — not in `qw/conf.py`; must be added
- ~~`QueueManager._is_draining()`~~ — does not exist; draining check is on QWorker
- ~~`HealthServer._supervisor_status()`~~ — does not exist; must be added
- ~~`HealthServer.__init__(shared_state=...)`~~ — current `__init__` does not accept `shared_state`; must be extended

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Manager proxy mutation rule** (documented at `qw/state.py:6-8`):
  All shared_state updates MUST use full-value replacement, never nested
  mutation. Copy the dict, modify the copy, assign back:
  ```python
  d = dict(self._state[self._worker_name])
  d["heartbeat"] = time.time()
  self._state[self._worker_name] = d
  ```
- **Daemon thread**: The supervisor MUST be a daemon thread so it does not
  prevent clean process exit.
- **Async-first in workers**: The heartbeat loop in QWorker is an asyncio task
  (not a thread), consistent with all other background tasks in the worker.
- **Logging**: Use `logging.getLogger('QW.Supervisor')` and
  `logging.getLogger('QW.Heartbeat')`.
- **cloudpickle for serialization**: Task ledger entries use cloudpickle
  (same as existing Redis stream serialization in `qw/server.py:239`).
- **Redis stream format**: Rescued tasks must use the same `{"task": ..., "uid": ...}`
  format as `start_subscription()` expects (see `qw/server.py:235-236`).

### Known Risks / Gotchas

1. **Manager proxy performance**: Writing the full worker state dict on every
   heartbeat (every 5s) involves pickling. With 4 workers this is ~4 pickle
   operations per 5s — negligible. However, the task_ledger serialization (on
   every `put()`) involves `cloudpickle.dumps()` of the entire task object.
   **Mitigation**: This is the same serialization cost already paid when
   sending tasks over Redis streams. The base64 string is cached once; the
   task is not re-serialized on ledger removal.

2. **Race between supervisor and worker**: The supervisor runs in a thread on
   the parent process while workers run in child processes. Both read/write
   `shared_state`. The `Manager().dict()` proxy is thread-safe and
   process-safe (it serializes all operations through the Manager server
   process). No additional locking is needed.

3. **Task rescue idempotency**: A rescued task re-submitted via Redis stream
   might be a task that was already partially executed (it was dequeued from
   `asyncio.Queue` but the process died mid-execution). The ledger tracks
   enqueue, not dequeue. **Mitigation**: Remove from ledger at dequeue time
   (when `queue.get()` returns), not at completion time. This narrows the
   window to: task was dequeued and being executed when process died. For
   those tasks, duplicate execution is possible but acceptable — the
   alternative (not rescuing them) loses them entirely. Tasks that are
   not idempotent should handle this at the application layer.

4. **Heartbeat during heavy load**: If the event loop is saturated with tasks,
   the heartbeat coroutine might not get scheduled for longer than
   `HEARTBEAT_TIMEOUT`. **Mitigation**: Set `HEARTBEAT_TIMEOUT` high enough
   (30s default) relative to `HEARTBEAT_INTERVAL` (5s) to tolerate several
   missed beats. The supervisor only marks draining, not immediately killing.

5. **SO_REUSEPORT and client retry**: When a draining worker rejects a task,
   the client must reconnect. The next `SO_REUSEPORT` connection may land on
   the same draining worker again. **Mitigation**: This is statistically
   unlikely with N-1 healthy workers, and the client will retry again if it
   happens. The drain timeout (5 min) ensures the stuck worker is eventually
   replaced.

### External Dependencies

No new external dependencies. All functionality uses:
- `multiprocessing` (stdlib)
- `threading` (stdlib)
- `cloudpickle` (existing dependency)
- `redis.asyncio` (existing dependency)

---

## 8. Worktree Strategy

- **Isolation unit**: `per-spec` (all tasks run sequentially in one worktree).
- **Rationale**: Modules have strict linear dependencies (conf → state →
  queue_manager → server → supervisor → process → health → tests). No
  parallelization is safe.
- **Cross-feature dependencies**: None. This feature extends the existing
  infrastructure without conflicting with FEAT-001 through FEAT-004.

---

## 9. Open Questions

- [x] Should the task ledger entry be removed at dequeue time (narrower
  rescue window, fewer duplicates) or at completion time (wider rescue window,
  more duplicates)? — **Decision: Remove at dequeue time** (when
  `queue.get()` returns in `queue_handler`). This minimizes duplicate
  execution. Tasks that die mid-execution are lost (acceptable tradeoff).
- [x] Should the Supervisor expose its own HTTP endpoint for observability
  (e.g., `/supervisor/status`)? — **Decision: Yes.** Expose a
  `/supervisor/status` endpoint on the health port. Returns per-worker status
  (healthy/draining), heartbeat age, task_ledger depth, respawn count. Useful
  for Grafana dashboards monitoring worker health.
- [x] Should rescued tasks be given a higher priority or marked as "rescued"
  in the Redis stream so consumers can log/track them? — **Decision: No.**
  Rescued tasks are re-submitted as normal stream entries. No special marking.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-23 | Jesus Lara | Initial draft |
| 0.2 | 2026-04-23 | Jesus Lara | Resolved open questions: add `/supervisor/status` endpoint for Grafana; no special marking for rescued tasks |
