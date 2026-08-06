# TASK-048: Add TEMPLATE_DIR Configuration Constant

**Feature**: notifyworker-template-string
**Spec**: `sdd/specs/notifyworker-template-string.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

FEAT-008 requires qworker to propagate a `template_dir` to `NotifyWorker` so
the forthcoming `async-notify` template heuristic can resolve file-based
templates from a configurable directory.  This task adds the configuration
constant that all downstream tasks depend on.

Implements **Module 1** of the spec (§3).

---

## Scope

- Add `TEMPLATE_DIR` constant to `qw/conf.py`, read from `navconfig.config`.
- Default to `None` (async-notify falls back to its own `TEMPLATE_DIR`).
- Add the constant to the existing import block in `qw/process.py` so it's
  available when needed (TASK-050 will use it).
- Write unit tests verifying default and env-override behaviour.

**NOT in scope**:
- CLI argument (`--template-dir`) — that is TASK-049.
- Modifying `SpawnProcess` — that is TASK-050.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/conf.py` | MODIFY | Add `TEMPLATE_DIR` constant |
| `tests/test_conf_template_dir.py` | CREATE | Unit tests for the new constant |

---

## Implementation Notes

### Pattern to Follow

All config constants in `qw/conf.py` use the same pattern:

```python
# Existing pattern (qw/conf.py line 17):
NOTIFY_DEFAULT_PORT = config.getint('NOTIFY_DEFAULT_PORT', fallback=8989)
```

Add the new constant in the `### Worker Configuration` section, near the
existing notify-related constants (around line 17):

```python
# Template directory for NotifyWorker (overridden by --template-dir CLI arg)
TEMPLATE_DIR: str | None = config.get('TEMPLATE_DIR', fallback=None)
```

### Key Constraints

- Use `config.get()` (not `config.getint()`) — it's a string/path.
- Default to `None`, not to a path — `None` means "let async-notify decide".
- Type annotation `str | None` (Python 3.10+ union syntax, consistent with
  the rest of qworker which uses `|` unions e.g. `ProcessSupervisor | None`
  in `qw/process.py` line 137).

---

## Codebase Contract

### Verified References

#### `qw/conf.py` (line 1)
```python
from navconfig import config, ENVIRONMENT, ENV
```
- `config` is a `navconfig` ConfigParser-like object.
- `config.get(key, fallback=...)` returns `str | None`.
- `config.getint(key, fallback=...)` returns `int`.

#### `qw/conf.py` — existing notify constants (lines 17-18)
```python
NOTIFY_DEFAULT_PORT = config.getint('NOTIFY_DEFAULT_PORT', fallback=8989)
WORKER_DEFAULT_QTY = config.getint('WORKER_DEFAULT_QTY', fallback=4)
```

#### `qw/process.py` — current imports from conf (lines 13-19)
```python
from .conf import (
    NOFILES,
    WORKER_REDIS,
    QW_WORKER_LIST,
    WORKER_DISCOVERY_PORT,
    WORKER_USE_NAKED_IP,
    QW_MAX_WORKERS
)
```

### Does NOT Exist

- `qw/conf.py` does **NOT** currently have a `TEMPLATE_DIR` constant.
- There is **NO** `qw/templates.py` module.

---

## Acceptance Criteria

- [ ] `TEMPLATE_DIR` exported from `qw/conf.py`.
- [ ] Default value is `None` when `TEMPLATE_DIR` env var is unset.
- [ ] Returns string value when `TEMPLATE_DIR` env var is set.
- [ ] All tests pass: `pytest tests/test_conf_template_dir.py -v`
- [ ] Import works: `from qw.conf import TEMPLATE_DIR`

---

## Test Specification

```python
# tests/test_conf_template_dir.py
import pytest


class TestTemplateDir:
    def test_template_dir_default_none(self, monkeypatch):
        """TEMPLATE_DIR defaults to None when env var is unset."""
        monkeypatch.delenv('TEMPLATE_DIR', raising=False)
        # Re-import to pick up change
        import importlib
        import qw.conf
        importlib.reload(qw.conf)
        assert qw.conf.TEMPLATE_DIR is None

    def test_template_dir_from_env(self, monkeypatch):
        """TEMPLATE_DIR reads value from env var."""
        monkeypatch.setenv('TEMPLATE_DIR', '/opt/templates')
        import importlib
        import qw.conf
        importlib.reload(qw.conf)
        assert qw.conf.TEMPLATE_DIR == '/opt/templates'

    def test_template_dir_importable(self):
        """TEMPLATE_DIR is importable from qw.conf."""
        from qw.conf import TEMPLATE_DIR
        assert TEMPLATE_DIR is None or isinstance(TEMPLATE_DIR, str)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/notifyworker-template-string.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-048-conf-template-dir.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
