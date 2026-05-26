---
type: feature
base_branch: dev
---

# Feature Specification: QWorker Query Handler

**Feature ID**: FEAT-007
**Date**: 2026-05-26
**Author**: Jesus Lara
**Status**: draft
**Target version**: TBD

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource's FEAT-101 (MultiQuery Remote Execution) introduced a `RemoteExecutor` that
offloads queries to a remote qworker server via `QClient.run()`. The querysource side is
complete — `RemoteExecutor` calls
`QClient.run("querysource.remote.query_handler", slug, conditions=conditions)` — but the
qworker side has **no handler to receive and execute these queries**.

**Critical gap**: qworker currently dispatches tasks by serializing Python function objects
via cloudpickle. There is no string-based handler registry. When `QClient.run()` receives
a string like `"querysource.remote.query_handler"`, it wraps it in `partial(str, *args)`,
which the worker deserializes and tries to call — failing because a string is not callable.

**Who is affected**: Operators deploying QuerySource with `remote: true` in MultiQS
configurations. Without this feature, remote query execution is dead on arrival.

**Why now**: FEAT-101 was completed on querysource and is ready for integration testing.
The interface contract (`sdd/contracts/qworker-query-handler.md` in querysource) is
approved and waiting for the qworker-side implementation.

### Goals

- Implement a general-purpose **named handler registry** on the qworker server, enabling
  string-based handler dispatch via `QClient.run("handler.name", *args, **kwargs)`.
- Implement the **querysource query handler** that executes QuerySource queries (slug-based
  and raw SQL) on the qworker server, returning DataFrames via the existing cloudpickle
  protocol. Must match the interface contract in querysource's
  `sdd/contracts/qworker-query-handler.md`.
- Keep querysource as an **optional dependency** — qworker must start normally without it.
- Maintain **full backward compatibility** — existing `QClient.run(function_object, ...)`
  must continue to work unchanged.

### Non-Goals (explicitly out of scope)

- **v2 streaming**: Chunked-row streaming via Redis is a future extension documented in
  the querysource contract. Not implemented here.
- **Automatic fallback**: No local fallback if a named handler is not found or fails.
  Errors propagate to the caller.
- **Handler deregistration**: Runtime removal of registered handlers. Not needed for v1.
- **Handler authentication/authorization**: Per-handler access control. The existing
  qworker signature validation applies uniformly to all requests.

---

## 2. Architectural Design

### Overview

This feature adds two layers to qworker:

1. **Named Handler Infrastructure**: A `NamedHandlerWrapper` (wire format) +
   `HandlerRegistry` (server-side resolution) + client-side string detection. This is
   general-purpose — any package can register handlers.

2. **QuerySource Handler**: A concrete handler registered as
   `"querysource.remote.query_handler"` that uses QuerySource's `QueryObject` to execute
   queries locally on the worker. Ships as an optional module that auto-registers via
   Python entry_points.

**Client flow**: `QClient.run("name", *args, **kwargs)` detects `fn` is a string →
creates `NamedHandlerWrapper(name, args, kwargs)` → serializes via cloudpickle → sends
over TCP.

**Server flow**: `connection_handler()` deserializes → detects `NamedHandlerWrapper` →
resolves handler name via `HandlerRegistry` → executes handler → returns result.

**Registry resolution order** (lazy, on first call):
1. Explicit registry (programmatic `register()` calls) → return if found.
2. Entry_points scan (`importlib.metadata.entry_points(group="qworker.handlers")`) →
   import, cache, return if found.
3. Raise `QWException("Handler not found: <name>")`.

### Component Diagram

