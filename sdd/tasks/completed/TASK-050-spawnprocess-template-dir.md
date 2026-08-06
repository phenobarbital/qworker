# TASK-050: Pass template_dir to NotifyWorker in SpawnProcess

**Feature**: notifyworker-template-string
**Spec**: `sdd/specs/notifyworker-template-string.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-048, TASK-049
**Assigned-to**: unassigned

---

## Context

This is the core task of FEAT-008.  `SpawnProcess` spawns `NotifyWorker` as a
child process.  This task wires the `template_dir` value (from CLI arg or
config) through `SpawnProcess.__init__` → `mp.Process` args →
`start_notify_worker` → `NotifyWorker.__init__()`.

A forward-compatibility guard ensures qworker does NOT crash with the
currently installed `async-notify` version (which does not yet accept
`template_dir`).  When the new version is deployed, the kwarg will flow
through automatically.

Implements **Module 3** of the spec (§3).

---

## Scope

- In `SpawnProcess.__init__`, resolve `template_dir` from `args.template_dir`
  (CLI) falling back to `TEMPLATE_DIR` (conf).
- Pass `template_dir` as an additional positional arg to `mp.Process(args=(...))`.
- Update `start_notify_worker` signature to accept `template_dir: str | None`.
- Use `inspect.signature` to check if `NotifyWorker.__init__` accepts
  `template_dir` before passing it — **introspection guard**.
- Add `TEMPLATE_DIR` to the import block from `.conf`.
- Write unit tests covering: resolution logic, forward-compat guard, and
  backward-compat (old async-notify without `template_dir`).

**NOT in scope**:
- The `TEMPLATE_DIR` config constant — done in TASK-048.
- The `--template-dir` CLI argument — done in TASK-049.
- Modifying `async-notify` itself.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/process.py` | MODIFY | Update SpawnProcess + start_notify_worker |
| `tests/test_spawn_template_dir.py` | CREATE | Unit tests |

---

## Implementation Notes

### Changes to `qw/process.py`

#### 1. Add import (top of file, line 1-20 area)

```python
import inspect  # NEW — for introspection guard
```

Add `TEMPLATE_DIR` to the `.conf` import block (line 13-19):

```python
from .conf import (
    NOFILES,
    WORKER_REDIS,
    QW_WORKER_LIST,
    WORKER_DISCOVERY_PORT,
    WORKER_USE_NAKED_IP,
    QW_MAX_WORKERS,
    TEMPLATE_DIR,       # ← NEW
)
```

#### 2. In `SpawnProcess.__init__` (after line 91, before worker loop)

```python
# Resolve template directory: CLI arg takes precedence over conf
self._template_dir: str | None = getattr(args, 'template_dir', None) or TEMPLATE_DIR
```

#### 3. Update the notify process spawn (lines 113-122)

Add `self._template_dir` as the 6th positional argument:

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

#### 4. Update `start_notify_worker` method signature and body

Current signature (line ~155):
```python
def start_notify_worker(self, host, port, debug, name, notify_empty):
```

New signature:
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
```

New body — build kwargs dict and introspect:
```python
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Build NotifyWorker kwargs
    nw_kwargs: dict = dict(
        host=host,
        port=port,
        debug=debug,
        name=name,
        notify_empty_stream=notify_empty,
    )
    # Forward-compat: pass template_dir only if the installed
    # async-notify version accepts it.
    if template_dir is not None:
        sig = inspect.signature(NotifyWorker.__init__)
        if 'template_dir' in sig.parameters:
            nw_kwargs['template_dir'] = template_dir
    notify_worker = NotifyWorker(**nw_kwargs)
    try:
        loop.run_until_complete(notify_worker.start())
    except KeyboardInterrupt:
        loop.run_until_complete(notify_worker.stop())
    finally:
        _cancel_remaining_tasks(loop)
        loop.close()
```

### Key Constraints

- **Introspection guard is MANDATORY** — without it, qworker crashes on the
  currently installed async-notify which does NOT accept `template_dir`.
- Use `inspect.signature(NotifyWorker.__init__)` — this works with both
  `__init__` defined in Python and Cython extensions.
- The positional arg order in `mp.Process(args=...)` must match the method
  signature exactly. `template_dir` goes **last** with a default of `None`
  for backward compat if something calls the method without it.
- Keep the existing `try/except/finally` structure for `loop.run_until_complete`.

---

## Codebase Contract

### Verified References

#### `qw/process.py` — imports (lines 1-22)
```python
import asyncio                                    # line 1
import uuid                                       # line 2
import multiprocessing as mp                      # line 3
import resource as res                            # line 4
import subprocess                                 # line 5
from collections.abc import Callable              # line 6
import socket                                     # line 7
from redis import asyncio as aioredis             # line 8
from navconfig.logging import logging             # line 9
from notify.server import NotifyWorker            # line 10
from .exceptions import ConfigError               # line 11
from datamodel.parsers.json import json_encoder   # line 12
from .conf import (                               # line 13
    NOFILES,                                      # line 14
    WORKER_REDIS,                                 # line 15
    QW_WORKER_LIST,                               # line 16
    WORKER_DISCOVERY_PORT,                         # line 17
    WORKER_USE_NAKED_IP,                          # line 18
    QW_MAX_WORKERS                                # line 19
)                                                 # line 20
from .server import start_server, _cancel_remaining_tasks  # line 22
from .supervisor import ProcessSupervisor          # line 23
```

#### `qw/process.py` — `SpawnProcess.__init__` (line 62)
```python
class SpawnProcess:
    def __init__(self, args):
        ...
        self.host: str = args.host              # line 68
        self.debug: bool = args.debug           # line 74
        self._enable_notify: bool = args.enable_notify  # line 75
        ...
        self._health_port = getattr(args, 'health_port', 8080)  # line 91
