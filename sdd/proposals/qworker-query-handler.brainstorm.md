# Brainstorm: QWorker Query Handler

**Date**: 2026-05-26
**Author**: Jesus Lara
**Status**: exploration
**Recommended Option**: Option A

---

## Problem Statement

QuerySource's FEAT-101 (MultiQuery Remote Execution) introduced a `RemoteExecutor` that
offloads query execution to a remote qworker server via `QClient.run()`. The querysource
side is complete — `RemoteExecutor` calls
`QClient.run("querysource.remote.query_handler", slug, conditions=conditions)` — but the
qworker side has no handler to receive and execute these queries.

**Critical gap**: qworker currently dispatches tasks by **serializing Python function objects**
via cloudpickle. There is no string-based handler registry. When `QClient.run()` receives
a string like `"querysource.remote.query_handler"`, it wraps it in `partial(str, *args)`,
which the worker deserializes and tries to call — failing because a string is not callable.

**Who is affected**: Operators deploying QuerySource with `remote: true` in MultiQS
configurations. Without this feature, remote query execution is dead on arrival.

**Why now**: FEAT-101 was completed on querysource and is ready for integration testing.
The interface contract (`sdd/contracts/qworker-query-handler.md` in querysource) is
approved and waiting for the qworker-side implementation.

## Constraints & Requirements

- **Interface contract**: Must match the contract in querysource's
  `sdd/contracts/qworker-query-handler.md` — handler signature, error propagation,
  slug + raw query support.
- **querysource is optional**: qworker must start and operate normally without
  querysource installed. The handler is only available when querysource is importable.
- **Lazy loading**: Handler resolution must be lazy (on first call, not at server startup)
  to avoid importing heavy optional dependencies when they're not needed.
- **General-purpose registry**: The registration mechanism must be reusable — querysource
  is the first consumer, but future handlers (e.g., flowtask tasks by name, custom ETL
  handlers) should use the same pattern.
- **Backward compatibility**: Existing `QClient.run(function_object, ...)` must continue
  to work unchanged. String-based dispatch is additive.
- **No v2 streaming**: The v2 chunked-row streaming extension (Redis-based) is explicitly
  out of scope per the querysource contract.

---

## Options Explored

### Option A: NamedHandlerWrapper + HandlerRegistry

Add a server-side `HandlerRegistry` that maps string names to callables, and a new
`NamedHandlerWrapper` that carries handler name + args over the wire. The client detects
string arguments and wraps them appropriately. The server resolves the name on arrival.

Handlers register in two ways:
1. **Explicit**: `HandlerRegistry.register("name", fn)` — for programmatic registration.
2. **Auto-discovery**: Python `entry_points` group `"qworker.handlers"` — scanned lazily
   on first unresolved name.

The querysource handler ships as an optional module (`qw.handlers.querysource`) that
conditionally imports `QueryObject` and implements the contract. It registers itself
via entry_points if querysource is installed, or can be registered explicitly at startup.

**Flow**:
1. `QClient.run("querysource.remote.query_handler", slug, conditions={...})`
2. Client detects `fn` is a string → creates `NamedHandlerWrapper(name, args, kwargs)`
3. Worker deserializes → sees `NamedHandlerWrapper` → looks up name in `HandlerRegistry`
4. Registry checks: explicit map → entry_points → dotted-path import (fallback)
5. Resolved handler is called with args/kwargs → result returned via cloudpickle

✅ **Pros:**
- Clean separation: client sends a name, server resolves and executes
- Supports explicit registration, entry_points, and import-path fallback
- Lazy loading — heavy dependencies only imported on first call
- Follows existing wrapper pattern (`FuncWrapper`, `TaskWrapper`)
- General-purpose — any package can register handlers
- Backward compatible — existing function-based dispatch untouched

❌ **Cons:**
- More moving parts: new wrapper class, registry class, client-side detection
- Entry_points discovery requires `importlib.metadata` (stdlib, but adds complexity)
- Three resolution strategies (explicit, entry_points, import path) need clear priority

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `importlib.metadata` | Entry-points discovery | stdlib, Python 3.11+ |
| `cloudpickle` | Serialize NamedHandlerWrapper | already a dependency |
| `querysource` | QueryObject for handler impl | optional dependency |

🔗 **Existing Code to Reuse:**
- `qw/wrappers/base.py` — `QueueWrapper` base class for `NamedHandlerWrapper`
- `qw/wrappers/func.py` — `FuncWrapper` pattern (wrapper that holds function + args)
- `qw/wrappers/__init__.py` — conditional import pattern (try/except for optional deps)
- `qw/executor/__init__.py` — `TaskExecutor.run()` dispatch logic (add new branch)
- `qw/server.py:853-858` — `connection_handler()` type-based routing (add new branch)
- `qw/client.py:299-324` — `get_wrapped_function()` (add string detection)
- `qw/conf.py` — configuration pattern for new settings