```
  QClient.run("querysource.remote.query_handler", slug, conditions={...})
       │
       ▼
  get_wrapped_function()
  ┌─────────────────────────────────────────┐
  │ fn is str?                              │
  │   YES → NamedHandlerWrapper(name, args) │
  │   NO  → existing FuncWrapper/partial    │
  └─────────┬───────────────────────────────┘
            │ cloudpickle.dumps()
            ▼
     ── TCP wire ──
            │ cloudpickle.loads()
            ▼
  connection_handler()
  ┌──────────────────────────────────────────────────┐
  │ isinstance(task, NamedHandlerWrapper)?            │
  │   YES → handle_named_handler()                   │
  │         ┌──────────────────────────────────────┐ │
  │         │ HandlerRegistry.resolve(name)        │ │
  │         │   1. explicit map                    │ │
  │         │   2. entry_points("qworker.handlers")│ │
  │         │   3. error                           │ │
  │         └──────────┬───────────────────────────┘ │
  │                    │ handler(slug, conditions)    │
  │                    ▼                              │
  │              QueryObject                          │
  │              .build_provider()                    │
  │              .query()                             │
  │                    │                              │
  │                    ▼                              │
  │              pd.DataFrame                         │
  │   NO  → existing QueueWrapper/callable dispatch  │
  └──────────┬───────────────────────────────────────┘
             │ cloudpickle.dumps(result)
             ▼
       ── TCP wire ──
             │
             ▼
       QClient receives DataFrame
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QueueWrapper` (`wrappers/base.py`) | extends | `NamedHandlerWrapper` inherits from it |
| `QClient.get_wrapped_function()` (`client.py:299`) | modifies | Add string detection branch |
| `QWorker.connection_handler()` (`server.py:810`) | modifies | Add `NamedHandlerWrapper` branch before `QueueWrapper` check |
| `QWorker.handle_queue_wrapper()` (`server.py:735`) | unchanged | Not involved in named handler path |
| `TaskExecutor` (`executor/__init__.py`) | unchanged | Named handlers bypass TaskExecutor |
| `qw/wrappers/__init__.py` | extends | Export `NamedHandlerWrapper` |
| `qw/conf.py` | extends | Handler registry config setting |
| `pyproject.toml` | extends | `querysource` optional dependency group + entry_points |

### Data Models

```python
# No new Pydantic models. NamedHandlerWrapper is a QueueWrapper subclass
# that carries the handler name + arguments over the wire.
```

### New Public Interfaces

```python
from qw.wrappers.base import QueueWrapper


class NamedHandlerWrapper(QueueWrapper):
    """Wire-format wrapper for string-based handler dispatch.

    Carries a handler name + positional/keyword arguments from client to server.
    The server resolves the name via HandlerRegistry and executes the handler.
    """

    def __init__(self, handler_name: str, *args, **kwargs):
        ...

    @property
    def handler_name(self) -> str:
        ...

    async def __call__(self):
        ...


class HandlerRegistry:
    """Server-side registry mapping string names to callable handlers.

    Resolution order: explicit register() → entry_points → error.
    Resolved handlers are cached after first lookup.
    Thread-safe: each worker process has its own registry instance.
    """

    def register(self, name: str, handler: callable) -> None:
        """Register a handler by name. Overwrites existing entry."""
        ...

    def resolve(self, name: str) -> callable:
        """Resolve a handler name to a callable. Raises QWException if not found."""
        ...

    def list_handlers(self) -> dict[str, str]:
        """Return {name: repr(handler)} for all registered + discovered handlers."""
        ...


# Module-level singleton:
handler_registry = HandlerRegistry()
```

---

## 3. Module Breakdown

### Module 1: NamedHandlerWrapper

- **Path**: `qw/wrappers/named.py`
- **Responsibility**: Define the `NamedHandlerWrapper` class that extends `QueueWrapper`
  to carry a handler name string + positional args + keyword args over the wire.
  The `__call__` method resolves the name via `HandlerRegistry` and invokes the handler.
- **Depends on**: `QueueWrapper` (existing), `HandlerRegistry` (Module 2)
- **Details**:
  - `handler_name: str` stored as instance attribute
  - `args` and `kwargs` stored for handler invocation
  - `queued` defaults to `False` (named handlers are always immediate execution)
  - `__call__()` calls `handler_registry.resolve(self.handler_name)` then invokes
    the resolved handler with `self.args` and `self.kwargs`
  - `__repr__` and `__str__` include the handler name for logging

### Module 2: HandlerRegistry

- **Path**: `qw/registry.py`
- **Responsibility**: Implement the `HandlerRegistry` class and module-level
  `handler_registry` singleton. Provides `register()`, `resolve()`, and
  `list_handlers()`. Lazy resolution via `importlib.metadata.entry_points()`.