```

#### `qw/process.py` — notify process spawn (lines 110-130)
```python
if self._enable_notify is True:
    try:
        _name = f'NotifyWorker_{self.id}'
        notify_process = mp.Process(
            target=self.start_notify_worker,
            name=_name,
            args=(
                args.notify_host,
                args.notify_port,
                args.debug,
                _name,
                args.notify_empty,
            )
        )
        JOB_LIST.append(notify_process)
        notify_process.start()
```

#### `qw/process.py` — `start_notify_worker` method (line ~155)
```python
def start_notify_worker(
    self,
    host: str,
    port: str,
    debug: bool,
    name: str,
    notify_empty: bool
):
    """Function to start NotifyWorker in a separate process."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    notify_worker = NotifyWorker(
        host=host,
        port=port,
        debug=debug,
        name=name,
        notify_empty_stream=notify_empty
    )
    try:
        loop.run_until_complete(notify_worker.start())
    except KeyboardInterrupt:
        loop.run_until_complete(notify_worker.stop())
    finally:
        _cancel_remaining_tasks(loop)
        loop.close()
```

#### `notify.server.server.NotifyWorker.__init__` (external — current version)
```python
def __init__(
    self,
    host: str = DEFAULT_HOST,
    port: int = NOTIFY_DEFAULT_PORT,
    debug: bool = False,
    name: Optional[str] = None,
    notify_empty_stream: bool = False,
    empty_stream_minutes: int = 10
):
```
**Does NOT accept `template_dir`** — that will be added in a future release.

### Does NOT Exist

- `qw/process.py` does **NOT** import `inspect` — must be added.
- `qw/process.py` does **NOT** import `TEMPLATE_DIR` from conf — must be added (TASK-048 creates it).
- `SpawnProcess` does **NOT** have a `_template_dir` attribute — must be added.
- `start_notify_worker` does **NOT** accept `template_dir` — must be added.
- `NotifyWorker.__init__` does **NOT** accept `template_dir` (current version) — introspection guard handles this.

---

## Acceptance Criteria

- [ ] `SpawnProcess.__init__` resolves `template_dir` from `args.template_dir` or `TEMPLATE_DIR`.
- [ ] `start_notify_worker` accepts `template_dir: str | None = None`.
- [ ] `template_dir` is passed to `NotifyWorker` only when introspection confirms
      the parameter exists (forward compat).
- [ ] When introspection finds no `template_dir` param, `NotifyWorker` is instantiated
      without it — **no crash** (backward compat).
- [ ] `import inspect` is added to the imports.
- [ ] `TEMPLATE_DIR` is imported from `.conf`.
- [ ] All tests pass: `pytest tests/test_spawn_template_dir.py -v`
- [ ] Existing tests remain passing: `pytest tests/ -v`

---

## Test Specification

```python
# tests/test_spawn_template_dir.py
import argparse
import inspect
from unittest.mock import patch, MagicMock
import pytest


class TestSpawnProcessTemplateDir:
    """Tests for template_dir resolution in SpawnProcess."""

    def _make_args(self, template_dir=None):
        """Create a minimal args Namespace."""
        return argparse.Namespace(
            host='127.0.0.1',
            port=18900,
            notify_host='127.0.0.1',
            notify_port=8991,
            workers=1,
            debug=False,
            enable_notify=False,  # don't actually spawn processes
            notify_empty=False,
            wkname='TestWorker',
            health_port=8080,
            template_dir=template_dir,
        )

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.start_server')
    def test_template_dir_from_args(self, _srv, _mgr, _port):
        """CLI arg template_dir is stored on SpawnProcess."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir='/opt/templates')
        sp = SpawnProcess(args)
        assert sp._template_dir == '/opt/templates'

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.start_server')
    @patch('qw.process.TEMPLATE_DIR', '/conf/templates')
    def test_template_dir_fallback_to_conf(self, _srv, _mgr, _port):
        """Falls back to TEMPLATE_DIR from conf when CLI arg is None."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir=None)
        sp = SpawnProcess(args)
        assert sp._template_dir == '/conf/templates'

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.start_server')
    @patch('qw.process.TEMPLATE_DIR', None)
    def test_template_dir_none_when_both_unset(self, _srv, _mgr, _port):
        """template_dir is None when both CLI and conf are unset."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir=None)
        sp = SpawnProcess(args)
        assert sp._template_dir is None