---

### Option B: Entry-Points Only (No Explicit Registry)

Rely entirely on Python packaging entry_points for handler discovery. Packages that want
to register handlers declare them in their `pyproject.toml`:

```toml
[project.entry-points."qworker.handlers"]
"querysource.remote.query_handler" = "querysource.remote:query_handler"
```

When the server receives a string handler name, it scans `entry_points(group="qworker.handlers")`
to find a matching entry, loads it, and caches the result.

No new wrapper class — the string is sent as-is and resolved server-side after
deserialization. The server must detect when a deserialized task is a string (not callable).

✅ **Pros:**
- Minimal code in qworker — no registry class, no wrapper class
- Standard Python packaging mechanism — well understood
- Handler registration is fully decoupled (querysource declares its own entry_point)
- Lazy by nature — entry_points loaded on first use

❌ **Cons:**
- Requires package reinstall/rebuild to add or modify handlers (no runtime registration)
- Entry_points scan can be slow on systems with many installed packages
- Sending a raw string over cloudpickle is fragile — no type safety on the wire
- No explicit `register()` API for programmatic use (testing, dynamic handlers)
- Cannot register handlers from packages that don't use entry_points

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `importlib.metadata` | Entry-points discovery | stdlib |
| `querysource` | Handler implementation | optional, declares its own entry_point |

🔗 **Existing Code to Reuse:**
- `qw/server.py:853-858` — `connection_handler()` type routing (add string branch)
- `qw/client.py:299-324` — `get_wrapped_function()` (add string passthrough)

---

### Option C: Dotted-Path Import Resolution (Convention Over Configuration)

When `QClient.run("querysource.remote.query_handler", ...)` is called, the server
resolves the string by splitting it into a module path and attribute name, imports the
module, and calls the function. No registration, no entry_points — just Python import
resolution.

The string `"querysource.remote.query_handler"` becomes:
`importlib.import_module("querysource.remote").query_handler`

Any installed Python module's functions can be invoked by their fully qualified dotted path.

✅ **Pros:**
- Simplest implementation — just `importlib.import_module()` + `getattr()`
- Zero configuration — if the module is installed, it works
- No new classes or infrastructure needed

❌ **Cons:**
- **Security risk**: Any callable in any installed package can be invoked remotely
- No allowlist or validation — attacker who controls the client can execute arbitrary code
- Import errors surface at call time with poor diagnostics
- No way to restrict which handlers are available
- Module path must exactly match the Python import structure

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `importlib` | Module import resolution | stdlib |

🔗 **Existing Code to Reuse:**
- `qw/server.py:853-858` — `connection_handler()` type routing

---

## Recommendation

**Option A** is recommended because:

1. **Security**: Unlike Option C, it restricts handler resolution to explicitly registered
   names and declared entry_points. No arbitrary module import.
2. **Flexibility**: Unlike Option B, it supports both packaging-based discovery (entry_points)
   and programmatic registration (`register()`). This matters for testing, dynamic
   handlers, and packages that don't use entry_points.
3. **Pattern consistency**: The `NamedHandlerWrapper` follows the established `FuncWrapper`
   and `TaskWrapper` pattern — a new wrapper type that the server dispatch logic can
   handle cleanly.
4. **Lazy loading**: Handlers are only imported when first called, satisfying the
   requirement that querysource remains optional and doesn't slow down server startup.

The tradeoff is slightly more code than Option B, but the extra infrastructure (registry +
wrapper) pays for itself immediately: programmatic registration enables clean testing,
the wrapper provides type safety on the wire, and the three-tier resolution
(explicit → entry_points → error) gives operators clear diagnostics.

---

## Feature Description

### User-Facing Behavior

**For QuerySource operators**: When a MultiQS config has `remote: true`, the query is
dispatched to a qworker server. If the qworker has querysource installed, the query
executes locally on the worker and the DataFrame result is returned. No configuration
on the qworker side is needed beyond installing querysource — the handler auto-registers
via entry_points.

**For qworker operators**: A new optional dependency `qworker[querysource]` installs
querysource and makes the query handler available. The handler appears in the server's
registered handlers list (visible via health endpoint or logs at startup).

**For developers**: The `HandlerRegistry` provides a `register()` API for adding custom
named handlers. Any package can declare handlers via entry_points or register them
programmatically at import time.

### Internal Behavior

**Client side** (`QClient.run()`):
1. `get_wrapped_function()` detects `fn` is a `str`.
2. Creates `NamedHandlerWrapper(name=fn, args=args, kwargs=kwargs)`.
3. Serializes via cloudpickle and sends over TCP (normal flow).