- **Depends on**: None (leaf module, uses only stdlib)
- **Details**:
  - `_handlers: dict[str, callable]` — explicit registrations
  - `_cache: dict[str, callable]` — resolved entry_points cache
  - `_entry_points_scanned: bool` — tracks whether entry_points have been loaded
  - Entry_points group: `"qworker.handlers"` (configurable via `HANDLER_ENTRY_POINTS_GROUP`
    in `qw/conf.py`)
  - `resolve()` checks `_handlers` first, then scans entry_points (once, cached),
    then raises `QWException`
  - Thread-safe: each worker process has its own Python interpreter; no cross-process
    sharing. No locking needed within a single async worker.

### Module 3: QClient String Dispatch

- **Path**: `qw/client.py` (modify existing)
- **Responsibility**: Modify `get_wrapped_function()` to detect when `fn` is a `str`.
  When it is, create a `NamedHandlerWrapper` instead of a `partial(str, ...)`.
- **Depends on**: Module 1 (NamedHandlerWrapper)
- **Details**:
  - Add new branch at the top of `get_wrapped_function()`:
    ```python
    if isinstance(fn, str):
        return NamedHandlerWrapper(fn, *args, **kwargs)
    ```
  - This must come BEFORE the existing `isinstance(fn, (TaskWrapper, FuncWrapper))` check
  - No changes to `run()`, `queue()`, `sendto_worker()`, or `get_result()`
  - Backward compatible: only strings trigger the new path

### Module 4: Server Handler Resolution

- **Path**: `qw/server.py` (modify existing)
- **Responsibility**: Add a new branch in `connection_handler()` that detects
  `NamedHandlerWrapper` instances and handles them via direct handler resolution
  and execution, bypassing the existing `handle_queue_wrapper` / `TaskExecutor` path.
- **Depends on**: Module 1 (NamedHandlerWrapper), Module 2 (HandlerRegistry)
- **Details**:
  - In `connection_handler()` (line 853), add an `isinstance(task, NamedHandlerWrapper)`
    check **BEFORE** the `isinstance(task, QueueWrapper)` check. This is critical because
    `NamedHandlerWrapper` extends `QueueWrapper` and would otherwise be caught by the
    parent class check.
  - New method `handle_named_handler(task, uid, writer)`:
    1. Resolve handler via `handler_registry.resolve(task.handler_name)`
    2. Execute: `result = await handler(*task.args, **task.kwargs)` (if async) or
       run in executor (if sync)
    3. Track state via `self._state.task_executing()` / `task_completed()`
    4. Return result via `self.return_result(writer, result, task, uid)`
    5. On error: serialize exception and return to client (existing error path)
  - Import `NamedHandlerWrapper` in server.py

### Module 5: QuerySource Handler

- **Path**: `qw/handlers/__init__.py` + `qw/handlers/querysource.py`
- **Responsibility**: Implement the `query_handler` function that matches the interface
  contract in querysource's `sdd/contracts/qworker-query-handler.md`. Conditionally
  imports `QueryObject` from querysource. Registers via entry_points.
- **Depends on**: Module 2 (HandlerRegistry), querysource (optional, external)
- **Details**:
  - `qw/handlers/__init__.py` — empty or with utility imports
  - `qw/handlers/querysource.py` — contains `query_handler()`:
    1. Import `QueryObject` from `querysource.queries.obj` (lazy, at call time)
    2. Build query dict from `slug` + `conditions`
    3. Handle raw SQL: if `slug is None` and `conditions` has `"query"` key
    4. Create `QueryObject(name=name, query=query, queue=queue, request=None, loop=...)`
    5. Call `await query_obj.build_provider()` then `await query_obj.query()`
    6. Drain queue, return DataFrame
  - Error propagation: `SlugNotFound`, `QueryException`, `DriverError`, `DataNotFound`
    propagate as-is (the cloudpickle protocol handles this)
  - Entry_point declaration in `pyproject.toml`:
    ```toml
    [project.entry-points."qworker.handlers"]
    "querysource.remote.query_handler" = "qw.handlers.querysource:query_handler"
    ```

### Module 6: Package Configuration

- **Path**: `pyproject.toml` + `qw/conf.py` (modify existing)
- **Responsibility**: Add `querysource` optional dependency group, entry_points
  declaration, handler registry configuration, and wrappers export.
