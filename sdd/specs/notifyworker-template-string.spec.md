# Feature Specification: NotifyWorker Template String Support

**Feature ID**: FEAT-008
**Date**: 2026-08-06
**Author**: Jesus Lara
**Status**: draft
**Target version**: 1.x.x

---

## 1. Motivation & Business Requirements

### Problem Statement

The `async-notify` library's `TemplateParser` currently treats the `template`
parameter exclusively as a **filename** looked up via Jinja2's `FileSystemLoader`
in a single `TEMPLATE_DIR` directory.  A forthcoming release of `async-notify`
adds a heuristic to `ProviderBase._prepare_` so `template` can be either:

- **A file path** (`Path` or path-like string) — loaded from the filesystem.
- **An inline template string** (raw Jinja2/HTML content) — rendered directly
  from the string without touching the filesystem.

The `type` becomes `Union[Path, str]`, where the library distinguishes between
the two via a heuristic (e.g. checking for path separators, file existence, or
Jinja2 syntax markers).

`qworker` spawns `NotifyWorker` as a child process and feeds it notification
messages over TCP and Redis Pub/Sub.  Today the pipeline assumes `template` is
always a short filename string.  When the new `async-notify` lands, qworker
must:

1. **Propagate** the `template_dir` configuration to `NotifyWorker` so it can
   resolve file-based templates.
2. **Preserve** inline template strings through the JSON
   serialization/deserialization pipeline (TCP messages and Redis streams) so
   the new heuristic works end-to-end.
3. **Expose** a `--template-dir` CLI flag and configuration constant so
   operators can set the templates directory at deploy time.

### Goals

- Allow `template` in notification messages to be either an inline Jinja2
  string or a file path (`Union[Path, str]`), with qworker passing the value
  through transparently to the updated `async-notify`.
- Add a `--template-dir` CLI argument and `TEMPLATE_DIR` config variable so
  the template search directory is configurable from qworker startup.
- Pass `template_dir` to `NotifyWorker.__init__()` when the new `async-notify`
  accepts it (guarded by an optional-parameter check for backward compat).
- Maintain full backward compatibility — existing deployments passing
  `template='email_applied.html'` (filename only) must keep working with no
  changes.

### Non-Goals (explicitly out of scope)

- Modifying `async-notify` itself — that work happens in the notify repo.
- Adding template *editing*, *caching*, or *management* features to qworker.
- Supporting template content via Redis (template data arrives inline in the
  message, not stored separately).

---

## 2. Architectural Design

### Overview

The change is a thin pass-through enhancement: qworker's configuration,
CLI, and `SpawnProcess` layers learn to carry a `template_dir` value and
forward it to `NotifyWorker`.  The JSON message pipeline is already
string-transparent, so inline template strings flow through without extra
serialization logic.  The key integration point is
`SpawnProcess.start_notify_worker`, which gains a `template_dir` kwarg.

### Component Diagram

```
CLI (--template-dir)
        │
        ▼
  qw/conf.py  (TEMPLATE_DIR)
        │
        ▼
  SpawnProcess.__init__
        │
        ├── mp.Process(target=start_notify_worker, args=(..., template_dir))
        │           │
        │           ▼
        │     NotifyWorker(host, port, ..., template_dir=...)
        │           │
        │           ▼
        │     build_notify(data) → NotifyWrapper(**msg)
        │           │                   │
        │           │       kwargs["template"] = "email.html" | "<html>{{x}}</html>"
        │           │                   │
        │           ▼                   ▼
        │     NotifyWrapper.__call__() → Notify(provider).send(template=..., **kwargs)
        │                                         │
        │                                         ▼
        │                           ProviderBase._prepare_(template=Union[Path,str])
        │                                         │
        │                                 ┌───────┴────────┐
        │                                 │   heuristic    │  (async-notify)
        │                                 ├────────────────┤
        │                                 │ path? → file   │
        │                                 │ string? → jinja│
        │                                 └────────────────┘
        │
        └── mp.Process(target=start_server, args=(...))   # QWorker (unchanged)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `qw/conf.py` | extends | Add `TEMPLATE_DIR` constant |
| `qw/__main__.py` | extends | Add `--template-dir` CLI argument |
| `qw/process.py` `SpawnProcess` | modifies | Pass `template_dir` to `NotifyWorker` |
| `notify.server.NotifyWorker` | uses | Forward-compatible: pass `template_dir` only when the new init accepts it |
| `notify.providers.base.ProviderBase._prepare_` | relies on | Unchanged — async-notify heuristic handles `Union[Path, str]` |

### Data Models

No new data models required.  The `template` field in notification message
JSON payloads remains a JSON string — the heuristic in `async-notify`
determines interpretation.

### New Public Interfaces

```python
# qw/conf.py — new constant
TEMPLATE_DIR: str | None  # Read from navconfig / env var TEMPLATE_DIR