**Server side** (`connection_handler()`):
1. Deserializes task — gets a `NamedHandlerWrapper` instance.
2. Detects it's a `NamedHandlerWrapper` (new branch in type dispatch).
3. Calls `HandlerRegistry.resolve(wrapper.handler_name)`:
   a. Check explicit registry map → return if found.
   b. Scan `entry_points(group="qworker.handlers")` → import, cache, return.
   c. Raise `QWException("Handler not found: <name>")`.
4. Calls resolved handler with `wrapper.args` and `wrapper.kwargs`.
5. Returns result via cloudpickle (normal flow).

**Handler registry** (`HandlerRegistry`):
- Singleton (module-level instance).
- Thread-safe (handlers resolved in per-process workers, no cross-process sharing).
- Caches resolved handlers after first lookup.
- Provides `register(name, fn)`, `resolve(name) → callable`, `list() → dict`.

**QuerySource handler** (`qw.handlers.querysource`):
1. Imports `QueryObject` from querysource (lazy, only when called).
2. Builds query dict from slug + conditions.
3. Creates `QueryObject`, calls `build_provider()` + `query()`.
4. Returns the DataFrame result.
5. Handles raw SQL queries (slug=None, conditions contains "query" key).

### Edge Cases & Error Handling

- **querysource not installed**: Handler never registers. `HandlerRegistry.resolve()` returns
  a clear error: `"Handler 'querysource.remote.query_handler' not found. Is querysource installed?"`.
- **Handler raises exception**: Exception is cloudpickle-serialized and returned to the
  client as-is (existing error propagation path). `SlugNotFound`, `QueryException`,
  `DriverError` propagate transparently per the contract.
- **Unknown handler name**: `QWException` with the unresolved name, serialized and returned
  to the client.
- **Entry_points scan failure**: Logged as warning; explicit registry still works.
- **Handler import failure**: Logged with full traceback; returns `QWException` to client.
- **Concurrent first calls**: Registry uses a simple dict + lazy import. Two concurrent
  calls to the same unresolved handler may both import, but the second just overwrites
  with the same value (idempotent).
- **String fn backward compatibility**: Only strings trigger `NamedHandlerWrapper`. Existing
  function/TaskWrapper/FuncWrapper dispatch is untouched.

---

## Code Context

### Verified Signatures & Locations

```python
# qw/wrappers/base.py — QueueWrapper (base for new NamedHandlerWrapper)
class QueueWrapper:                                              # line 14
    def __init__(self, coro=None, *args, **kwargs):              # line 18
    async def __call__(self):                                    # line 40
    @property
    def id(self):                                                # line 64
    def set_loop(self, event_loop):                              # line 72

# qw/wrappers/func.py — FuncWrapper (pattern to follow)
class FuncWrapper(QueueWrapper):                                 # line 7
    def __init__(self, host, func, *args, **kwargs):             # line 9
    async def __call__(self):                                    # line 15

# qw/wrappers/__init__.py — conditional import pattern
from .func import FuncWrapper                                    # line 9
from .base import QueueWrapper                                   # line 10
try:                                                             # line 11
    from .di_task import TaskWrapper                              # line 12
except Exception as e:                                           # line 13
    TaskWrapper = None                                           # line 16

# qw/server.py — QWorker server and task dispatch
class QWorker:                                                   # line 50
    async def start(self):                                       # line 375
    async def deserialize_task(self, serialized_task, writer):   # line 682
    async def connection_handler(self, reader, writer):          # line 810
    # Type dispatch in connection_handler:
    #   isinstance(task, QueueWrapper) → handle_queue_wrapper()  # line 853
    #   callable(task) → TaskExecutor(task).run()                # line 856
    #   else → put in queue                                      # line 860+

# qw/client.py — QClient
class QClient:                                                   # line 58
    def get_wrapped_function(self, fn, host, *args, **kwargs):   # line 299
    async def run(self, fn: Any, *args, **kwargs):               # line 326
    async def sendto_worker(self, func, writer):                 # line 260
    async def get_result(self, reader, writer):                  # line 280

# qw/executor/__init__.py — TaskExecutor dispatch
class TaskExecutor:                                              # line 16
    async def run(self):                                         # line 84
    # Dispatch in run():
    #   FuncWrapper/QueueWrapper → await self.task()             # line 91-97
    #   TaskWrapper → await self.run_task()                      # line 98-104
    #   awaitable → await self.task()                            # line 105-111
    #   else → run_in_executor (blocking)                        # line 112-118

# qw/conf.py — configuration pattern
WORKER_DEFAULT_HOST = config.get('WORKER_DEFAULT_HOST', ...)     # line 15
WORKER_DEFAULT_PORT = config.getint('WORKER_DEFAULT_PORT', ...)  # line 16
PACKAGE_LIST = config.getlist('PACKAGE_LIST', ...)               # line 109

# qw/wrappers/di_task.py — TaskWrapper (precedent for optional-dep wrapper)
class TaskWrapper(QueueWrapper):                                 # line 23
    def __init__(self, program, task, *args, **kwargs):          # line 25
    async def create(self):                                      # line 47
    async def run(self):                                         # line 121
    async def close(self):                                       # line 163
```

