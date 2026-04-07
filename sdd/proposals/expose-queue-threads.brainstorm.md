# Brainstorm: Expose Queue & Thread State via CLI

**Date**: 2026-04-08
**Author**: Jesus Lara / Claude
**Status**: exploration
**Recommended Option**: Option A

---

## Problem Statement

QWorker spawns N multiprocessing worker processes, each with its own asyncio event loop,
asyncio queue, and Redis Stream consumer. There is currently **no way to inspect** what tasks
are queued, executing, or recently completed across any of these workers.

Operators and developers need a CLI command (`qw info`) to get a real-time snapshot of:
- Tasks waiting in each worker's asyncio queue (deferred execution path)
- Tasks currently being executed via direct TCP connections (immediate execution path)
- Tasks currently being executed from Redis Streams (distributed execution path)
- Tasks currently being processed via RabbitMQ broker (event execution path)
- Last N completed tasks per worker (execution history)

**Who is affected**: DevOps, developers debugging production, anyone monitoring qworker clusters.

**Why now**: As qworker deployments grow, lack of observability into internal queues becomes
a critical operational blind spot.

## Constraints & Requirements

- `qw` with no subcommand MUST still start the server (backward compatibility)
- Remote querying must follow existing `make_signature`/`SECRET_KEY` auth pattern
- Output must support both human-readable text and JSON (`--json` flag)
- Each worker process is a separate `multiprocessing.Process` — IPC is required
- Must not degrade task execution performance (state tracking overhead must be minimal)
- Watch mode (`--watch`) uses poll-based approach with configurable interval
- Last 10 completed tasks retained per worker (ring buffer)
- Task info includes: task.id (UUID), function name, enqueue time, worker PID, retry count
- Results grouped by source: asyncio queue → direct TCP → Redis streams → broker

---

## Options Explored

### Option A: multiprocessing.Manager Shared Dict + TCP Command Extension

`SpawnProcess` (the parent orchestrator) creates a `multiprocessing.Manager()` with a shared
`DictProxy`. Each `QWorker` process receives a reference to this shared dict and updates it
at task lifecycle events (enqueue, start execution, complete, fail). A new TCP socket command
`info` is added alongside existing `health` and `check_state` commands — it reads from the
shared dict and returns aggregated state as JSON. The `qw info` CLI subcommand connects to
a running qworker via TCP (with signature auth) and displays the result.

Pros:
- Leverages existing TCP command pattern (`health`, `check_state`) — minimal new surface area
- `multiprocessing.Manager` is stdlib — no new dependencies
- Central shared dict gives a single consistent view across all workers
- Auth reuses proven `make_signature` + `SECRET_KEY` mechanism
- Parent process owns the Manager — natural fit since `SpawnProcess` already orchestrates workers

Cons:
- `multiprocessing.Manager` uses a background server process (adds one extra process)
- Shared dict access is proxied (small serialization overhead per update)
- Manager process is a single point of failure for state (not for execution)

Effort: Medium

Libraries / Tools:
| Package | Purpose | Notes |
|---|---|---|
| `multiprocessing.Manager` | Shared state dict across processes | stdlib, no new dep |
| `argparse` (subparsers) | Add `info` subcommand to CLI | already used in `__main__.py` |
| `rich` (optional) | Pretty table output for human-readable mode | optional enhancement |

Existing Code to Reuse:
- `qw/server.py:396-401` — `check_signature()` method for auth validation
- `qw/server.py:508-518` — TCP command dispatch pattern (`health`, `check_state`)
- `qw/server.py:420-436` — `worker_health()` as template for new `worker_info()` response
- `qw/__main__.py:24-92` — argparse setup, extend with subparsers
- `qw/process.py:61-103` — `SpawnProcess.__init__()`, add Manager creation here
- `qw/utils/functions.py:14-22` — `make_signature()` for client-side auth

---

### Option B: Dedicated Management HTTP Endpoint (aiohttp)

Each worker spawns a lightweight aiohttp server on a secondary management port
(e.g., `port + 1000`). Each exposes a `/api/v1/worker/info` REST endpoint returning
JSON. The CLI iterates over known worker ports, queries each, and aggregates.

