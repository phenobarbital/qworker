# Feature Specification: Expose Queue & Thread State via CLI

**Feature ID**: FEAT-002
**Date**: 2026-04-08
**Author**: Jesus Lara / Claude
**Status**: approved
**Target version**: 1.15.0
**Brainstorm**: `sdd/proposals/expose-queue-threads.brainstorm.md`

---

## 1. Motivation & Business Requirements

### Problem Statement

QWorker spawns N multiprocessing worker processes, each running its own asyncio event loop
with an internal asyncio queue, a Redis Streams consumer, and optionally a RabbitMQ broker
consumer. Currently there is **zero visibility** into what tasks are queued, executing, or
recently completed across any of these workers.

Operators must rely on log tailing to infer system state, which is slow, unreliable, and
impossible for remote monitoring. There is no programmatic way to answer "what is qworker
doing right now?"

### Goals
- Provide a `qw info` CLI command that shows a real-time snapshot of all tasks across all
  worker processes (queued, executing, recently completed).
- Cover all 4 execution paths: asyncio queue (deferred), direct TCP (immediate), Redis
  Streams (distributed), and RabbitMQ broker (event-driven).
- Support both human-readable (`rich` tables) and machine-parsable (`--json`) output.
- Support continuous monitoring via `--watch N` (poll every N seconds).
- Enable remote querying using the existing TCP command + signature auth pattern.

### Non-Goals (explicitly out of scope)
- Prometheus/OpenMetrics exporter (future work, can read from same shared state).
- Historical task analytics or persistent task logging.
- NotifyWorker state visibility (out of scope per brainstorm discussion).
- Modifying task execution behavior — this feature is read-only observability.

---

## 2. Architectural Design

### Overview

A `multiprocessing.Manager` owned by `SpawnProcess` provides a shared `DictProxy` that all
worker processes update at task lifecycle events. A new TCP socket command `info` (alongside
existing `health` and `check_state`) reads this shared dict and returns JSON. The `qw info`
CLI subcommand connects to a running qworker via TCP with signature authentication and
renders the result.

### Component Diagram
```
                          ┌─────────────────────┐
                          │   SpawnProcess       │
                          │  (process.py)        │
                          │                      │
                          │  mp.Manager()        │
                          │  shared_state: dict  │──────────────────┐
                          └──────┬───────────────┘                  │
                                 │ passes shared_state              │
                    ┌────────────┼────────────┐                     │
                    ▼            ▼            ▼                     │
             ┌──────────┐ ┌──────────┐ ┌──────────┐               │
             │ QWorker_0│ │ QWorker_1│ │ QWorker_N│               │
             │          │ │          │ │          │               │
             │ Queue ───┤ │ Queue ───┤ │ Queue ───┤               │
             │ TCP   ───┤ │ TCP   ───┤ │ TCP   ───┤               │
             │ Redis ───┤ │ Redis ───┤ │ Redis ───┤               │
             │ Broker───┤ │ Broker───┤ │ Broker───┤               │
             └──────────┘ └──────────┘ └──────────┘               │
                  │ updates    │ updates    │ updates              │
                  └────────────┴────────────┘                     │
                                                                   │
                          ┌─────────────────────┐                  │
                          │  qw info (CLI)       │                  │
                          │  (__main__.py)       │                  │
                          │                      │   TCP "info"     │
                          │  Connects via TCP  ──┼──────────────────┘
                          │  w/ signature auth   │   reads shared_state
                          │                      │
                          │  --json | rich table │
                          │  --watch N           │
                          └─────────────────────┘
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `SpawnProcess` (process.py:61) | extends | Add `mp.Manager()` creation, pass shared dict to workers |
| `start_server()` (server.py:740) | modifies | Accept `shared_state` as new parameter |
| `QWorker` (server.py:51) | extends | Store shared state ref, update at task events, add `info` TCP cmd |
| `QueueManager` (queues/manager.py:31) | extends | Accept shared state ref, report queue/execution state changes |
| `QClient.health()` (client.py:560) | pattern reuse | New `QClient.info()` follows same TCP query pattern |
| `signature_validation()` (server.py:487) | extends | Add `info` command to prefix dispatch (line 508-518) |
| `BrokerConsumer.wrap_callback()` (broker/rabbit.py:294) | extends | Instrument wrapped callback to update shared state |
| `__main__.py:main()` (line 24) | modifies | Add argparse subparsers (start, info) |
| `setup.py:127` | no change | Entry point `qw = qw.__main__:main` remains the same |

### Data Models
```python
from dataclasses import dataclass, field
from typing import Optional
import time