- **Depends on**: Module 5 (entry_points reference the handler)
- **Details**:
  - `pyproject.toml`:
    - Add `querysource = ["querysource"]` to `[project.optional-dependencies]`
    - Add `[project.entry-points."qworker.handlers"]` section
  - `qw/conf.py`:
    - Add `HANDLER_ENTRY_POINTS_GROUP` setting (default: `"qworker.handlers"`)
  - `qw/wrappers/__init__.py`:
    - Add `NamedHandlerWrapper` to imports and `__all__`

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_named_handler_wrapper_init` | Module 1 | NamedHandlerWrapper stores handler_name, args, kwargs correctly |
| `test_named_handler_wrapper_extends_queue_wrapper` | Module 1 | Verify it's a QueueWrapper subclass (for serialization compatibility) |
| `test_named_handler_wrapper_queued_false` | Module 1 | Default queued=False — named handlers are always immediate |
| `test_named_handler_wrapper_repr` | Module 1 | __repr__ includes handler name for logging |
| `test_named_handler_wrapper_serializable` | Module 1 | cloudpickle.dumps/loads roundtrip preserves name+args |
| `test_registry_register_and_resolve` | Module 2 | register() stores handler; resolve() returns it |
| `test_registry_resolve_unknown_raises` | Module 2 | resolve() for unregistered name raises QWException |
| `test_registry_entry_points_discovery` | Module 2 | Mock entry_points; resolve() finds and caches handler |
| `test_registry_explicit_overrides_entry_points` | Module 2 | Explicit register() wins over entry_points with same name |
| `test_registry_list_handlers` | Module 2 | list_handlers() returns all registered + discovered |
| `test_registry_lazy_scan` | Module 2 | Entry_points not scanned until first unresolved name |
| `test_client_string_creates_named_wrapper` | Module 3 | get_wrapped_function("name", ...) returns NamedHandlerWrapper |
| `test_client_function_unchanged` | Module 3 | get_wrapped_function(fn_obj, ...) still returns FuncWrapper/partial |
| `test_client_task_wrapper_unchanged` | Module 3 | get_wrapped_function(TaskWrapper, ...) still passes through |
| `test_server_dispatches_named_handler` | Module 4 | NamedHandlerWrapper triggers handle_named_handler, not handle_queue_wrapper |
| `test_server_named_handler_returns_result` | Module 4 | Resolved handler result returned to client |
| `test_server_named_handler_error_propagation` | Module 4 | Handler exception serialized and returned |
| `test_server_named_handler_unknown` | Module 4 | Unresolved name returns QWException to client |
| `test_query_handler_slug_execution` | Module 5 | query_handler("slug_name", conditions) returns DataFrame |
| `test_query_handler_raw_sql` | Module 5 | query_handler(None, {"query": "SELECT...", "driver": "pg"}) works |
| `test_query_handler_slug_not_found` | Module 5 | Non-existent slug raises SlugNotFound |
| `test_query_handler_no_querysource` | Module 5 | Handler raises ImportError with helpful message if querysource missing |

### Integration Tests

| Test | Description |
|---|---|
| `test_client_server_named_handler_roundtrip` | QClient.run("test.handler", ...) → server resolves → handler returns result → client receives |
| `test_named_handler_alongside_function_dispatch` | Mix of string-based and function-based QClient.run() calls in same session |
| `test_querysource_handler_end_to_end` | Full roundtrip with mocked QueryObject: QClient → server → query_handler → DataFrame result |
| `test_backward_compat_no_named_handlers` | Existing function-based dispatch works identically without any handlers registered |

### Test Data / Fixtures

```python
import asyncio
import pandas as pd
import pytest


@pytest.fixture
def sample_handler():
    async def handler(slug: str = None, conditions: dict = None, **options):
        return pd.DataFrame({"id": [1, 2], "value": [10, 20]})
    return handler


@pytest.fixture
def registry(sample_handler):
    from qw.registry import HandlerRegistry
    reg = HandlerRegistry()
    reg.register("test.handler", sample_handler)
    return reg


@pytest.fixture
def named_wrapper():
    from qw.wrappers.named import NamedHandlerWrapper
    return NamedHandlerWrapper(
        "test.handler",
        "my-slug",
        conditions={"store_id": 42},
    )


@pytest.fixture
def mock_query_object(mocker):
    qo = mocker.AsyncMock()
    qo.build_provider = mocker.AsyncMock()
    qo.query = mocker.AsyncMock()
    return qo
