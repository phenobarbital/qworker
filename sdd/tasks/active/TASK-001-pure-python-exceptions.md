# TASK-001: Convert Cython Exceptions to Pure Python

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

QWorker's exception classes are defined in `qw/exceptions.pyx` as Cython `cdef class` types.
Since we're removing Cython entirely (FEAT-001), these must be converted to standard Python classes.
This is the first task because all other modules import from `qw.exceptions`.

Implements: Spec Module 1.

---

## Scope

- Create `qw/exceptions.py` with all exception classes converted from Cython `cdef class` to standard Python `class`
- Preserve the exact same public API: class names, `__init__` signatures, attributes (`message`, `status`, `stacktrace`), methods (`__str__`, `get`)
- The file replaces the `.pyx` version — do NOT delete `.pyx`/`.pxd` yet (TASK-003 handles cleanup)

**NOT in scope**: deleting `.pyx`/`.pxd`/`.c`/`.so` files (TASK-003), modifying any importers, pyproject.toml changes.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/exceptions.py` | CREATE | Pure Python exceptions (same API as .pyx) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# These imports MUST continue to work after this task:
from qw.exceptions import QWException       # qw/server.py:22, qw/protocols.py:11, qw/queues/manager.py:20, qw/discovery.py:6, qw/client.py:22
from qw.exceptions import ParserError       # qw/server.py:24, qw/client.py:23
from qw.exceptions import DiscardedTask     # qw/server.py:25, qw/client.py:25
from qw.exceptions import ConfigError       # qw/utils/functions.py:5
from qw.exceptions import ProcessNotFound   # defined but not imported elsewhere currently
```

### Existing Signatures to Use
```python
# qw/exceptions.pyx — CONVERT these exact signatures to pure Python:
class QWException(Exception):               # line 5 (cdef class)
    status: int = 400                       # line 8
    stacktrace = None                       # line 12 (set in __init__)
    message: str                            # line 18 (set in __init__)
    def __init__(self, message: str, status: int = 400, **kwargs): ...  # line 10
    def __str__(self) -> str: ...           # line 18, returns f"{self.message}"
    def get(self) -> str: ...               # line 21, returns self.message

class ConfigError(QWException):             # line 25
    def __init__(self, message: str = None): ...  # defaults to "QW Configuration Error.", status=500

class ParserError(QWException):             # line 30
    def __init__(self, message: str = None): ...  # defaults to "JSON Parser Error", status=410

class DiscardedTask(QWException):           # line 35
    def __init__(self, message: str = None): ...  # defaults to "Task was Discarded", status=408

class ProcessNotFound(QWException):         # line 39
    def __init__(self, message: str = None): ...  # defaults to "Task Not Found", status=404
```

### Does NOT Exist
- ~~`qw/exceptions.py`~~ — does not yet exist, you must CREATE it
- ~~`qw.exceptions.QWError`~~ — not a class name; the base class is `QWException`

---

## Implementation Notes

### Pattern to Follow
Direct 1:1 conversion from Cython syntax to Python:
- `cdef class Foo(Bar):` → `class Foo(Bar):`
- Remove `str` type annotations from Cython-style parameters (use Python type hints instead)
- Keep the copyright header and module docstring

### Key Constraints
- Keep the `__all__` tuple exporting all 5 exception classes
- Type hints: use `Optional[str]` for parameters that default to `None`
- The `.so` files may shadow `.py` at import time if they exist — this is expected during development and will be resolved by TASK-003 cleanup

### References in Codebase
- `qw/exceptions.pyx` — source to convert from
- `qw/exceptions.pxd` — Cython declaration file (shows the class hierarchy)

---

## Acceptance Criteria

- [ ] `qw/exceptions.py` exists with all 5 exception classes
- [ ] `from qw.exceptions import QWException, ConfigError, ParserError, DiscardedTask, ProcessNotFound` works
- [ ] `QWException("test", status=500)` creates exception with `.message == "test"`, `.status == 500`
- [ ] `ConfigError()` defaults to message "QW Configuration Error." and status 500
- [ ] `ParserError()` defaults to message "JSON Parser Error" and status 410
- [ ] `DiscardedTask()` defaults to message "Task was Discarded" and status 408
- [ ] `ProcessNotFound()` defaults to message "Task Not Found" and status 404
- [ ] All exceptions are subclasses of `QWException` which is a subclass of `Exception`

---

## Test Specification

```python
# tests/test_exceptions.py
import pytest
from qw.exceptions import (
    QWException, ConfigError, ParserError, DiscardedTask, ProcessNotFound
)


class TestQWException:
    def test_default_status(self):
        exc = QWException("test error")
        assert exc.message == "test error"
        assert exc.status == 400
        assert exc.stacktrace is None

    def test_custom_status(self):
        exc = QWException("error", status=500)
        assert exc.status == 500

    def test_stacktrace(self):
        exc = QWException("error", stacktrace="trace info")
        assert exc.stacktrace == "trace info"

    def test_str(self):
        exc = QWException("test error")
        assert str(exc) == "test error"

    def test_get(self):
        exc = QWException("test error")
        assert exc.get() == "test error"

    def test_is_exception(self):
        assert issubclass(QWException, Exception)


class TestConfigError:
    def test_default(self):
        exc = ConfigError()
        assert exc.message == "QW Configuration Error."
        assert exc.status == 500

    def test_custom_message(self):
        exc = ConfigError("custom error")
        assert exc.message == "custom error"

    def test_hierarchy(self):
        assert issubclass(ConfigError, QWException)


class TestParserError:
    def test_default(self):
        exc = ParserError()
        assert exc.message == "JSON Parser Error"
        assert exc.status == 410


class TestDiscardedTask:
    def test_default(self):
        exc = DiscardedTask()
        assert exc.message == "Task was Discarded"
        assert exc.status == 408


class TestProcessNotFound:
    def test_default(self):
        exc = ProcessNotFound()
        assert exc.message == "Task Not Found"
        assert exc.status == 404
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/uv-migration.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Verify the Codebase Contract** — read `qw/exceptions.pyx` and `qw/exceptions.pxd` to confirm signatures
4. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
5. **Implement** `qw/exceptions.py` as a 1:1 pure-Python conversion
6. **Run tests**: `pytest tests/test_exceptions.py -v`
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-001-pure-python-exceptions.md`
9. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
