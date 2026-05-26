# TASK-045: Server Handler Resolution

**Feature**: qworker-query-handler
**Spec**: `sdd/specs/qworker-query-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-042, TASK-043
**Assigned-to**: unassigned

---

## Context

This task modifies the qworker server to detect and handle `NamedHandlerWrapper`
tasks. When the server deserializes a `NamedHandlerWrapper`, it resolves the
handler name via `HandlerRegistry` and executes it — bypassing the existing
`handle_queue_wrapper` / `TaskExecutor` path entirely.

Implements Spec Module 4.

---

## Scope

- Add `isinstance(task, NamedHandlerWrapper)` check in `connection_handler()`
  BEFORE the existing `isinstance(task, QueueWrapper)` check at line 853
- Implement new method `handle_named_handler(task, uid, writer)` on `QWorker`
- Handle both async and sync handlers (async: await directly; sync: run in executor)
- Integrate with state tracking (`_state.task_executing()` / `task_completed()`)
- Write unit tests

**NOT in scope**: Client-side changes (TASK-044), the querysource handler (TASK-046),
registry implementation (TASK-042), wrapper implementation (TASK-043).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/server.py` | MODIFY | Add NamedHandlerWrapper import, dispatch branch, handle_named_handler() |
| `tests/test_server_named_handler.py` | CREATE | Unit tests for server dispatch |

---

## Implementation Notes

### Exact Change Locations

**1. Import (top of server.py, around line 41-44):**
```python
from .wrappers import QueueWrapper  # existing line 42
# Add:
from .wrappers import NamedHandlerWrapper
from .registry import handler_registry
```

**2. connection_handler() — add branch BEFORE line 853:**
```python
# CURRENT (line 853):
if isinstance(task, QueueWrapper):
    return await self.handle_queue_wrapper(task, task_uuid, writer)
elif callable(task):
    ...

# CHANGE TO:
if isinstance(task, NamedHandlerWrapper):
    return await self.handle_named_handler(task, task_uuid, writer)
elif isinstance(task, QueueWrapper):
    return await self.handle_queue_wrapper(task, task_uuid, writer)
elif callable(task):
    ...
```

**CRITICAL**: `NamedHandlerWrapper` extends `QueueWrapper`, so the isinstance check
for `NamedHandlerWrapper` MUST come first. If `QueueWrapper` is checked first,
named handlers silently enter the wrong execution path.

**3. New method handle_named_handler():**
```python
async def handle_named_handler(
    self,
    task: NamedHandlerWrapper,
    uid: uuid.UUID,
    writer: asyncio.StreamWriter,
):
    task_id = str(uid)
    if self._state is not None:
        self._state.task_executing(task_id, source="tcp")
    try:
        handler = handler_registry.resolve(task.handler_name)
        if asyncio.iscoroutinefunction(handler):
            result = await handler(*task.args, **task.kwargs)
        else:
            loop = asyncio.get_running_loop()
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                from functools import partial
                fn = partial(handler, *task.args, **task.kwargs)
                result = await loop.run_in_executor(executor, fn)
        if self._state is not None:
            self._state.task_completed(task_id, result="success", source="tcp")
        return await self.return_result(writer, result, task, uid)
    except Exception as err:
        if self._state is not None:
            self._state.task_completed(task_id, result="error", source="tcp")
        try:
            result = cloudpickle.dumps(err)
        except Exception as ex:
            result = cloudpickle.dumps(
                QWException(f'Error on handler {task.handler_name!r}: {ex!s}')
            )
        await self.closing_writer(writer, result)
```

### Key Constraints

- Must handle both async and sync handlers (check with `asyncio.iscoroutinefunction()`)
- Error handling must match existing pattern at server.py:797-808 — serialize exception
  and return to client
- State tracking must match pattern at server.py:788-795
- Do NOT use `TaskExecutor` — named handlers have their own execution path

### References in Codebase

```python
# qw/server.py — existing dispatch at line 853:
if isinstance(task, QueueWrapper):                       # line 853
    return await self.handle_queue_wrapper(...)           # line 854
elif callable(task):                                     # line 855
    executor = TaskExecutor(task)                         # line 856

# qw/server.py — state tracking pattern (lines 788-795):
if self._state is not None:
    self._state.task_executing(task_id, source="tcp")
# ...
if self._state is not None:
    self._state.task_completed(task_id, result="success", source="tcp")

# qw/server.py — error serialization pattern (lines 797-808):
except Exception as err:
    try:
        result = cloudpickle.dumps(err)
    except Exception as ex:
        result = cloudpickle.dumps(QWException(...))
    await self.closing_writer(writer, result)

# qw/server.py — return_result (line 698):
async def return_result(self, writer, result, task, uid):
```

