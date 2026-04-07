# TASK-003: Delete All Cython Artifacts

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-001, TASK-002
**Assigned-to**: unassigned

---

## Context

After TASK-001 converted exceptions to Python and TASK-002 removed the JSON module,
all Cython source and generated artifacts must be deleted. This includes `.pyx`, `.pxd`,
`.c`, `.cpp`, and `.so` files.

Implements: Spec Module 3.

---

## Scope

- Delete `qw/exceptions.pyx` (original Cython source, now replaced by `qw/exceptions.py`)
- Delete `qw/exceptions.pxd` (Cython declaration file)
- Delete `qw/exceptions.c` (generated C source)
- Delete `qw/exceptions.cpython-*.so` (all compiled shared objects)
- Delete `qw/utils/json.cpp` (generated C++ source)
- Delete `qw/utils/json.cpython-*.so` (if any compiled shared objects exist)
- Delete `qw/exceptions.c` file created by user (`qw/exceptions.c` in git status)
- Delete `qw/utils/json.cpp` file created by user (`qw/utils/json.cpp` in git status)
- Verify NO `.pyx`, `.pxd`, `.c`, `.cpp`, or `.so` files remain under `qw/`

**NOT in scope**: modifying pyproject.toml, setup.py, Makefile, or any Python source files.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/exceptions.pyx` | DELETE | Original Cython source |
| `qw/exceptions.pxd` | DELETE | Cython declaration file |
| `qw/exceptions.c` | DELETE | Generated C source |
| `qw/exceptions.cpython-39-x86_64-linux-gnu.so` | DELETE | Compiled .so |
| `qw/exceptions.cpython-310-x86_64-linux-gnu.so` | DELETE | Compiled .so |
| `qw/exceptions.cpython-311-x86_64-linux-gnu.so` | DELETE | Compiled .so |
| `qw/utils/json.cpp` | DELETE | Generated C++ source |

---

## Codebase Contract (Anti-Hallucination)

### Files to Delete (verified via glob)
```
qw/exceptions.pyx                                    # Cython source
qw/exceptions.pxd                                    # Cython declarations
qw/exceptions.c                                      # Generated C
qw/exceptions.cpython-39-x86_64-linux-gnu.so         # Compiled .so
qw/exceptions.cpython-310-x86_64-linux-gnu.so        # Compiled .so
qw/exceptions.cpython-311-x86_64-linux-gnu.so        # Compiled .so
qw/utils/json.cpp                                    # Generated C++
```

### Does NOT Exist
- ~~`qw/utils/json.pxd`~~ — no `.pxd` file for json
- ~~`qw/utils/json.c`~~ — json used C++ (`.cpp`), not C
- ~~`qw/utils/json.cpython-*.so`~~ — verify at runtime; may or may not exist

---

## Implementation Notes

### Key Constraints
- Use `git rm` for tracked files, `rm` for untracked files
- After deletion, run `find qw/ -name "*.pyx" -o -name "*.pxd" -o -name "*.c" -o -name "*.cpp" -o -name "*.so"` to verify nothing remains
- The `.so` files are in `.gitignore` typically — check if they're tracked or untracked

---

## Acceptance Criteria

- [ ] No `.pyx` files exist under `qw/`
- [ ] No `.pxd` files exist under `qw/`
- [ ] No `.c` files exist under `qw/`
- [ ] No `.cpp` files exist under `qw/`
- [ ] No `.so` files exist under `qw/`
- [ ] `from qw.exceptions import QWException` still works (uses `.py` file now)

---

## Agent Instructions

When you pick up this task:

1. **Check dependencies** — verify TASK-001 and TASK-002 are completed
2. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
3. **List** all Cython artifacts: `find qw/ -name "*.pyx" -o -name "*.pxd" -o -name "*.c" -o -name "*.cpp" -o -name "*.so"`
4. **Delete** each file (use `git rm` for tracked, `rm` for untracked)
5. **Verify** no artifacts remain
6. **Move this file** to `sdd/tasks/completed/TASK-003-cython-artifact-cleanup.md`
7. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-08
**Notes**: Deleted `qw/exceptions.pyx` and `qw/exceptions.pxd` via `git rm`. Note: `qw/utils/json.pyx` was already deleted in TASK-002. No `.c`, `.cpp`, or `.so` files were present in the worktree (already cleaned or never generated in dev environment). Verified no Cython artifacts remain under `qw/`. `from qw.exceptions import QWException` still works via the `.py` file created in TASK-001.

**Deviations from spec**: none