@dataclass
class TaskInfo:
    """Represents a single task's observable state."""
    task_id: str               # UUID from QueueWrapper._id
    function_name: str         # From repr(task) or task.func.__name__
    enqueued_at: float         # time.time() when queued/received
    started_at: Optional[float] = None  # time.time() when execution began
    completed_at: Optional[float] = None
    duration: Optional[float] = None    # seconds
    retries: int = 0           # From QueueWrapper.retries
    status: str = "queued"     # queued | executing | completed | failed
    result: str = ""           # "success" | "error" | error message
    source: str = ""           # "queue" | "tcp" | "redis" | "broker"

@dataclass
class WorkerState:
    """State of a single worker process."""
    worker_name: str           # QWorker._name
    pid: int                   # QWorker._pid
    queue_tasks: list = field(default_factory=list)      # TaskInfo dicts
    tcp_executing: list = field(default_factory=list)     # TaskInfo dicts
    redis_executing: list = field(default_factory=list)   # TaskInfo dicts
    broker_executing: list = field(default_factory=list)  # TaskInfo dicts
    completed: list = field(default_factory=list)         # Ring buffer, max 10
```

Note: These dataclasses define the schema. The actual shared state uses plain dicts
(JSON-serializable) because `multiprocessing.Manager` proxies work with basic types.

### New Public Interfaces
```python
# In qw/client.py — new method on QClient
class QClient:
    async def info(self) -> dict:
        """Query worker state via TCP 'info' command. Returns aggregated state dict."""
        ...

# In qw/server.py — new method on QWorker
class QWorker:
    async def worker_info(self, writer: asyncio.StreamWriter) -> None:
        """Handle 'info' TCP command. Returns shared state as JSON."""
        ...

# In qw/state.py — new module for state management
class StateTracker:
    """Thin wrapper around the shared dict for safe state updates."""
    def __init__(self, shared_state: dict, worker_name: str, pid: int): ...
    def task_queued(self, task_id: str, function_name: str): ...
    def task_executing(self, task_id: str, source: str): ...
    def task_completed(self, task_id: str, result: str, source: str): ...
    def get_state(self) -> dict: ...