# qw/process.py — updated method signature
class SpawnProcess:
    def start_notify_worker(
        self,
        host: str,
        port: str,
        debug: bool,
        name: str,
        notify_empty: bool,
        template_dir: str | None = None,   # ← NEW
    ) -> None: ...
```

---

## 3. Module Breakdown

### Module 1: Configuration — `TEMPLATE_DIR`

- **Path**: `qw/conf.py`
- **Responsibility**: Read `TEMPLATE_DIR` from navconfig / environment variable.
  Defaults to `None` (let async-notify use its own default).
- **Depends on**: `navconfig`

**Changes**:
```python
# In qw/conf.py, add:
TEMPLATE_DIR: str | None = config.get('TEMPLATE_DIR', fallback=None)
```

### Module 2: CLI argument — `--template-dir`

- **Path**: `qw/__main__.py`
- **Responsibility**: Accept `--template-dir` CLI argument in the `start`
  subcommand and store it on `args.template_dir`.
- **Depends on**: Module 1

**Changes** (in `_add_start_args`):
```python
parser.add_argument(
    '--template-dir', dest='template_dir', type=str,
    default=None,
    help='Directory for notification templates (overrides TEMPLATE_DIR env var)'
)
```

### Module 3: SpawnProcess — pass `template_dir` to NotifyWorker

- **Path**: `qw/process.py`
- **Responsibility**: Read `template_dir` from CLI args (falling back to
  `qw/conf.py` value), pass it to `start_notify_worker`, and forward it to
  `NotifyWorker.__init__()` when the new async-notify supports it.
- **Depends on**: Module 1, Module 2

**Changes**:

1. In `SpawnProcess.__init__`, resolve `template_dir`:
   ```python
   self._template_dir: str | None = getattr(args, 'template_dir', None) or TEMPLATE_DIR
   ```

2. In `SpawnProcess.__init__`, pass `template_dir` when spawning the notify process:
   ```python
   notify_process = mp.Process(
       target=self.start_notify_worker,
       name=_name,
       args=(
           args.notify_host,
           args.notify_port,
           args.debug,
           _name,
           args.notify_empty,
           self._template_dir,   # ← NEW
       )
   )
   ```

3. Update `start_notify_worker` signature and forward-compatible instantiation:
   ```python
   def start_notify_worker(
       self,
       host: str,
       port: str,
       debug: bool,
       name: str,
       notify_empty: bool,
       template_dir: str | None = None,
   ):
       loop = asyncio.new_event_loop()
       asyncio.set_event_loop(loop)
       # Build kwargs for NotifyWorker, adding template_dir only if
       # the installed async-notify version accepts it.
       nw_kwargs: dict = dict(
           host=host,
           port=port,
           debug=debug,
           name=name,
           notify_empty_stream=notify_empty,
       )
       if template_dir is not None:
           import inspect
           sig = inspect.signature(NotifyWorker.__init__)
           if 'template_dir' in sig.parameters:
               nw_kwargs['template_dir'] = template_dir
       notify_worker = NotifyWorker(**nw_kwargs)
       ...
   ```

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_conf_template_dir_default` | Module 1 | `TEMPLATE_DIR` is `None` when env var is unset |
| `test_conf_template_dir_from_env` | Module 1 | `TEMPLATE_DIR` reads value from env var |
| `test_cli_template_dir_arg` | Module 2 | `--template-dir /path` populates `args.template_dir` |
| `test_cli_template_dir_default_none` | Module 2 | Omitting `--template-dir` yields `None` |
| `test_spawn_process_template_dir_from_args` | Module 3 | `SpawnProcess` resolves `template_dir` from CLI args |
| `test_spawn_process_template_dir_fallback_conf` | Module 3 | Falls back to `qw/conf.py` when CLI arg is `None` |
| `test_start_notify_worker_passes_template_dir` | Module 3 | `NotifyWorker` receives `template_dir` kwarg when supported |
| `test_start_notify_worker_omits_template_dir_old` | Module 3 | Backward compat: does NOT pass `template_dir` to old `NotifyWorker` |

