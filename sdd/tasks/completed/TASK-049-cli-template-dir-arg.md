# TASK-049: Add --template-dir CLI Argument

**Feature**: notifyworker-template-string
**Spec**: `sdd/specs/notifyworker-template-string.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-048
**Assigned-to**: unassigned

---

## Context

FEAT-008 requires a `--template-dir` CLI flag on the `start` subcommand so
operators can override the template directory at deploy time.  The flag
overrides the `TEMPLATE_DIR` env var / config constant added in TASK-048.

Implements **Module 2** of the spec (§3).

---

## Scope

- Add `--template-dir` argument to `_add_start_args()` in `qw/__main__.py`.
- The argument stores to `args.template_dir`, type `str`, default `None`.
- Write unit tests verifying the argument is parsed correctly.

**NOT in scope**:
- The `TEMPLATE_DIR` config constant — done in TASK-048.
- Modifying `SpawnProcess` — that is TASK-050.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/__main__.py` | MODIFY | Add `--template-dir` parser argument |
| `tests/test_cli_template_dir.py` | CREATE | Unit tests for CLI arg parsing |

---

## Implementation Notes

### Pattern to Follow

All CLI arguments in `_add_start_args` follow the same pattern.  Place the
new argument after the existing notify-related args (`--notify_empty`),
before `--debug`:

```python
# Existing pattern (qw/__main__.py lines 32-40):
parser.add_argument(
    '--notify_host', dest='notify_host', type=str,
    default=WORKER_DEFAULT_HOST,
    help='Set Notify host'
)
parser.add_argument(
    '--notify_port', dest='notify_port', type=int,
    default=NOTIFY_DEFAULT_PORT,
    help='Set Notify Port'
)
```

Add:

```python
parser.add_argument(
    '--template-dir', dest='template_dir', type=str,
    default=None,
    help='Directory for notification templates (overrides TEMPLATE_DIR env var)'
)
```

### Key Constraints

- Use `dest='template_dir'` (underscore) so `args.template_dir` works.
- Default is `None`, not a path — `None` defers to conf / async-notify default.
- Place **after** `--notify_empty` and **before** `--debug` to group with
  notify-related arguments.

---

## Codebase Contract

### Verified References

#### `qw/__main__.py` — `_add_start_args` function (lines 18-91)
```python
def _add_start_args(parser: argparse.ArgumentParser) -> None:
    """Register all arguments for the `start` (default) subcommand."""
    parser.add_argument('--host', ...)
    parser.add_argument('--port', ...)
    parser.add_argument('--notify_host', ...)      # line 32
    parser.add_argument('--notify_port', ...)      # line 37
    parser.add_argument('--workers', ...)
    parser.add_argument('--queue', ...)
    parser.add_argument('--wkname', ...)
    parser.add_argument('--enable-discovery', ...)
    parser.add_argument('--discovery', ...)
    parser.add_argument('--enable_notify', ...)    # line 69
    parser.add_argument('--notify_empty', ...)     # line 74
    parser.add_argument('--debug', ...)            # line 79
    parser.add_argument('--health-port', ...)      # line 84
```

#### `qw/__main__.py` — `run_start` function (line 93)
```python
def run_start(args: argparse.Namespace) -> None:
    """Start the QWorker server processes."""
    ...
    process = SpawnProcess(args)
    ...
```
`SpawnProcess.__init__` reads from `args` — TASK-050 will read `args.template_dir`.

### Does NOT Exist

- `qw/__main__.py` does **NOT** currently have a `--template-dir` argument.
- There is **NO** `--template` or `--templates` argument.

---

## Acceptance Criteria

- [ ] `--template-dir /path/to/templates` is accepted by `qw start`.
- [ ] Omitting `--template-dir` yields `args.template_dir == None`.
- [ ] All tests pass: `pytest tests/test_cli_template_dir.py -v`
- [ ] `python -m qw start --help` shows the `--template-dir` flag.

---

## Test Specification

```python
# tests/test_cli_template_dir.py
import argparse
import pytest


class TestCliTemplateDirArg:
    def _parse_args(self, argv: list[str]) -> argparse.Namespace:
        """Parse argv through the real _add_start_args parser."""
        from qw.__main__ import _add_start_args
        parser = argparse.ArgumentParser()
        _add_start_args(parser)
        return parser.parse_args(argv)

    def test_template_dir_provided(self):
        """--template-dir stores the path string."""
        args = self._parse_args(['--template-dir', '/opt/templates'])
        assert args.template_dir == '/opt/templates'

    def test_template_dir_default_none(self):
        """Omitting --template-dir yields None."""
        args = self._parse_args([])
        assert args.template_dir is None

    def test_template_dir_dest_name(self):
        """dest is 'template_dir' (underscore, not hyphen)."""
        args = self._parse_args(['--template-dir', '/tmp/tpl'])
        assert hasattr(args, 'template_dir')
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/notifyworker-template-string.spec.md` for full context
2. **Check dependencies** — verify TASK-048 is in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-049-cli-template-dir-arg.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-08-06
**Notes**: Added `--template-dir` argument (dest=`template_dir`, type=str,
default=None) to `_add_start_args()` in `qw/__main__.py`, placed after
`--notify_empty` and before `--debug` as specified. Added
`tests/test_cli_template_dir.py` covering provided value, default None,
and dest name. Verified `python -m qw start --help` shows the flag. All 3
tests pass.

**Deviations from spec**: none