class TestStartNotifyWorkerIntrospection:
    """Tests for the introspection guard on NotifyWorker."""

    def test_template_dir_passed_when_supported(self):
        """template_dir is forwarded when NotifyWorker accepts it."""
        from qw.process import SpawnProcess

        class FakeNotifyWorker:
            def __init__(self, *, host, port, debug, name,
                         notify_empty_stream, template_dir=None):
                self.template_dir = template_dir

            async def start(self):
                pass

        with patch('qw.process.NotifyWorker', FakeNotifyWorker):
            sp = object.__new__(SpawnProcess)
            # call directly — don't run the event loop
            import asyncio
            loop = asyncio.new_event_loop()
            # We just need to verify kwargs are built correctly
            nw_kwargs = dict(
                host='0.0.0.0', port=8991, debug=False,
                name='test', notify_empty_stream=False,
            )
            sig = inspect.signature(FakeNotifyWorker.__init__)
            if 'template_dir' in sig.parameters:
                nw_kwargs['template_dir'] = '/opt/tpl'
            worker = FakeNotifyWorker(**nw_kwargs)
            assert worker.template_dir == '/opt/tpl'
            loop.close()

    def test_template_dir_omitted_when_unsupported(self):
        """template_dir is NOT passed when NotifyWorker doesn't accept it."""
        from qw.process import SpawnProcess

        class OldNotifyWorker:
            def __init__(self, *, host, port, debug, name,
                         notify_empty_stream):
                pass  # no template_dir

            async def start(self):
                pass

        nw_kwargs = dict(
            host='0.0.0.0', port=8991, debug=False,
            name='test', notify_empty_stream=False,
        )
        sig = inspect.signature(OldNotifyWorker.__init__)
        if 'template_dir' in sig.parameters:
            nw_kwargs['template_dir'] = '/opt/tpl'
        # Should NOT raise — template_dir is not in kwargs
        assert 'template_dir' not in nw_kwargs
        worker = OldNotifyWorker(**nw_kwargs)  # no crash
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/notifyworker-template-string.spec.md` for full context
2. **Check dependencies** — verify TASK-048 and TASK-049 are in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-050-spawnprocess-template-dir.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-08-06
**Notes**: Added `import inspect` and `TEMPLATE_DIR` to the `.conf` import
block in `qw/process.py`. `SpawnProcess.__init__` now resolves
`self._template_dir` from `args.template_dir` falling back to
`TEMPLATE_DIR`. The notify-process `mp.Process(args=...)` tuple now passes
`self._template_dir` as the 6th positional arg. `start_notify_worker` gained
`template_dir: str | None = None`, builds an `nw_kwargs` dict, and uses
`inspect.signature(NotifyWorker.__init__)` to add `template_dir` only when
supported (forward-compat guard) — the currently installed `NotifyWorker`
does not accept it, so it is correctly omitted with no crash.

Added `tests/test_spawn_template_dir.py` covering: CLI-arg resolution,
conf-fallback resolution, both-unset (None), introspection-guard passing
`template_dir` when supported, omitting it when unsupported, and a direct
`inspect.signature` sanity check. Patched `qw.process.mp.Process` and
`qw.process.ProcessSupervisor` in the `SpawnProcess.__init__` tests (in
addition to the mocks listed in the spec's test fixture) to prevent the
real supervisor background thread from outliving the `unittest.mock.patch`
context and attempting a real respawn against a live Redis/network — this
was causing the test run to hang; the added patches keep the test scope
exactly on `SpawnProcess.__init__`'s `template_dir` resolution logic with
no behavioral changes to production code.

All 6 new tests pass. Full suite (`pytest tests/ -q`) passes: 384 passed,
no regressions, no orphan processes left behind.

**Deviations from spec**: The test file adds `@patch('qw.process.mp.Process')`
and `@patch('qw.process.ProcessSupervisor')` to the three
`TestSpawnProcessTemplateDir` tests, beyond the mocks given in the spec's
Test Specification (§ Test Specification only mocks `is_port_available`,
`mp.Manager`, `start_server`). This is a test-isolation fix, not a
production-code deviation: without it, `ProcessSupervisor`'s real
background thread survives past the mock context and attempts to respawn
workers using the real `start_server`, hanging on network I/O. No
production code (`qw/process.py`) differs from the spec.