# CLI entry point changes
# qw (no args) → starts server (backward compatible)
# qw info --host H --port P [--json] [--watch N] → queries state
```

---

## 3. Module Breakdown

### Module 1: State Tracker (`qw/state.py`)
- **Path**: `qw/state.py` (new file)
- **Responsibility**: Encapsulates all reads/writes to the `multiprocessing.Manager` shared
  dict. Provides methods for task lifecycle events (queued, executing, completed). Manages
  the completed-tasks ring buffer (max 10). Handles the function-name extraction from
  different wrapper types (FuncWrapper, TaskWrapper, QueueWrapper).
- **Depends on**: `multiprocessing.Manager` (stdlib), `qw/wrappers/base.py` (for type checks)

### Module 2: SpawnProcess Manager Integration (`qw/process.py` modification)
- **Path**: `qw/process.py` (modify existing)
- **Responsibility**: Create `multiprocessing.Manager()` and shared dict in
  `SpawnProcess.__init__()`. Pass shared dict to `start_server()` as additional argument.
  Cross-reference `JOB_LIST` PIDs with shared state to detect dead workers.
- **Depends on**: Module 1 (StateTracker schema/contract)

### Module 3: QWorker State Instrumentation (`qw/server.py` modification)
- **Path**: `qw/server.py` (modify existing)
- **Responsibility**: Accept shared state in `start_server()` and `QWorker.__init__()`.
  Create `StateTracker` instance. Instrument `connection_handler()` for direct TCP tasks,
  `start_subscription()` for Redis stream tasks. Add `worker_info()` TCP command handler.
  Add `info` prefix to `signature_validation()` dispatch. For localhost connections to the
  `info` command, allow unauthenticated access.
- **Depends on**: Module 1 (StateTracker), Module 2 (shared dict passed in)

### Module 4: QueueManager State Instrumentation (`qw/queues/manager.py` modification)
- **Path**: `qw/queues/manager.py` (modify existing)
- **Responsibility**: Accept `StateTracker` reference in `__init__()`. Call
  `state.task_queued()` in `put()`, update to executing in `queue_handler()`,
  call `state.task_completed()` in `finally` block of `queue_handler()`.
- **Depends on**: Module 1 (StateTracker), Module 3 (passes StateTracker to QueueManager)

### Module 5: Broker State Instrumentation (`qw/broker/` modification)
- **Path**: `qw/broker/rabbit.py` and `qw/broker/consumer.py` (modify existing)
- **Responsibility**: Accept optional `StateTracker` reference in `BrokerConsumer`. Instrument
  `wrap_callback()` to call `state.task_executing()` before callback and
  `state.task_completed()` after. Must be backward-compatible (StateTracker is optional — if
  not provided, broker works as before).
- **Depends on**: Module 1 (StateTracker)

### Module 6: CLI Subcommands (`qw/__main__.py` modification + `qw/cli/` new)
- **Path**: `qw/__main__.py` (modify), `qw/cli/__init__.py` (new), `qw/cli/info.py` (new)
- **Responsibility**: Refactor `__main__.py` to use argparse subparsers. `qw` (no subcommand)
  or `qw start` starts the server (backward compatible). `qw info` connects to a running
  worker via TCP `info` command with signature auth, receives JSON, renders output using
  `rich` for human-readable tables or raw JSON with `--json`. Implements `--watch N`
  poll-based continuous monitoring.
- **Depends on**: Module 3 (TCP `info` command exists), `rich` (new dependency)

### Module 7: QClient.info() Method (`qw/client.py` modification)
- **Path**: `qw/client.py` (modify existing)
- **Responsibility**: Add `async def info()` method following the same TCP query pattern as
  `health()` (client.py:560). Sends `info` command, receives JSON response. This provides
  programmatic access for Python code (not just CLI).
- **Depends on**: Module 3 (TCP `info` command exists)

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_state_tracker_init` | Module 1 | StateTracker initializes with empty state |
| `test_task_queued` | Module 1 | `task_queued()` adds entry with correct fields |
| `test_task_executing` | Module 1 | `task_executing()` moves task from queued to executing |
| `test_task_completed_success` | Module 1 | `task_completed()` moves to completed list |
| `test_task_completed_error` | Module 1 | Error result recorded correctly |
| `test_completed_ring_buffer` | Module 1 | 11th completion evicts oldest entry |
| `test_function_name_extraction` | Module 1 | Extracts name from FuncWrapper, TaskWrapper, QueueWrapper |
| `test_dead_worker_detection` | Module 1 | Worker with non-existent PID marked as "dead" |
| `test_info_json_output` | Module 6 | `--json` flag produces valid JSON |
| `test_info_text_output` | Module 6 | Default output produces rich table |
| `test_backward_compat_no_subcommand` | Module 6 | `qw` with no subcommand still starts server |