Pros:
- REST API is more extensible (future: Prometheus metrics, webhooks)
- HTTP is firewall/proxy-friendly for remote monitoring
- Each worker is independently queryable
- Familiar pattern for web-oriented teams

Cons:
- Each worker needs an additional listening port (N workers = N extra ports)
- New dependency on aiohttp within the worker process (currently only in broker)
- More complex network topology — port management across workers
- No single aggregated view without a separate aggregator
- Higher memory footprint per worker

Effort: High

Libraries / Tools:
| Package | Purpose | Notes |
|---|---|---|
| `aiohttp` | HTTP server per worker | new dep in worker process |
| `argparse` (subparsers) | CLI `info` subcommand | already used |

Existing Code to Reuse:
- `qw/broker/producer.py:252-295` — REST endpoint pattern (BrokerManager)
- `qw/server.py:420-461` — health/state response structure

---

### Option C: Redis-Based State Aggregation

Each worker periodically publishes its internal state to a Redis hash
(e.g., `QW:state:{worker_name}`). The `qw info` CLI reads directly from Redis
without connecting to any worker TCP port. A background asyncio task in each worker
updates its Redis hash every 1-2 seconds.

Pros:
- Fully decoupled from worker processes — CLI only needs Redis access
- Works even if a worker is stuck or unresponsive (last known state)
- No IPC complexity — Redis is already a dependency
- Scales naturally with multiple hosts

Cons:
- State is eventually consistent (1-2 second lag)
- Adds Redis write load proportional to number of workers × update frequency
- Requires Redis connectivity for local-only deployments (overkill)
- No auth mechanism for the CLI (anyone with Redis access can read)
- Completed tasks list in Redis needs TTL management

Effort: Medium

Libraries / Tools:
| Package | Purpose | Notes |
|---|---|---|
| `redis.asyncio` | State storage in Redis hashes | already a dependency |
| `argparse` (subparsers) | CLI `info` subcommand | already used |

Existing Code to Reuse:
- `qw/server.py:114-121` — `start_redis()` connection pool setup
- `qw/process.py:152-163` — `start_redis()` in SpawnProcess
- `qw/conf.py:49-57` — Redis configuration constants

---

### Option D: Hybrid — Manager + Redis State Broadcast

Combine Option A (Manager for local IPC) with periodic Redis state broadcast.
The shared dict is the source of truth; a background task in SpawnProcess publishes
a snapshot to Redis every N seconds for remote/cross-host monitoring.

Pros:
- Best of both worlds: fast local queries + remote visibility
- Graceful degradation: CLI works locally even if Redis is down
- Remote dashboards can read from Redis without TCP

Cons:
- Most complex implementation
- Two state paths to maintain and keep consistent

Effort: High

Libraries / Tools:
| Package | Purpose | Notes |
|---|---|---|
| `multiprocessing.Manager` | Local shared state | stdlib |
| `redis.asyncio` | Remote state broadcast | already a dependency |

Existing Code to Reuse:
- All from Option A + Option C

---

## Recommendation

**Option A** is recommended because:

1. **Minimal new surface area**: Extends the existing TCP command pattern (`health`, `check_state`)
   which is already proven and authenticated. No new ports, no new protocols.

2. **Stdlib only**: `multiprocessing.Manager` is part of Python — no new dependencies.

3. **Natural ownership**: `SpawnProcess` already orchestrates all workers and tracks `JOB_LIST`.
   Adding a Manager here is architecturally consistent.

4. **Auth built-in**: The TCP command path already enforces `make_signature` + `SECRET_KEY`.
   The new `info` command inherits this for free via `signature_validation()`.

5. **Acceptable tradeoff**: The Manager's background server process adds minimal overhead
   compared to the N worker processes already running. The proxied dict access adds
   microsecond-level latency per state update — negligible versus task execution time.

Option D (hybrid) is a natural evolution path if cross-host Redis visibility is needed later,
but for v1 the TCP approach satisfies the remote querying requirement.

---

## Feature Description

### User-Facing Behavior

**CLI command**: `qw info --host <host> --port <port> [--json] [--watch N]`

