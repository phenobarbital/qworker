# TASK-002: Remove qw/utils/json and Rewire Imports to datamodel

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

`qw/utils/json.pyx` provides `JSONContent`, `json_encoder`, `json_decoder` — all of which are
available from `datamodel.parsers.encoders.json` (already a dependency). Rather than converting
the Cython to Python, we delete the file entirely and rewire the 2 callers to use datamodel.

Implements: Spec Module 2.

---

## Scope

- Delete `qw/utils/json.pyx` (the source file)
- Update `qw/protocols.py` line 12: change `from qw.utils.json import json_encoder, json_decoder` to `from datamodel.parsers.encoders.json import json_encoder, json_decoder`
- Update `qw/discovery.py` line 8: change `from qw.utils.json import json_decoder` to `from datamodel.parsers.encoders.json import json_decoder`

**NOT in scope**: deleting `.cpp`/`.so` artifacts (TASK-003), modifying pyproject.toml, Makefile, or any other files.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/utils/json.pyx` | DELETE | Remove Cython JSON module |
| `qw/protocols.py` | MODIFY | Line 12: rewire JSON import to datamodel |
| `qw/discovery.py` | MODIFY | Line 8: rewire JSON import to datamodel |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# CURRENT imports that must be CHANGED:
from qw.utils.json import json_encoder, json_decoder  # qw/protocols.py:12 — CHANGE TO datamodel
from qw.utils.json import json_decoder                # qw/discovery.py:8 — CHANGE TO datamodel

# REPLACEMENT import (verified — datamodel is already used in codebase):
from datamodel.parsers.encoders.json import json_encoder, json_decoder
# datamodel already imported at: qw/broker/rabbit.py:11, qw/broker/pickle.py:6

# These imports in the same files must NOT be touched:
from qw.exceptions import QWException  # qw/protocols.py:11
from qw.exceptions import QWException  # qw/discovery.py:6
```

### Existing Signatures to Use
```python
# qw/protocols.py:11-12 (current state)
from qw.exceptions import QWException
from qw.utils.json import json_encoder, json_decoder  # ← change this line

# qw/discovery.py:6-8 (current state)
from qw.exceptions import QWException
from qw.utils import cPrint
from qw.utils.json import json_decoder  # ← change this line
```

### Does NOT Exist
- ~~`qw/utils/json.py`~~ — will NOT be created; we're removing the module, not converting
- ~~`qw/utils/json.pxd`~~ — no `.pxd` file exists for json (only exceptions has one)
- ~~`datamodel.parsers.json`~~ — wrong path; correct is `datamodel.parsers.encoders.json`
- ~~`datamodel.json`~~ — wrong path; correct is `datamodel.parsers.encoders.json`

---

## Implementation Notes

### Key Constraints
- This is a simple find-and-replace on 2 files + 1 file deletion
- Do NOT modify any other import lines in these files
- The `json_encoder` and `json_decoder` functions from `datamodel.parsers.encoders.json` have the same API as the Cython versions (they were the original source)

### References in Codebase
- `qw/protocols.py` — uses `json_encoder` and `json_decoder` for protocol message serialization
- `qw/discovery.py` — uses `json_decoder` for parsing discovery responses

---

## Acceptance Criteria

- [ ] `qw/utils/json.pyx` is deleted
- [ ] `qw/protocols.py` imports `json_encoder, json_decoder` from `datamodel.parsers.encoders.json`
- [ ] `qw/discovery.py` imports `json_decoder` from `datamodel.parsers.encoders.json`
- [ ] `python -c "from qw.protocols import DiscoveryProtocol"` does not error
- [ ] `python -c "from qw.discovery import get_client_discovery"` does not error
- [ ] No references to `qw.utils.json` remain in `.py` files

---

## Test Specification

```python
# Verification commands (not a pytest file — manual verification):
# python -c "from datamodel.parsers.encoders.json import json_encoder, json_decoder; print('OK')"
# python -c "from qw.protocols import DiscoveryProtocol; print('OK')"
# python -c "from qw.discovery import get_client_discovery; print('OK')"
# grep -r "qw.utils.json" qw/ --include="*.py"  # should return nothing
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/uv-migration.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify** that `from datamodel.parsers.encoders.json import json_encoder, json_decoder` works
4. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
5. **Delete** `qw/utils/json.pyx`
6. **Edit** `qw/protocols.py:12` and `qw/discovery.py:8` to use datamodel imports
7. **Verify** all acceptance criteria
8. **Move this file** to `sdd/tasks/completed/TASK-002-remove-json-rewire-imports.md`
9. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-08
**Notes**: Deleted `qw/utils/json.pyx` via `git rm`. Updated `qw/protocols.py:12` and `qw/discovery.py:8` to import from `datamodel.parsers.encoders.json`. Verified no remaining `qw.utils.json` references in `.py` files.

**Deviations from spec**: none