### User-Provided Code (from querysource contract)

```python
# Handler signature (from sdd/contracts/qworker-query-handler.md):
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

### Does NOT Exist (Anti-Hallucination)

- ~~`qw.handlers`~~ — no handlers module exists in qworker
- ~~`qw.handlers.querysource`~~ — does not exist yet; this feature creates it
- ~~`qw.registry`~~ — no registry module exists
- ~~`qw.wrappers.named`~~ — no named handler wrapper exists
- ~~`NamedHandlerWrapper`~~ — does not exist yet
- ~~`HandlerRegistry`~~ — does not exist yet
- ~~`QWorker.register_handler()`~~ — no handler registration method on the server
- ~~`QClient.run()` string detection~~ — currently wraps strings in partial(), no special handling
- ~~`querysource.remote`~~ — no remote module exists in querysource (yet)
- ~~`qw.conf.HANDLER_ENTRY_POINTS_GROUP`~~ — no such config setting exists
- ~~`connection_handler` NamedHandlerWrapper branch~~ — does not exist; needs to be added

---

## Capabilities

### New Capabilities
- `named-handler-registry`: General-purpose server-side registry for string-addressable
  handlers with lazy resolution via explicit registration and entry_points auto-discovery.
- `querysource-query-handler`: Handler that executes QuerySource queries (slug-based and
  raw SQL) on the qworker server, returning DataFrames via the existing cloudpickle protocol.
- `named-handler-wrapper`: Wire-format wrapper (`NamedHandlerWrapper`) that carries a
  handler name + arguments from client to server for string-based dispatch.

### Modified Capabilities
- `qw-client-dispatch`: `QClient.run()` and `get_wrapped_function()` extended to detect
  string arguments and create `NamedHandlerWrapper` instead of `partial(str, ...)`.
- `qw-server-routing`: `connection_handler()` extended with a new type branch for
  `NamedHandlerWrapper` resolution and execution.
- `qw-optional-dependencies`: `pyproject.toml` gains a `querysource` extras group.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `qw/wrappers/` | extends | New `NamedHandlerWrapper` class + export in `__init__.py` |
| `qw/client.py` | modifies | `get_wrapped_function()` adds string detection branch |
| `qw/server.py` | modifies | `connection_handler()` adds `NamedHandlerWrapper` branch |
| `qw/executor/__init__.py` | modifies | `TaskExecutor.run()` adds `NamedHandlerWrapper` branch |
| `qw/conf.py` | extends | Optional handler registry config (entry_points group name) |
| `pyproject.toml` | extends | New `querysource` optional dependency group |
| `qw/handlers/` | new | New module for built-in handlers (querysource first) |
| `qw/registry.py` | new | `HandlerRegistry` class |

---

## Parallelism Assessment

- **Internal parallelism**: `mixed` — the registry infrastructure (wrapper + registry +
  client/server modifications) is one stream, while the querysource handler implementation
  is independent once the registry interface is defined. These two streams can be developed
  in parallel after the `NamedHandlerWrapper` interface is agreed upon.
- **Cross-feature independence**: No in-flight specs touch `connection_handler()`,
  `get_wrapped_function()`, or the wrappers module. FEAT-005 (graceful drain) and
  FEAT-006 (Docker/K8s) are complete and merged.
- **Recommended isolation**: `per-spec` — while the handler and registry are logically
  separable, they share the `wrappers/__init__.py` and `server.py` dispatch code. Sequential
  execution in one worktree avoids merge conflicts.
- **Rationale**: The registry is small enough (~3 files) that the overhead of a separate
  worktree outweighs the parallelism benefit. The querysource handler depends on the
  registry being in place to register itself.

---

## Open Questions

- [ ] Should `HandlerRegistry` support handler removal/deregistration? — *Owner: Jesus*
- [ ] Should the health endpoint expose the list of registered handlers (for operational
  visibility)? — *Owner: Jesus*
- [ ] Should handler names be validated (e.g., must contain a dot, max length) or is any
  non-empty string valid? — *Owner: Jesus*
- [ ] The querysource contract shows the handler as `async def query_handler(slug, conditions, **options)`.
  Should qworker enforce that registered handlers are async, or also support sync handlers
  (run in executor)? — *Owner: Jesus*