Default output (human-readable table):

```
QWorker Status — worker-8888 (host: 10.0.0.5, port: 8888)
Workers: 4 | Queue Size: 4

=== Asyncio Queue (Deferred) ===
Worker              PID    Task ID                              Function        Enqueued At          Retries  Status
Worker-8888_0       12345  a1b2c3d4-...                         process_data    2026-04-08 14:23:01  0        queued
Worker-8888_0       12345  e5f6g7h8-...                         send_email      2026-04-08 14:23:05  1        executing

=== Direct TCP (Immediate) ===
Worker              PID    Task ID                              Function        Started At           Status
Worker-8888_1       12346  i9j0k1l2-...                         run_report      2026-04-08 14:23:10  executing

=== Redis Streams ===
Worker              PID    Task ID                              Function        Started At           Status
Worker-8888_2       12347  m3n4o5p6-...                         sync_users      2026-04-08 14:23:08  executing

=== RabbitMQ Broker ===
Worker              PID    Task ID                              Function        Started At           Status
(none)

=== Recently Completed (last 10 per worker) ===
Worker              PID    Task ID                              Function        Completed At         Duration   Result
Worker-8888_0       12345  q7r8s9t0-...                         import_csv      2026-04-08 14:22:55  3.2s       success
Worker-8888_1       12346  u1v2w3x4-...                         validate        2026-04-08 14:22:50  0.8s       error
```

With `--json`: outputs the same data as a JSON object.
With `--watch 2`: re-queries every 2 seconds, clears and redraws (like `watch` command).

### Internal Behavior

**State tracking flow:**

1. `SpawnProcess.__init__()` creates a `multiprocessing.Manager()` and a shared `DictProxy`
   structured as:
   ```python
   {
       "worker_name": {
           "pid": int,
           "queue": [                # asyncio queue tasks
               {"task_id": str, "function": str, "enqueued_at": float, "retries": int, "status": str}
           ],
           "tcp_executing": [        # direct TCP tasks currently running
               {"task_id": str, "function": str, "started_at": float}
           ],
           "redis_executing": [      # Redis stream tasks currently running
               {"task_id": str, "function": str, "started_at": float}
           ],
           "broker_executing": [     # RabbitMQ broker tasks currently running
               {"task_id": str, "function": str, "started_at": float}
           ],
           "completed": [            # ring buffer, max 10
               {"task_id": str, "function": str, "completed_at": float, "duration": float, "result": str}
           ]
       }
   }
   ```

2. The shared dict reference is passed to `start_server()` as an additional argument,
   then to `QWorker.__init__()`, which stores it as `self._state`.

3. **Update points** in existing code (instrumented, not restructured):
   - `QueueManager.put()` → add entry to `queue` list with status `queued`
   - `QueueManager.queue_handler()` → update status to `executing` when dequeued,
     move to `completed` when done
   - `QWorker.connection_handler()` → add to `tcp_executing` when direct-executing,
     move to `completed` when done
   - `QWorker.start_subscription()` → add to `redis_executing` when task received,
     move to `completed` when done
   - `BrokerConsumer` callback → add to `broker_executing`, move to `completed`

4. **TCP command `info`**: Added to `signature_validation()` alongside `health`/`check_state`.
   Reads the shared dict and returns JSON via `response_keepalive()`.

5. **CLI `qw info`**: Uses `argparse` subparsers. Connects to worker TCP port, sends
   authenticated `info` command, receives JSON, formats output.

### Edge Cases & Error Handling

- **Worker process dies**: Its entry in the shared dict becomes stale. The `info` command
  cross-references with `JOB_LIST` PIDs to mark dead workers.
- **Manager process dies**: State queries fail gracefully — `qw info` reports
  "state unavailable" but workers continue executing normally.
- **Queue empty**: Sections show "(none)" instead of empty tables.
- **Auth failure**: Same behavior as existing commands — connection refused message.
- **Watch mode + connection loss**: Displays error, retries on next poll interval.
- **Completed list overflow**: When >10 entries, oldest is removed (collections.deque with maxlen=10).
- **Task has no function name**: Falls back to `repr(task)` or `"<unknown>"`.

