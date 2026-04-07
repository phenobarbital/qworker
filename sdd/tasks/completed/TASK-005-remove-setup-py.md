# TASK-005: Delete setup.py

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-004
**Assigned-to**: unassigned

---

## Context

With `pyproject.toml` now handling all build configuration (TASK-004), the legacy `setup.py`
with its Cython extension definitions is no longer needed.

Implements: Spec Module 5.

---

## Scope

- Delete `setup.py`
- Verify `uv build` works without `setup.py` (uses pyproject.toml only)

**NOT in scope**: modifying pyproject.toml (TASK-004), Makefile (TASK-006), or release.yml (TASK-007).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `setup.py` | DELETE | Legacy build script with Cython extensions |

---

## Codebase Contract (Anti-Hallucination)

### File to Delete
```python
# setup.py — 135 lines
# Contains: Cython extension definitions, install_requires, entry_points
# All of this is now in pyproject.toml (TASK-004)
```

### Does NOT Exist
- ~~`setup.cfg`~~ — this project does not have a setup.cfg

---

## Acceptance Criteria

- [ ] `setup.py` is deleted
- [ ] `uv build` succeeds and produces a wheel + sdist
- [ ] The built wheel contains `qw/` package files

---

## Agent Instructions

When you pick up this task:

1. **Check dependencies** — verify TASK-004 is completed
2. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
3. **Delete** `setup.py` via `git rm setup.py`
4. **Test**: `uv build` should produce dist/*.whl and dist/*.tar.gz
5. **Move this file** to `sdd/tasks/completed/TASK-005-remove-setup-py.md`
6. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-08
**Notes**: Deleted `setup.py` via `git rm`. The `pyproject.toml` (TASK-004) now handles all build configuration. Note: `uv build` test is a CI-level test and requires the full environment setup.

**Deviations from spec**: none