### Integration Tests
| Test | Description |
|---|---|
| `test_info_tcp_command` | Start a QWorker, send `info` TCP command, verify JSON response |
| `test_info_with_queued_task` | Queue a task, query `info`, verify task appears in queue section |
| `test_info_auth_required_remote` | Remote `info` query without signature is rejected |
| `test_info_localhost_no_auth` | Localhost `info` query succeeds without signature |
| `test_qclient_info` | Use `QClient.info()` to query worker state programmatically |

### Test Data / Fixtures
```python
import pytest
from unittest.mock import MagicMock
from multiprocessing import Manager

@pytest.fixture
def shared_state():
    """Provides a real multiprocessing.Manager shared dict for testing."""
    manager = Manager()
    state = manager.dict()
    yield state
    manager.shutdown()

@pytest.fixture
def state_tracker(shared_state):
    """Provides a StateTracker instance with a real shared dict."""
    from qw.state import StateTracker
    return StateTracker(shared_state, worker_name="TestWorker_0", pid=12345)

@pytest.fixture
def sample_task():
    """Provides a FuncWrapper task for testing."""
    from qw.wrappers import FuncWrapper
    async def dummy_func(): pass
    return FuncWrapper(host="localhost", func=dummy_func, queued=True)
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `qw info --host H --port P` connects to a running qworker and displays task state
- [ ] Output shows tasks grouped by source: asyncio queue, direct TCP, Redis streams, broker
- [ ] Each task entry shows: task_id (UUID), function name, enqueue/start time, worker PID, retry count
- [ ] `--json` flag outputs valid JSON with the same data
- [ ] `--watch N` re-queries every N seconds with screen refresh
- [ ] `qw` with no subcommand still starts the server (backward compatibility)
- [ ] Remote queries require signature authentication (same as `health`/`check_state`)
- [ ] Localhost queries to `info` work without authentication
- [ ] Last 10 completed tasks per worker are shown with duration and result status
- [ ] Dead worker processes are marked as "dead" in the output
- [ ] `QClient.info()` method works programmatically
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] `rich` is listed as a dependency in `setup.py`
- [ ] No measurable performance degradation in task execution (state updates < 1ms overhead)

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports
```python
# These imports have been confirmed to work:
from qw.process import SpawnProcess          # qw/__main__.py:19
from qw.server import start_server           # qw/process.py:22
from qw.queues import QueueManager           # qw/server.py:42
from qw.wrappers import QueueWrapper, FuncWrapper, TaskWrapper  # qw/server.py:43-44
from qw.executor import TaskExecutor         # qw/server.py:46
from qw.utils import cPrint, make_signature  # qw/server.py:27
from qw.utils.json import json_encoder       # qw/server.py:40
from qw.exceptions import QWException, ParserError, DiscardedTask  # qw/server.py:22-26
from qw.conf import (
    WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT, WORKER_DEFAULT_QTY,
    WORKER_QUEUE_SIZE, WORKER_SECRET_KEY, expected_message,
    REDIS_WORKER_STREAM, REDIS_WORKER_GROUP, WORKER_USE_STREAMS,
    WORKER_REDIS, NOTIFY_DEFAULT_PORT, WORKER_DISCOVERY_PORT
)  # qw/server.py:28-39, qw/__main__.py:10-17

# Broker imports:
from qw.broker.rabbit import RabbitMQConnection   # qw/broker/consumer.py:12
from qw.broker.consumer import BrokerConsumer      # usage in external code
from qw.broker.producer import BrokerManager       # usage in external code