---

## Capabilities

### New Capabilities
- `worker-state-tracking`: Shared state dict via multiprocessing.Manager tracking task lifecycle
- `cli-info-command`: `qw info` subcommand with text/JSON output and watch mode
- `tcp-info-protocol`: New TCP socket command `info` for querying worker state

### Modified Capabilities
- `spawn-process`: Extended to create and manage multiprocessing.Manager
- `qworker-server`: Extended to update shared state at task lifecycle points
- `queue-manager`: Extended to report task state changes to shared dict
- `cli-entry-point`: Refactored from flat argparse to subparser-based CLI

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `qw/process.py:SpawnProcess` | extends | Add Manager creation, pass shared dict to workers |
| `qw/server.py:QWorker` | extends | Accept shared dict, update state at task events, add `info` TCP cmd |
| `qw/server.py:start_server` | modifies | Accept shared dict as new parameter |
| `qw/queues/manager.py:QueueManager` | extends | Accept shared dict, report queue state changes |
| `qw/__main__.py:main` | modifies | Refactor to argparse subparsers (start + info) |
| `qw/broker/consumer.py:BrokerConsumer` | extends | Report broker task state to shared dict |
| `qw/client.py:QClient` | extends | Add `info()` method for programmatic state queries |

---

## Code Context

### User-Provided Code
(No code snippets provided during brainstorming.)

### Verified Codebase References

#### Classes & Signatures
```python
# From qw/process.py:61
class SpawnProcess:
    def __init__(self, args):  # line 62
        self.host: str  # line 67
        self.port: int  # line 71
        self.worker: str  # line 72 — format: "{wkname}-{port}"
        self.id = str(uuid.uuid4())  # line 70
        # Workers spawned at lines 90-103 via mp.Process(target=start_server, ...)

    def start(self):  # line 227
    def terminate(self):  # line 259

# From qw/server.py:51
class QWorker:
    def __init__(self, host, port, worker_id, name, event_loop, debug, ...):  # line 62
        self._name = name or mp.current_process().name  # line 82
        self._pid = os.getpid()  # line 85
        self.queue = None  # line 77 — set in start() to QueueManager instance

    async def start(self):  # line 313 — creates QueueManager, starts subscription
    async def start_subscription(self):  # line 195 — Redis stream consumer loop
    async def connection_handler(self, reader, writer):  # line 652
    async def signature_validation(self, reader, writer):  # line 487
    async def worker_health(self, writer):  # line 420
    async def worker_check_state(self, writer):  # line 438
    async def handle_queue_wrapper(self, task, uid, writer):  # line 606
    def check_signature(self, payload: bytes) -> bool:  # line 396

# From qw/queues/manager.py:31
class QueueManager:
    def __init__(self, worker_name: str):  # line 35
        self.queue: asyncio.Queue  # line 38 — maxsize=WORKER_QUEUE_SIZE
        self.consumers: list  # line 41

    async def put(self, task: QueueWrapper, id: str):  # line 103
    async def get(self) -> QueueWrapper:  # line 124
    async def queue_handler(self):  # line 137 — main consumer loop
    async def fire_consumers(self):  # line 82
    def size(self) -> int:  # line 73
    def empty(self) -> bool:  # line 76
    def full(self) -> bool:  # line 79

# From qw/wrappers/base.py:10
class QueueWrapper:
    _queued: bool = True  # line 11
    _id: uuid.UUID  # line 17
    retries: int = 0  # line 25

    @property
    def id(self) -> uuid.UUID:  # line 58
    @property
    def queued(self) -> bool:  # line 42

# From qw/wrappers/func.py:7
class FuncWrapper(QueueWrapper):
    def __init__(self, host, func, *args, **kwargs):  # line 9
        self.func  # line 13 — the actual callable
    def __repr__(self) -> str:  # line 29 — returns '<func_name> from host'
    def retry(self):  # line 35

# From qw/executor/__init__.py:16
class TaskExecutor:
    def __init__(self, task, *args, **kwargs):  # line 17
    async def run(self) -> Any:  # line 84 — main execution method

# From qw/broker/consumer.py (BrokerConsumer)
class BrokerConsumer(RabbitMQConnection):
    def __init__(self, ..., callback):  # line 26
    async def subscribe_to_events(self):  # line 72
    # Callback receives deserialized message — instrumentation point

# From qw/broker/rabbit.py (RabbitMQConnection)
class RabbitMQConnection:
    async def wrap_callback(self, callback, message):  # line 294 — wraps user callback with ack/nack
    async def process_message(self, message):  # line 253 — deserialize
```

