# TASK-044: QClient String Dispatch

**Feature**: qworker-query-handler
**Spec**: `sdd/specs/qworker-query-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-043
**Assigned-to**: unassigned

---

## Context

This task modifies `QClient.get_wrapped_function()` to detect when `fn` is a string
and create a `NamedHandlerWrapper` instead of the current `partial(str, *args)` which
is uncallable. This is the client-side half of string-based handler dispatch.

Implements Spec Module 3.

---

## Scope

- Modify `QClient.get_wrapped_function()` in `qw/client.py` to add a string detection
  branch at the top of the method, BEFORE the existing `isinstance(fn, (TaskWrapper, FuncWrapper))`
  check at line 308
- When `fn` is a `str`, return `NamedHandlerWrapper(fn, *args, **kwargs)`
- Write unit tests verifying string → NamedHandlerWrapper, function → unchanged behavior

**NOT in scope**: Server-side dispatch (TASK-045), registry (TASK-042),
the querysource handler (TASK-046).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/client.py` | MODIFY | Add string detection in `get_wrapped_function()` |
| `tests/test_client_string_dispatch.py` | CREATE | Unit tests for string dispatch |

---

## Implementation Notes

### Exact Change Location

```python
# qw/client.py — get_wrapped_function() at line 299
# Current code (lines 308-323):
def get_wrapped_function(self, fn, host, *args, use_wrapper=False, queued=False, **kwargs):
    if isinstance(fn, (TaskWrapper, FuncWrapper)):     # line 308
        func = fn                                      # line 310
        func.queued = queued                           # line 311
    elif use_wrapper is True:                          # line 312
        func = FuncWrapper(host, fn, *args, **kwargs)  # line 314
        func.queued = queued                           # line 320
    else:                                              # line 321
        func = partial(fn, *args, **kwargs)            # line 323
    return func                                        # line 324

# ADD this branch at the TOP (before line 308):
    if isinstance(fn, str):
        return NamedHandlerWrapper(fn, *args, **kwargs)
    # ... existing code follows unchanged
```

### Key Constraints

- The string check MUST come before `isinstance(fn, (TaskWrapper, FuncWrapper))`
- Must import `NamedHandlerWrapper` from `qw.wrappers.named` or `qw.wrappers`
- Existing behavior for function objects, TaskWrapper, FuncWrapper must be UNCHANGED
- The `use_wrapper` and `queued` parameters are irrelevant for string dispatch —
  `NamedHandlerWrapper` always has `queued=False`
- The `host` parameter is not passed to `NamedHandlerWrapper` (it carries only
  handler name + args + kwargs)

### References in Codebase

```python
# qw/client.py — current imports (top of file, around lines 1-20):
from functools import partial
from qw.wrappers import QueueWrapper, FuncWrapper, TaskWrapper

# The NamedHandlerWrapper import should be added alongside the existing wrapper imports
```

---

## Codebase Contract

### Verified Signatures

```python
# qw/client.py:299 — method to modify:
def get_wrapped_function(
    self,
    fn: Any,
    host: str,
    *args,
    use_wrapper: bool = False,
    queued: bool = False,
    **kwargs
):

# qw/client.py:326 — run() calls get_wrapped_function:
async def run(self, fn: Any, *args, use_wrapper: bool = False, **kwargs):
    # ...
    func = self.get_wrapped_function(
        fn, host, *args, use_wrapper=use_wrapper, queued=False, **kwargs
    )
```

### Verified Imports (must be available after TASK-043)

```python
from qw.wrappers import NamedHandlerWrapper  # added by TASK-043 to __init__.py
from qw.wrappers.named import NamedHandlerWrapper  # direct import also works
```

### Does NOT Exist

- ~~`QClient.run_handler()`~~ — no such method; string dispatch goes through existing `run()`
- ~~`QClient.run()` string detection~~ — currently `run()` passes `fn` directly to
  `get_wrapped_function()`, which wraps strings in `partial(str, ...)` — uncallable

---

## Acceptance Criteria

- [ ] `get_wrapped_function("handler.name", host, "arg1", key="val")` returns a
  `NamedHandlerWrapper` with correct name, args, kwargs
- [ ] `get_wrapped_function(function_obj, host)` still returns FuncWrapper/partial
  (backward compatible)
- [ ] `get_wrapped_function(TaskWrapper(...), host)` still passes through (unchanged)
- [ ] All existing qworker client tests still pass
- [ ] New tests pass: `pytest tests/test_client_string_dispatch.py -v`

---

## Test Specification

```python
# tests/test_client_string_dispatch.py
import pytest
from unittest.mock import MagicMock
from qw.client import QClient
from qw.wrappers.named import NamedHandlerWrapper
from qw.wrappers import FuncWrapper


class TestClientStringDispatch:
    @pytest.fixture
    def client(self):
        return QClient(worker_list=[("localhost", 8888)])

    def test_string_creates_named_wrapper(self, client):
        result = client.get_wrapped_function(
            "test.handler", "localhost", "my-slug", conditions={"id": 1}
        )
        assert isinstance(result, NamedHandlerWrapper)
        assert result.handler_name == "test.handler"

    def test_string_wrapper_has_args(self, client):
        result = client.get_wrapped_function(
            "test.handler", "localhost", "slug", conditions={"id": 42}
        )
        assert "slug" in result.args
        assert result.kwargs["conditions"] == {"id": 42}

    def test_function_still_returns_partial(self, client):
        def my_func(x): return x
        result = client.get_wrapped_function(my_func, "localhost", 42)
        assert not isinstance(result, NamedHandlerWrapper)

    def test_function_with_use_wrapper_returns_func_wrapper(self, client):
        def my_func(x): return x
        result = client.get_wrapped_function(
            my_func, "localhost", 42, use_wrapper=True
        )
        assert isinstance(result, FuncWrapper)

    def test_backward_compat_existing_behavior(self, client):
        """Ensure non-string fn follows the original code path."""
        async def async_fn(x): return x
        result = client.get_wrapped_function(async_fn, "localhost", "arg1")
        assert not isinstance(result, NamedHandlerWrapper)
        assert callable(result)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qworker-query-handler.spec.md` for full context
2. **Check dependencies** — verify TASK-043 is in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Modify** `qw/client.py` — add string detection branch in `get_wrapped_function()`
5. **Write tests** in `tests/test_client_string_dispatch.py`
6. **Run existing tests** to verify no regression
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-044-qclient-string-dispatch.md`
9. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