```

---

## 5. Acceptance Criteria

- [ ] All unit tests pass (`pytest tests/ -k "named_handler or registry or query_handler" -v`)
- [ ] All integration tests pass
- [ ] Existing qworker tests pass unchanged (backward compatibility)
- [ ] `QClient.run("querysource.remote.query_handler", slug, conditions={...})` dispatches
  to the server, resolves the handler, executes the query, and returns a DataFrame
- [ ] `QClient.run(function_object, ...)` continues to work identically (no regression)
- [ ] `HandlerRegistry.register("name", fn)` makes handler available by name
- [ ] Entry_points declared in `pyproject.toml` are discovered lazily by the registry
- [ ] qworker starts and operates normally without querysource installed
- [ ] When querysource is not installed and the handler is called, a clear error message
  is returned: `"Handler 'querysource.remote.query_handler' not found. Is querysource installed?"`
- [ ] Handler exceptions (`SlugNotFound`, `QueryException`, `DriverError`, `DataNotFound`)
  propagate as-is to the client via cloudpickle
- [ ] Unknown handler names return `QWException` with the unresolved name
- [ ] The querysource handler supports both slug-based and raw SQL queries per the
  interface contract
- [ ] `NamedHandlerWrapper` serializes/deserializes correctly via cloudpickle
- [ ] No breaking changes to existing public API
- [ ] `ruff check` and `mypy` pass on all modified files

---

## 6. Codebase Contract

### Verified Imports & Signatures

```python
# qw/wrappers/base.py — QueueWrapper (base for NamedHandlerWrapper)
class QueueWrapper:                                              # line 14
    _queued: bool = True                                         # line 15
    _debug: bool = False                                         # line 16
    def __init__(self, coro=None, *args, **kwargs):              # line 18
        self._queued: bool = kwargs.pop('queued', True)          # line 19
        self._debug: bool = kwargs.pop('debug', False)           # line 20
        self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())    # line 21
        self._container_config = kwargs.pop('container_config', None)  # line 25
        self.args = args                                         # line 28
        self.kwargs = kwargs                                     # line 29
        self.loop = None                                         # line 30
        self.retries = 0                                         # line 32
        self.coro = coro                                         # line 34
    async def __call__(self):                                    # line 40
        return await self.coro(*self.args, **self.kwargs)        # line 41
    @property
    def queued(self):                                            # line 48
    @property
    def id(self):                                                # line 64
    def set_loop(self, event_loop):                              # line 72

# qw/wrappers/func.py — FuncWrapper (pattern to follow for NamedHandlerWrapper)
class FuncWrapper(QueueWrapper):                                 # line 7
    def __init__(self, host, func, *args, **kwargs):             # line 9
        super().__init__(*args, **kwargs)                        # line 10
        self.host = host                                         # line 11
        self.func, self.args, self.kwargs = func, args, kwargs   # line 13
    async def __call__(self):                                    # line 15
        # Handles both async and sync functions                  # line 16-27

# qw/wrappers/__init__.py — conditional import pattern
from .func import FuncWrapper                                    # line 9
from .base import QueueWrapper                                   # line 10
try:                                                             # line 11
    from .di_task import TaskWrapper                              # line 12
except Exception as e:                                           # line 13
    TaskWrapper = None                                           # line 16
__all__ = ('QueueWrapper', 'FuncWrapper', 'TaskWrapper')         # line 20

# qw/wrappers/di_task.py — TaskWrapper (precedent for optional-dep wrapper)
class TaskWrapper(QueueWrapper):                                 # line 23
    def __init__(self, program, task, *args, task_id=None, **kwargs):  # line 25
    async def create(self):                                      # line 47
    async def __call__(self, *args, **kwargs):                   # line 82
    async def run(self):                                         # line 121
    async def close(self):                                       # line 163

# qw/server.py — QWorker server
class QWorker:                                                   # line 50
    def __init__(self, host, port, worker_id, ...):              # line 61
    async def start(self):                                       # line 375
    async def deserialize_task(self, serialized_task, writer):   # line 682
        task = cloudpickle.loads(serialized_task)                # line 684
    async def connection_handler(self, reader, writer):          # line 810
        # Type dispatch:
        if isinstance(task, QueueWrapper):                       # line 853
            return await self.handle_queue_wrapper(...)           # line 854
        elif callable(task):                                     # line 855
            executor = TaskExecutor(task)                         # line 856
            result = await executor.run()                        # line 857
        # NOTE: NamedHandlerWrapper check must go BEFORE line 853
    async def handle_queue_wrapper(self, task, uid, writer):     # line 735
        if task.queued is True:                                  # line 745
            # put in queue                                       # line 760
        else:                                                    # line 785
            executor = TaskExecutor(task)                         # line 792
            result = await executor.run()                        # line 793
    async def return_result(self, writer, result, task, uid):    # line 698

