# TASK-014: CLI Info Command & Output Formatting

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-011
**Assigned-to**: unassigned

---

## Context

> This task creates the user-facing `qw info` CLI subcommand and the output formatting.
> It refactors `__main__.py` to use argparse subparsers while keeping backward compatibility
> (`qw` with no subcommand still starts the server). Uses `rich` for human-readable tables
> and supports `--json` for machine-parsable output and `--watch N` for polling.
> Implements: Spec Module 6 (CLI Subcommands).

---

## Scope

- Modify `qw/__main__.py`:
  - Refactor from flat argparse to subparsers
  - `qw` (no subcommand) → calls existing `main()` server-start logic (backward compatible)
  - `qw start [options]` → same as above (explicit start)
  - `qw info --host H --port P [--json] [--watch N]` → new info subcommand
- Create `qw/cli/__init__.py` (empty or minimal)
- Create `qw/cli/info.py`:
  - `async def fetch_info(host, port) -> dict` — connects to worker TCP port, sends `info`
    command (following same pattern as `QClient.health()`), receives JSON response. For
    localhost, sends raw `info` command without auth. For remote, uses signature auth
  - `def render_text(data: dict)` — formats state data as rich tables grouped by source
  - `def render_json(data: dict)` — dumps state data as formatted JSON to stdout
  - `def run_info(args)` — orchestrates: fetch → render. If `--watch`, loop with clear+redraw
- Add `rich` to dependencies in `setup.py` `install_requires`

**NOT in scope**:
- QClient.info() method (TASK-015)
- State tracking / instrumentation (TASK-009 through TASK-013)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/__main__.py` | MODIFY | Refactor to argparse subparsers |
| `qw/cli/__init__.py` | CREATE | Package init |
| `qw/cli/info.py` | CREATE | Info command implementation |
| `setup.py` | MODIFY | Add `rich` to install_requires |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# qw/__main__.py existing imports:
import asyncio      # line 4
import argparse     # line 5
from qw.conf import (
    WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT, NOTIFY_DEFAULT_PORT,
    WORKER_DEFAULT_QTY, WORKER_QUEUE_SIZE, WORKER_DISCOVERY_PORT
)  # lines 10-17
from .process import SpawnProcess  # line 19
from .utils import cPrint          # line 20
from .utils.events import enable_uvloop  # line 21

# For CLI info command:
from qw.utils import make_signature  # qw/utils/__init__.py:1 → qw/utils/functions.py:14
from qw.conf import WORKER_SECRET_KEY, expected_message  # qw/conf.py:46,45

# Rich library (NEW dependency):
from rich.console import Console
from rich.table import Table
from rich.live import Live  # for --watch mode

# JSON output:
import json  # stdlib
```

### Existing Signatures to Use
```python
# qw/__main__.py:24 — current main() function
def main():
    enable_uvloop()
    parser = argparse.ArgumentParser(...)
    # Arguments: --host, --port, --notify_host, --notify_port, --workers,
    #   --queue, --wkname, --enable-discovery, --discovery, --enable_notify,
    #   --notify_empty, --debug
    args = parser.parse_args()
    # ... SpawnProcess(args) ... loop.run_forever()

# qw/utils/functions.py:14
def make_signature(message: str, key: str) -> str:
    # HMAC-SHA512 + base64 — returns bytes (base64.b64encode returns bytes)

# qw/client.py:560 — health() pattern for TCP query:
async def health(self):
    task = 'health'
    serialized_task = task.encode('utf-8')
    reader, writer = await self.get_worker_connection()
    await self.sendto_worker(serialized_task, writer=writer)
    # ... read response ... orjson.loads(serialized_result)
    # NOTE: health sends just the command name as bytes, no length prefix or auth
    # The 'health' and 'check_state' commands bypass signature_validation entirely
    # (they match at line 508/514 before the signature check at line 520+)

# setup.py:125-129 — entry_points (NO CHANGE needed)
# setup.py install_requires location needs to be found
```

### Does NOT Exist
- ~~`qw/cli/`~~ — directory does not exist; this task creates it
- ~~`qw/cli/info.py`~~ — does not exist; this task creates it
- ~~`QClient.info()`~~ — does not exist yet (TASK-015); this task uses direct TCP
- ~~argparse subparsers in __main__.py~~ — not currently used; this task adds them

---

## Implementation Notes

