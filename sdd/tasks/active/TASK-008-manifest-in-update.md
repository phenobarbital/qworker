# TASK-008: Update or Remove MANIFEST.in

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-004
**Assigned-to**: unassigned

---

## Context

The current `MANIFEST.in` includes `graft qw` which would include `.pyx`/`.pxd`/`.c`/`.cpp` files
in the sdist. Since Cython is removed, this file should be updated to only include Python sources,
or removed if `pyproject.toml` `[tool.setuptools.packages.find]` handles packaging.

Implements: Spec Module 8.

---

## Scope

- Update `MANIFEST.in` to exclude Cython-related file types
- OR remove `MANIFEST.in` entirely if `pyproject.toml` handles sdist contents
- Verify that `uv build` produces a clean sdist without `.pyx`/`.c`/`.cpp` files

**NOT in scope**: modifying pyproject.toml (TASK-004), any Python source files, or other build files.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `MANIFEST.in` | MODIFY or DELETE | Update to exclude Cython artifacts |

---

## Codebase Contract (Anti-Hallucination)

### Current MANIFEST.in (lines 1-10)
```
include LICENSE
include CHANGELOG.md
include README.md
include Makefile
graft qw
graft docs
graft tests
global-exclude *.pyc
prune docs/_build
```

### Does NOT Exist
- ~~`CHANGELOG.md`~~ — referenced in MANIFEST.in, verify if it actually exists
- ~~`setup.cfg`~~ — does not exist

---

## Implementation Notes

### Option A: Update MANIFEST.in
```
include LICENSE
include README.md
include Makefile
graft qw
graft docs
graft tests
global-exclude *.pyc
global-exclude *.pyo
global-exclude *.pyx
global-exclude *.pxd
global-exclude *.c
global-exclude *.cpp
global-exclude *.so
prune docs/_build
```

### Option B: Remove MANIFEST.in
If `[tool.setuptools.packages.find]` in pyproject.toml handles everything and `uv build`
produces a clean sdist without MANIFEST.in, then delete it.

### Key Constraints
- Test with `uv build` and inspect the resulting tarball to verify contents
- Check if `CHANGELOG.md` exists; if not, remove the `include` line

---

## Acceptance Criteria

- [ ] `uv build` produces a sdist that does NOT contain `.pyx`, `.pxd`, `.c`, `.cpp`, or `.so` files
- [ ] `uv build` sdist contains `qw/` Python files, LICENSE, README.md
- [ ] Either MANIFEST.in is updated with proper exclusions OR removed if not needed

---

## Agent Instructions

When you pick up this task:

1. **Check dependencies** — verify TASK-004 is completed
2. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
3. **Check** if `CHANGELOG.md` exists: `ls CHANGELOG.md`
4. **Try** `uv build` and inspect the sdist: `tar tzf dist/*.tar.gz | head -50`
5. **Decide** whether to update or remove MANIFEST.in based on sdist contents
6. **Verify** acceptance criteria
7. **Move this file** to `sdd/tasks/completed/TASK-008-manifest-in-update.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