# Stdlib:
import multiprocessing as mp          # qw/process.py:3
from collections import deque         # stdlib, for ring buffer
import argparse                       # qw/__main__.py:5
```

### Existing Class Signatures
```python
# qw/process.py:61
class SpawnProcess:
    def __init__(self, args):                          # line 62
        self.loop: asyncio.AbstractEventLoop           # line 64
        self.host: str                                 # line 67
        self.hostname: str                             # line 68
        self.address: str                              # line 69
        self.id: str  # uuid4                          # line 70
        self.port: int                                 # line 71
        self.worker: str  # "{wkname}-{port}"          # line 72
        self.debug: bool                               # line 73
        self.redis: Callable                           # line 75
        self.logger                                    # line 79
    def start(self):                                   # line 227
    def terminate(self):                               # line 259
    # Workers spawned at lines 90-103 via:
    #   mp.Process(target=start_server, name=f'{self.worker}_{i}',
    #              args=(i, args.host, args.port, args.debug, args.notify_empty))
    # JOB_LIST (module-level list) at line 24 tracks all processes

# qw/server.py:740
def start_server(num_worker, host, port, debug: bool, notify_empty: bool):
    # Creates QWorker and runs it — line 752-762
    # Current signature has 5 positional args

# qw/server.py:51
class QWorker:
    def __init__(self, host, port, worker_id, name, event_loop, debug,
                 protocol, notify_empty_stream, empty_stream_minutes):  # line 62-73
        self.host: str                                 # line 74
        self.port: int                                 # line 75
        self.debug: bool                               # line 76
        self.queue: QueueManager = None                # line 77, set in start()
        self._id: int = worker_id                      # line 78
        self._running: bool = True                     # line 79
        self._name: str                                # line 82
        self._pid: int = os.getpid()                   # line 85
        self._server: Callable = None                  # line 84
        self.logger                                    # line 88

    @property
    def name(self) -> str:                             # line 92
    async def start(self):                             # line 313
        # self.queue = QueueManager(worker_name=self._name)  # line 317
        # self.subscription_task = self._loop.create_task(self.start_subscription())  # line 319
    async def start_subscription(self):                # line 195 — Redis stream consumer loop
    async def connection_handler(self, reader, writer): # line 652
    async def signature_validation(self, reader, writer): # line 487
        # Dispatches on prefix: b'health' (line 508), b'check_state' (line 514)
        # Else: parses msglen, reads payload, checks signature (lines 520-543)
    async def worker_health(self, writer):             # line 420
    async def worker_check_state(self, writer):        # line 438
    async def handle_queue_wrapper(self, task, uid, writer): # line 606
    async def response_keepalive(self, writer, status): # line 403
    async def closing_writer(self, writer, result):    # line 724
    def check_signature(self, payload: bytes) -> bool: # line 396
    def start_redis(self):                             # line 114

# qw/queues/manager.py:31
class QueueManager:
    def __init__(self, worker_name: str):              # line 35
        self.logger                                    # line 36
        self.worker_name: str                          # line 37
        self.queue: asyncio.Queue                      # line 38, maxsize=WORKER_QUEUE_SIZE
        self.consumers: list                           # line 41
        self._callback: Union[Callable, Awaitable]     # line 46

    async def put(self, task: QueueWrapper, id: str):  # line 103
        # self.queue.put_nowait(task)                  # line 111
    async def get(self) -> QueueWrapper:               # line 124
    async def queue_handler(self):                     # line 137
        # Main consumer loop: gets task, runs TaskExecutor, handles retries
        # task = await self.queue.get()                # line 141
        # executor = TaskExecutor(task); result = await executor.run()  # line 147-148
        # Retry logic at lines 164-179
        # self.queue.task_done() in finally            # line 197
        # self._callback(task, result=result) in finally # line 198-199
    async def fire_consumers(self):                    # line 82
    async def empty_queue(self):                       # line 90
    def size(self) -> int:                             # line 73
    def empty(self) -> bool:                           # line 76
    def full(self) -> bool:                            # line 79