### Integration Tests

| Test | Description |
|---|---|
| `test_notify_worker_inline_template` | Send a notification with an inline Jinja2 template string through the TCP pipeline; verify the rendered output contains the interpolated values (requires new async-notify) |
| `test_notify_worker_file_template` | Send a notification with a filename template through the TCP pipeline; verify it resolves from `template_dir` (requires new async-notify) |

### Test Data / Fixtures

```python
@pytest.fixture
def notify_args():
    """Minimal argparse.Namespace for SpawnProcess with template_dir."""
    return argparse.Namespace(
        host='127.0.0.1',
        port=18900,
        notify_host='127.0.0.1',
        notify_port=8991,
        workers=1,
        debug=False,
        enable_notify=True,
        notify_empty=False,
        wkname='TestWorker',
        health_port=8080,
        template_dir='/tmp/test-templates',
    )

@pytest.fixture
def inline_template():
    return '<html><body>Hello {{ username.name }}</body></html>'

@pytest.fixture
def file_template():
    return 'email_applied.html'
```

---

## 5. Acceptance Criteria

- [ ] `qw/conf.py` exports `TEMPLATE_DIR` (default `None`, configurable via env).
- [ ] `--template-dir` CLI argument accepted by `qw start`.
- [ ] `SpawnProcess.start_notify_worker` accepts `template_dir: str | None`.
- [ ] `template_dir` is forwarded to `NotifyWorker.__init__()` when the installed
      `async-notify` version supports it (introspection guard).
- [ ] When `template_dir` is not supported by the installed `async-notify`,
      `SpawnProcess` silently omits it — **no crash**.
- [ ] Inline template strings (e.g. `"<html>{{name}}</html>"`) pass through the
      JSON serialization in `NotifyWorker.build_notify` / `NotifyWrapper`
      without corruption or truncation.
- [ ] File-path template strings (e.g. `"email_applied.html"`) continue to
      work exactly as before (backward compatibility).
- [ ] All new unit tests pass (`pytest tests/ -v`).
- [ ] No breaking changes to existing CLI arguments or public API.

---

## 6. Codebase Contract

### Verified References

#### `qw/conf.py` — Configuration constants
- **Path**: `qw/conf.py`
- Exports: `NOFILES`, `WORKER_REDIS`, `QW_WORKER_LIST`, `WORKER_DISCOVERY_PORT`,
  `WORKER_USE_NAKED_IP`, `QW_MAX_WORKERS`, `WORKER_DEFAULT_HOST`, `WORKER_DEFAULT_PORT`,
  `NOTIFY_DEFAULT_PORT`, `WORKER_DEFAULT_QTY`, `WORKER_QUEUE_SIZE`, etc.
- Uses `navconfig.config` for reading env/config values.
- **Does NOT currently export `TEMPLATE_DIR`** — this must be added.

#### `qw/__main__.py` — CLI entry point
- **Path**: `qw/__main__.py`
- `_add_start_args(parser)` — registers all CLI args for the `start` subcommand.
- Current notify-related args: `--notify_host`, `--notify_port`, `--enable_notify`, `--notify_empty`.
- **Does NOT have `--template-dir`** — this must be added.

#### `qw/process.py` — `SpawnProcess`
- **Path**: `qw/process.py`
- `from notify.server import NotifyWorker` (line 10)
- `SpawnProcess.__init__(self, args)` — reads `args.notify_host`, `args.notify_port`,
  `args.debug`, `args.notify_empty`, `args.enable_notify`.
- `start_notify_worker(self, host, port, debug, name, notify_empty)` (line ~105) —
  instantiates `NotifyWorker(host=, port=, debug=, name=, notify_empty_stream=)`.
- Spawns notify process at lines ~80-98 with `mp.Process(target=self.start_notify_worker, args=(notify_host, notify_port, debug, _name, notify_empty))`.