### Pattern to Follow
```python
# __main__.py refactored structure:
def main():
    enable_uvloop()
    parser = argparse.ArgumentParser(
        prog='qw',
        description='QueueWorker — Task Queue System'
    )
    subparsers = parser.add_subparsers(dest='command')

    # 'start' subcommand (also the default when no subcommand given)
    start_parser = subparsers.add_parser('start', help='Start worker server')
    # ... all existing --host, --port, --workers, etc. arguments ...
    start_parser.set_defaults(func=run_start)

    # 'info' subcommand
    info_parser = subparsers.add_parser('info', help='Query worker state')
    info_parser.add_argument('--host', default=WORKER_DEFAULT_HOST)
    info_parser.add_argument('--port', type=int, default=WORKER_DEFAULT_PORT)
    info_parser.add_argument('--json', action='store_true', dest='json_output')
    info_parser.add_argument('--watch', type=int, default=None,
                             help='Poll interval in seconds')
    info_parser.set_defaults(func=run_info)

    args = parser.parse_args()
    if args.command is None:
        # No subcommand → start server (backward compatibility)
        run_start(args)  # but args won't have start-specific fields
        # Better: re-parse with start_parser defaults
    else:
        args.func(args)

# TCP info query — IMPORTANT: 'info' command is a pre-auth prefix
# just like 'health' and 'check_state', so for localhost it's sent
# as a raw string. For remote, it requires auth.
```

### Key Constraints
- **Backward compatibility is CRITICAL**: `qw --host 0.0.0.0 --port 8888 --workers 4`
  must still work. The simplest approach: if `args.command is None`, assume `start` and
  reparse with the start parser's defaults
- The TCP `info` command follows the same pre-auth pattern as `health` — the command
  prefix is read BEFORE signature validation happens. For localhost connections, `info`
  is dispatched immediately (like `health`). For remote, the server-side `signature_validation`
  in TASK-011 handles the auth check
- `rich` tables should group tasks by source with clear section headers
- `--watch` mode: use `os.system('clear')` or rich's `Live` context for screen refresh
- Timestamps should be displayed as human-readable datetime strings (convert from float)
- Duration should be displayed in human-readable format (e.g., "3.2s", "1m 45s")

### References in Codebase
- `qw/__main__.py:24-115` — current main() (full refactor target)
- `qw/client.py:560-589` — health() TCP query pattern
- `qw/utils/functions.py:14-22` — make_signature for auth
- `qw/conf.py:15-18` — default host/port constants

---

## Acceptance Criteria

- [ ] `qw` with no subcommand starts the server (backward compatible)
- [ ] `qw start` starts the server with all existing options
- [ ] `qw info --host H --port P` displays worker state as a rich table
- [ ] `qw info --json` outputs valid JSON
- [ ] `qw info --watch N` polls every N seconds with screen refresh
- [ ] Output groups tasks by source: asyncio queue → direct TCP → Redis → broker
- [ ] Each task shows: task_id, function name, time, worker PID, retry count
- [ ] Completed tasks section shows last 10 with duration and result
- [ ] Dead workers (if any) shown with "DEAD" status
- [ ] `rich` added to `setup.py` install_requires
- [ ] Existing `qw` CLI arguments still work (--host, --port, --workers, etc.)

---

## Test Specification

```python
# tests/test_cli_info.py
import pytest
import json

class TestCLIInfoFormatting:
    def test_render_json_valid(self):
        """--json output is valid JSON."""
        from qw.cli.info import render_json
        import io
        from contextlib import redirect_stdout
        data = {
            "worker": {"name": "Test", "pid": 123},
            "state": {"queue": [], "completed": []}
        }
        f = io.StringIO()
        with redirect_stdout(f):
            render_json(data)
        output = f.getvalue()
        parsed = json.loads(output)
        assert "worker" in parsed

    def test_backward_compat_no_subcommand(self):
        """Argparse accepts old-style args without subcommand."""
        # Verify parser.parse_args(['--host', '0.0.0.0', '--port', '8888'])
        # does not raise SystemExit
        ...
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies**:
   - TASK-011 completed: TCP `info` command exists in QWorker
3. **Verify the Codebase Contract**:
   - Read `qw/__main__.py` — confirm current parser structure
   - Read `qw/client.py:560-589` — understand the TCP query pattern
   - Verify `make_signature` works: `grep -n "make_signature" qw/utils/functions.py`
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement**:
   - First: refactor `__main__.py` with subparsers, test backward compatibility
   - Then: create `qw/cli/info.py` with fetch + render
   - Finally: add `rich` to `setup.py`
6. **Verify** `qw` still starts, `qw info --json` works against a running worker
7. **Move this file** to `tasks/completed/TASK-014-cli-info-command.md`
8. **Update index** → `"done"`

---

## Completion Note

**Completed by**: Claude Code (sdd-worker)
**Date**: 2026-04-08
**Notes**: All acceptance criteria met. 8 tests pass. setup.py doesn't exist (removed in
  FEAT-001) so rich was added to pyproject.toml dependencies instead. Backward compat
  maintained: qw with no subcommand still starts server.
**Deviations from spec**: `rich` added to pyproject.toml instead of setup.py (setup.py removed in FEAT-001).