# qw/client.py — QClient
class QClient:                                                   # line 58
    timeout: int = 5                                             # line 69
    def __init__(self, worker_list=None, timeout=5):             # line 72
    def get_wrapped_function(self, fn, host, *args,
        use_wrapper=False, queued=False, **kwargs):              # line 299
        if isinstance(fn, (TaskWrapper, FuncWrapper)):           # line 308
            func = fn                                            # line 310
        elif use_wrapper is True:                                # line 312
            func = FuncWrapper(host, fn, *args, **kwargs)        # line 314
        else:                                                    # line 321
            func = partial(fn, *args, **kwargs)                  # line 323
        # NOTE: string detection must go BEFORE line 308
    async def run(self, fn: Any, *args, **kwargs):               # line 326
    async def sendto_worker(self, func, writer):                 # line 260
        # cloudpickle.dumps(func)
    async def get_result(self, reader, writer):                  # line 280

# qw/executor/__init__.py — TaskExecutor
class TaskExecutor:                                              # line 16
    def __init__(self, task, *args, **kwargs):                   # line 17
    async def run(self):                                         # line 84
        if type(self.task) in (FuncWrapper, QueueWrapper):       # line 91
            result = await self.task()                           # line 97
        elif isinstance(self.task, TaskWrapper):                 # line 98
            result = await self.run_task()                       # line 104
        # NOTE: NamedHandlerWrapper does NOT go through TaskExecutor

# qw/conf.py — configuration pattern
WORKER_DEFAULT_HOST = config.get('WORKER_DEFAULT_HOST', ...)     # line 15
WORKER_DEFAULT_PORT = config.getint('WORKER_DEFAULT_PORT', ...)  # line 16
PACKAGE_LIST = config.getlist('PACKAGE_LIST', ...)               # line 109

# qw/exceptions.py
from qw.exceptions import QWException, ParserError, DiscardedTask  # server.py:17

# qw/__init__.py
from .version import __author__, __description__, __title__, __version__  # line 6
```

### User-Provided Code (from querysource contract)

```python
# Handler signature (from querysource sdd/contracts/qworker-query-handler.md):
async def query_handler(slug: str = None, conditions: dict = None, **options) -> pd.DataFrame:
    queue = asyncio.Queue()

    if slug is None and conditions and "query" in conditions:
        query = dict(conditions)
        name = "raw"
    else:
        query = {"slug": slug}
        if conditions:
            query.update(conditions)
        name = slug

    query_obj = QueryObject(
        name=name,
        query=query,
        queue=queue,
        request=None,
        loop=asyncio.get_running_loop(),
    )

    await query_obj.build_provider()
    await query_obj.query()

    result_dict = await queue.get()
    return result_dict[name]
```

### QuerySource References (external — verified in querysource repo)

```python
# querysource/queries/obj.py (verified 2026-05-26)
class QueryObject(BaseQuery):                                    # line 20
    def __init__(self, name, query, conditions=None, request=None,
                 queue=None, loop=None):                         # line 26
    async def build_provider(self):                              # line 65
    async def query(self):                                       # line 183
    # queue put: await self._queue.put({self._name: result})     # line 203