---

## Codebase Contract

### Verified Imports (available after TASK-042 and TASK-043)

```python
from qw.wrappers import NamedHandlerWrapper  # TASK-043 adds to __init__.py
from qw.registry import handler_registry     # TASK-042 creates qw/registry.py
```

### Verified Existing Signatures

```python
# qw/server.py:810 — connection_handler full signature:
async def connection_handler(
    self,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter
):

# qw/server.py:698 — return_result:
async def return_result(self, writer: asyncio.StreamWriter, result, task, uid):

# qw/server.py:899 — closing_writer:
async def closing_writer(self, writer: asyncio.StreamWriter, result):

# qw/state.py — StateTracker methods (verified):
# task_executing(task_id, source="tcp")
# task_completed(task_id, result="success"|"error", source="tcp")
```

### Does NOT Exist

- ~~`QWorker.handle_named_handler()`~~ — does not exist; this task creates it
- ~~`QWorker.registry`~~ — no registry attribute on QWorker; use module-level `handler_registry`
- ~~`NamedHandlerWrapper` check in connection_handler~~ — does not exist; this task adds it

---

## Acceptance Criteria

- [ ] `connection_handler()` routes `NamedHandlerWrapper` to `handle_named_handler()`
- [ ] `QueueWrapper` instances (non-NamedHandlerWrapper) still route to `handle_queue_wrapper()`
- [ ] `handle_named_handler()` resolves handler via `handler_registry.resolve()`
- [ ] Async handlers are awaited directly
- [ ] Sync handlers are run in a thread executor
- [ ] Handler exceptions are serialized and returned to client
- [ ] Unknown handler names return `QWException` to client
- [ ] State tracking calls (`task_executing`, `task_completed`) work correctly
- [ ] All existing server tests still pass (no regression)
- [ ] New tests pass: `pytest tests/test_server_named_handler.py -v`

---

## Test Specification

```python
# tests/test_server_named_handler.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from qw.wrappers.named import NamedHandlerWrapper
from qw.exceptions import QWException


class TestServerNamedHandler:
    @pytest.fixture
    def mock_writer(self):
        writer = AsyncMock()
        writer.get_extra_info.return_value = ("127.0.0.1", 8888)
        writer.is_closing.return_value = False
        return writer

    @pytest.fixture
    def wrapper(self):
        return NamedHandlerWrapper(
            "test.handler", "my-slug", conditions={"id": 42}
        )

    async def test_named_handler_resolves_and_executes(self):
        """NamedHandlerWrapper triggers handler resolution and execution."""
        result_value = {"data": [1, 2, 3]}
        async def test_handler(slug=None, conditions=None, **opts):
            return result_value

        with patch("qw.registry.handler_registry") as mock_reg:
            mock_reg.resolve.return_value = test_handler
            # Verify resolve is called with the handler name
            mock_reg.resolve("test.handler")
            mock_reg.resolve.assert_called_with("test.handler")

    async def test_unknown_handler_returns_error(self):
        """Unresolved handler name produces QWException."""
        from qw.registry import HandlerRegistry
        reg = HandlerRegistry()
        with pytest.raises(QWException, match="not.registered"):
            reg.resolve("not.registered")

    def test_named_wrapper_isinstance_queue_wrapper(self, wrapper):
        """NamedHandlerWrapper IS a QueueWrapper (for isinstance ordering)."""
        from qw.wrappers.base import QueueWrapper
        assert isinstance(wrapper, QueueWrapper)
        assert isinstance(wrapper, NamedHandlerWrapper)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qworker-query-handler.spec.md` for full context
2. **Check dependencies** — verify TASK-042 and TASK-043 are in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Read** `qw/server.py` lines 810-860 to see the current dispatch logic
5. **Add** the `NamedHandlerWrapper` isinstance check BEFORE the `QueueWrapper` check
6. **Implement** `handle_named_handler()` method
7. **Write tests** in `tests/test_server_named_handler.py`
8. **Run existing tests** to verify no regression
9. **Verify** all acceptance criteria are met
10. **Move this file** to `sdd/tasks/completed/TASK-045-server-handler-resolution.md`
11. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