#### Verified Imports
```python
# These imports have been confirmed to work:
from qw.process import SpawnProcess  # qw/__main__.py:19
from qw.server import start_server  # qw/process.py:22
from qw.queues import QueueManager  # qw/server.py:42
from qw.wrappers import QueueWrapper, FuncWrapper, TaskWrapper  # qw/server.py:43-44
from qw.executor import TaskExecutor  # qw/server.py:46
from qw.utils import cPrint, make_signature  # qw/server.py:27
from qw.utils.json import json_encoder  # qw/server.py:40
from qw.conf import (
    WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT, WORKER_DEFAULT_QTY,
    WORKER_QUEUE_SIZE, WORKER_SECRET_KEY, expected_message,
    REDIS_WORKER_STREAM, REDIS_WORKER_GROUP, WORKER_USE_STREAMS,
    WORKER_REDIS
)  # qw/server.py:28-39
```

#### Key Attributes & Constants
- `QueueWrapper._id` → `uuid.UUID` (qw/wrappers/base.py:17)
- `QueueWrapper.retries` → `int` (qw/wrappers/base.py:25)
- `FuncWrapper.func` → `Callable` (qw/wrappers/func.py:13)
- `QWorker._name` → `str` (qw/server.py:82)
- `QWorker._pid` → `int` (qw/server.py:85)
- `WORKER_QUEUE_SIZE` → `int`, default 4 (qw/conf.py:19)
- `WORKER_SECRET_KEY` → `str` (qw/conf.py:46)
- `expected_message` → `str` (qw/conf.py:45)

### Does NOT Exist (Anti-Hallucination)
- ~~`QWorker._state`~~ — does not exist yet, must be added
- ~~`QueueManager._state`~~ — does not exist yet, must be added
- ~~`SpawnProcess.manager`~~ — does not exist yet, must be added
- ~~`QueueWrapper.function_name`~~ — no such property; use `repr(task)` or `task.func.__name__` for FuncWrapper
- ~~`QueueWrapper.enqueued_at`~~ — no timestamp tracking exists; must be added
- ~~`qw info` subcommand~~ — CLI has no subcommands currently
- ~~`qw/cli.py`~~ — no separate CLI module exists
- ~~`QWorker.worker_info()`~~ — does not exist yet, must be added
- ~~`BrokerConsumer._state`~~ — does not exist yet, must be added

---

## Parallelism Assessment

- **Internal parallelism**: Moderate. The feature touches multiple files but changes are
  interdependent (shared dict flows from SpawnProcess → QWorker → QueueManager). The CLI
  subcommand (`qw info`) is independent from the state-tracking instrumentation.
- **Cross-feature independence**: No conflicts with in-flight specs (uv-migration is
  build-system only).
- **Recommended isolation**: `per-spec` — tasks should run sequentially in one worktree
  because the shared dict threading through SpawnProcess → QWorker → QueueManager
  creates tight dependencies between changes.
- **Rationale**: The state dict is passed through constructors across 3 files. Parallel
  implementation of these would cause constant merge conflicts. However, the CLI
  subcommand can be developed as the last task once the TCP `info` command works.

---

## Open Questions

- [ ] Should `rich` be added as a dependency for pretty CLI output, or keep it plain text with manual formatting? — *Owner: Jesus*
- [ ] Should the `info` TCP command work without auth for localhost connections (convenience for local debugging)? — *Owner: Jesus*
- [ ] What should `qw info` show when a worker process has crashed but its entry is still in the shared dict? Mark as "dead" or remove? — *Owner: Jesus*
- [ ] Should the broker (RabbitMQ) state tracking be included in v1 or deferred to v2? — *Owner: Jesus*