#### `notify.server.server.NotifyWorker` (external dependency — async-notify)
- **Path**: `.venv/lib/python3.11/site-packages/notify/server/server.py`
- `__init__(self, host, port, debug, name, notify_empty_stream, empty_stream_minutes)` —
  current signature. **Does NOT accept `template_dir`** (will be added in future async-notify release).
- `build_notify(self, data: dict)` — deserializes JSON → `NotifyWrapper(**msg)`.
- `connection_handler(reader, writer)` — TCP handler, reads data → `build_notify` → queue.
- `publish_subscribe()` — Redis pub/sub handler → `build_notify` → call.

#### `notify.server.server.NotifyWrapper` (external dependency)
- `__init__(self, provider: str, *args, **kwargs)` — receives all kwargs including `template`.
- `call()` / `__call__()` — creates `Notify(provider, **kwargs)` then calls
  `client.send(recipient=..., **self.kwargs)`.  `template` flows through as a kwarg.

#### `notify.providers.base.ProviderBase._prepare_` (external dependency)
- `_prepare_(self, recipient, message, template: str = None, **kwargs)` —
  if `template` is set, calls `self._tpl.get_template(template)` (Jinja2 FileSystemLoader lookup).
- This is the method that the new async-notify will modify to support the
  `Union[Path, str]` heuristic.

#### `notify.templates.TemplateParser` (external dependency)
- `__init__(self, directory: Path, filters=None, **kwargs)` — creates Jinja2 env with `FileSystemLoader(searchpath=[str(directory)])`.
- `get_template(filename: str)` — `self.env.get_template(str(filename))`.
- `render(filename, params)` / `render_async(filename, params)`.

#### `notify.conf` (external dependency)
- `TEMPLATE_DIR = config.get('TEMPLATE_DIR') or BASE_DIR / "templates"` —
  the global template dir for async-notify, read from navconfig.

### Does NOT Exist (Anti-Hallucination)

- `qw/conf.py` does **NOT** export `TEMPLATE_DIR` — must be added.
- `qw/__main__.py` does **NOT** have a `--template-dir` argument — must be added.
- `SpawnProcess.start_notify_worker` does **NOT** accept `template_dir` — must be added.
- `NotifyWorker.__init__` does **NOT** currently accept `template_dir` — the new
  async-notify will add it; use introspection guard for forward compatibility.
- There is **NO** `qw/templates.py` module — none is needed; template logic lives in async-notify.
- There is **NO** template validation utility in qworker — none is needed.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- Read configuration via `navconfig.config.get()` in `qw/conf.py` (same pattern
  as all other constants in that file).
- Use `argparse` in `_add_start_args()` for the CLI flag (same pattern as
  `--notify_host`, `--notify_port`).
- Use `inspect.signature` to check if `NotifyWorker.__init__` accepts
  `template_dir` before passing it — this guarantees qworker works with both
  old and new `async-notify` versions.

### Known Risks / Gotchas

- **Forward compatibility**: The new `async-notify` is not yet released.
  Implementation MUST guard `template_dir` behind an introspection check so
  qworker doesn't break with the currently installed version.
- **JSON serialization**: `Path` objects are not JSON-serializable.  Notification
  messages arriving via TCP/Redis carry `template` as a JSON string.  The
  heuristic in `async-notify` will operate on strings, so no `Path` conversion
  is needed on the qworker side — just pass the string through.
- **Multi-line template strings**: Inline templates can be large (full HTML
  emails).  The TCP message protocol in `NotifyWorker` reads until EOF, so there
  is no message-size limitation beyond available memory.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `async-notify` | `>=TBD` (future) | Template heuristic (`Union[Path, str]` support in `_prepare_`) |
| `navconfig` | (existing) | Configuration reading |

---

## 8. Worktree Strategy

- **Isolation**: `per-spec` — all three modules are tightly coupled and small.
- **Parallelizable tasks**: None — changes are sequential (conf → CLI → process).
- **Cross-feature dependencies**: None.

---

## 9. Open Questions

- [x] Will `NotifyWorker.__init__` accept `template_dir` as a keyword argument?
      — **Assumed yes**, based on user confirmation that the new async-notify will
      support it. Guarded by introspection.
- [ ] Should `TEMPLATE_DIR` in `qw/conf.py` default to `None` or to a project-level
      `templates/` directory? — *Owner: Jesus Lara*

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-08-06 | Jesus Lara | Initial draft |