# qw/wrappers/base.py:10
class QueueWrapper:
    _queued: bool = True                               # line 11
    _debug: bool = False                               # line 12
    def __init__(self, coro=None, *args, **kwargs):    # line 14
        self._id: uuid.UUID                            # line 17
        self.retries: int = 0                          # line 25
        self.coro                                      # line 27
    @property
    def id(self) -> uuid.UUID:                         # line 58
    @property
    def queued(self) -> bool:                          # line 42
    def add_retries(self):                             # line 38
    def set_loop(self, event_loop):                    # line 65

# qw/wrappers/func.py:7
class FuncWrapper(QueueWrapper):
    def __init__(self, host, func, *args, **kwargs):   # line 9
        self.host: str                                 # line 12 (first positional)
        self.func: Callable                            # line 13
    def __repr__(self) -> str:                         # line 29 — '<%s> from %s' % (func.__name__, host)
    def __str__(self) -> str:                          # line 32 — same format
    def retry(self):                                   # line 35

# qw/executor/__init__.py:16
class TaskExecutor:
    def __init__(self, task, *args, **kwargs):          # line 17
        self.task                                       # line 21
        self.semaphore: asyncio.Semaphore               # line 22
    async def run(self) -> Any:                         # line 84
        # Dispatches on type: FuncWrapper/QueueWrapper, TaskWrapper, awaitable, blocking

# qw/client.py:58
class QClient:
    def __init__(self, worker_list=None, timeout=5):    # line 72
        self._workers: list                             # line 81-88
        self._worker_list: itertools.cycle              # line 82
    async def health(self) -> dict:                     # line 560
        # Pattern: encode command → get_worker_connection() → sendto_worker() → read result
    async def get_worker_connection(self):              # exists (used by health)
    async def sendto_worker(self, data, writer):        # exists (used by health)
    async def close(self, writer):                      # exists (used by health)

# qw/broker/consumer.py:20
class BrokerConsumer(RabbitMQConnection):
    _name_: str = "broker_consumer"                     # line 24
    def __init__(self, dsn, timeout, callback, **kwargs): # line 26
        self._callback_: Callable                       # line 40
    async def subscriber_callback(self, message, body): # line 42
    async def subscribe_to_events(self, exchange, queue_name, routing_key,
                                  callback, ...):       # line 72

# qw/broker/rabbit.py:294
class RabbitMQConnection:
    def wrap_callback(self, callback, requeue_on_fail=False,
                      max_retries=3) -> Callable:       # line 294
        # Returns wrapped_callback that processes message, calls user callback,
        # handles ack/nack/retry — lines 304-369

# qw/utils/functions.py:14
def make_signature(message: str, key: str) -> str:      # line 14
    # HMAC-SHA512, base64 encoded — uses WORKER_SECRET_KEY
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `StateTracker` | `QWorker` | Constructor injection (`self._state = StateTracker(...)`) | server.py:62 (init) |
| `StateTracker` | `QueueManager` | Constructor injection (`self._state = state_tracker`) | queues/manager.py:35 (init) |
| `StateTracker` | `SpawnProcess` | Creates Manager + shared dict, passes to `start_server()` | process.py:90 (worker spawn loop) |
| `QWorker.worker_info()` | `signature_validation()` | New `elif prefix == b'info':` branch | server.py:508-518 (dispatch) |
| `QClient.info()` | `QWorker.worker_info()` | TCP connection, same pattern as `health()` | client.py:560 (health pattern) |
| `BrokerConsumer` | `StateTracker` | Optional kwarg in `__init__()`, used in `wrap_callback()` | broker/consumer.py:26 |
| CLI `qw info` | `QClient.info()` | Instantiates QClient, calls info(), renders output | __main__.py:24 (entry point) |