# querysource/exceptions.py (verified 2026-05-26)
# SlugNotFound                                                   # line 34
# QueryException                                                 # line 6
# DriverError                                                    # line 58
# DataNotFound                                                   # line 48
```

### Does NOT Exist (Anti-Hallucination)

- ~~`qw/handlers/`~~ — no handlers directory exists; Module 5 creates it
- ~~`qw/handlers/querysource.py`~~ — does not exist yet
- ~~`qw/registry.py`~~ — does not exist yet; Module 2 creates it
- ~~`qw/wrappers/named.py`~~ — does not exist yet; Module 1 creates it
- ~~`NamedHandlerWrapper`~~ — does not exist in any module
- ~~`HandlerRegistry`~~ — does not exist in any module
- ~~`handler_registry`~~ — no module-level instance exists
- ~~`QWorker.handle_named_handler()`~~ — no such method on QWorker
- ~~`QWorker.registry`~~ — no registry attribute on QWorker
- ~~`QClient` string detection~~ — `get_wrapped_function()` has no string branch; strings
  fall through to `partial(str, ...)` which creates an uncallable partial
- ~~`qw.conf.HANDLER_ENTRY_POINTS_GROUP`~~ — no such config setting; Module 6 adds it
- ~~`querysource.remote`~~ — no remote module in querysource (handler lives in qworker)
- ~~`QClient.run_handler()`~~ — no such method; string dispatch goes through existing `run()`
- ~~`qw.exceptions.HandlerNotFoundError`~~ — no such exception; use `QWException`

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Wrapper pattern**: Follow `FuncWrapper` (`wrappers/func.py`) — extend `QueueWrapper`,
  store function reference + args in `__init__`, implement `__call__()` for execution.
- **Conditional import pattern**: Follow `wrappers/__init__.py` lines 11-16 — use
  try/except for optional dependencies, set to `None` if unavailable.
- **Config pattern**: Follow `conf.py` — use `navconfig.config.get()` / `.getint()` with
  `fallback=` parameter for new settings.
- **Error serialization**: Exceptions raised by handlers are serialized via cloudpickle
  and returned to the client. This matches the existing behavior at `server.py:797-808`.
- **State tracking**: Named handler execution should use `self._state.task_executing()`
  and `self._state.task_completed()` matching the pattern at `server.py:788-795`.

### Known Risks / Gotchas

- **isinstance ordering in connection_handler**: `NamedHandlerWrapper` extends
  `QueueWrapper`, so the `isinstance(task, NamedHandlerWrapper)` check MUST come before
  `isinstance(task, QueueWrapper)` at line 853. Getting this wrong silently routes named
  handlers through the wrong execution path.
- **cloudpickle compatibility**: `NamedHandlerWrapper` must be importable on both client
  and server for cloudpickle deserialization to work. Both sides need the same qworker
  version installed.
- **Entry_points caching**: `importlib.metadata.entry_points()` can be slow on systems
  with many packages. The registry must cache results after first scan.
- **Async vs sync handlers**: The querysource handler is async, but future handlers may
  be sync. `handle_named_handler()` should detect and handle both (run sync handlers
  in a thread executor, matching the pattern in `FuncWrapper.__call__` at line 17-27).
- **querysource import weight**: `QueryObject` imports pull in database drivers, config
  systems, etc. The handler module must import querysource lazily (inside the function
  body, not at module level) to maintain qworker's fast startup.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `querysource` | existing (internal) | Optional — QueryObject for query execution |
| `importlib.metadata` | stdlib (3.11+) | Entry_points discovery for handler registry |
| `cloudpickle` | `>=3.0.0` (existing) | NamedHandlerWrapper serialization |

No new mandatory dependencies. `querysource` is optional — added to
`[project.optional-dependencies]` as `querysource = ["querysource"]`.

---

## Worktree Strategy

- **Default isolation**: `per-spec` — all tasks run sequentially in one worktree.
- **Rationale**: Modules 1-4 are tightly coupled through `NamedHandlerWrapper` — the
  wrapper definition (Module 1) must exist before the client (Module 3) and server
  (Module 4) can use it. Module 5 (handler) depends on Module 2 (registry). Module 6
  (config) touches `pyproject.toml` and `wrappers/__init__.py` which overlap with other
  modules.
- **Cross-feature dependencies**: None. No in-flight specs touch `connection_handler()`,
  `get_wrapped_function()`, or the wrappers module. FEAT-005 and FEAT-006 are merged.

---

## 8. Open Questions

- [x] Should `HandlerRegistry` support handler removal/deregistration? — *Owner: Jesus*:
  No, not for v1.
- [ ] Should the health endpoint expose the list of registered handlers? — *Owner: Jesus*
- [ ] Should handler names be validated (must contain a dot, max length) or is any
  non-empty string valid? — *Owner: Jesus*
- [ ] Should `handle_named_handler` enforce that handlers are async, or also support
  sync handlers run in a thread executor? — *Owner: Jesus*

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-26 | Jesus Lara / claude-opus-4-6 | Initial spec from brainstorm |