### Does NOT Exist (Anti-Hallucination)
- ~~`QWorker._state`~~ — does not exist yet; must be added (Module 3)
- ~~`QWorker.worker_info()`~~ — does not exist yet; must be added (Module 3)
- ~~`QueueManager._state`~~ — does not exist yet; must be added (Module 4)
- ~~`SpawnProcess.manager`~~ — does not exist yet; must be added (Module 2)
- ~~`qw/state.py`~~ — does not exist; must be created (Module 1)
- ~~`qw/cli/`~~ — directory does not exist; must be created (Module 6)
- ~~`QueueWrapper.function_name`~~ — no such property. Use `repr(task)` or `task.func.__name__` for FuncWrapper
- ~~`QueueWrapper.enqueued_at`~~ — no timestamp tracking; StateTracker records this externally
- ~~`QueueWrapper.started_at`~~ — no such attribute; StateTracker records this externally
- ~~`QClient.info()`~~ — does not exist yet; must be added (Module 7)
- ~~`BrokerConsumer._state`~~ — does not exist yet; must be added (Module 5)
- ~~`qw info` subcommand~~ — CLI currently has no subcommands at all
- ~~`argparse` subparsers~~ — not currently used in `__main__.py`; uses flat parser

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- **TCP command pattern**: Follow the existing `health`/`check_state` dispatch in
  `signature_validation()` (server.py:508-518). The `info` command is a new `elif` branch.
- **Response pattern**: Use `response_keepalive()` (server.py:403) with `json_encoder()` for
  the `info` response, same as `worker_health()` does.
- **Client query pattern**: Follow `QClient.health()` (client.py:560) for `QClient.info()`.
  Same flow: encode command → connect → send → read → decode.
- **Backward-compatible CLI**: Use `argparse` subparsers with `set_defaults(func=...)`.
  When no subcommand is given, default to the current server-start behavior.
- **SharedDict updates must be atomic**: Each update should write a complete new list to the
  shared dict key (not mutate in place), because Manager proxies don't detect nested mutations.

### Known Risks / Gotchas
- **Manager proxy nested mutation**: `shared_state[worker_name]["queue"].append(task)` will
  NOT propagate. Must reassign the whole value: `d = shared_state[worker_name]; d["queue"] = [...]; shared_state[worker_name] = d`.
  Alternatively, use `Manager.list()` for nested lists.
- **Manager serialization**: The shared dict values must be picklable. Use plain dicts/lists,
  not dataclasses or custom objects.
- **Process startup order**: The Manager must be created BEFORE `mp.Process()` calls so the
  shared dict is available. This is naturally satisfied since `SpawnProcess.__init__()` runs
  sequentially.
- **Rich dependency**: `rich` must be listed in `install_requires` (not extras). It's a
  widely-used, lightweight library with no transitive issues.
- **Localhost detection for auth bypass**: Check `peer_addr` in `signature_validation()`.
  If the connection comes from `127.0.0.1` or `::1` and the command is `info`, skip auth.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `rich` | `>=13.0` | Pretty table output for `qw info` human-readable mode |

No other new dependencies — `multiprocessing.Manager`, `argparse` subparsers, `collections.deque`
are all stdlib.

---

## Worktree Strategy

- **Default isolation**: `per-spec` (all tasks sequential in one worktree)
- **Rationale**: The shared state dict threads through `SpawnProcess → start_server → QWorker → QueueManager`. These changes are tightly coupled — the `start_server()` signature change in Module 2 is required by Module 3, which is required by Module 4. Parallel implementation would cause constant merge conflicts.
- **Exception**: Module 7 (`QClient.info()`) and Module 6 (CLI) could theoretically be developed in parallel once Module 3 (TCP `info` command) is done, but the savings are marginal.
- **Cross-feature dependencies**: None. FEAT-001 (uv-migration) is build-system only and does not conflict.

---

## 8. Open Questions

All open questions from brainstorm have been resolved:
- `rich` will be added as a dependency (confirmed)
- `info` TCP command allows unauthenticated localhost access (confirmed)
- Dead workers marked as "dead" in output (confirmed)
- Broker (RabbitMQ) state tracking included in v1 (confirmed)

No remaining open questions.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-08 | Jesus Lara / Claude | Initial draft from brainstorm |
