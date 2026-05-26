# TASK-043: NamedHandlerWrapper

**Feature**: qworker-query-handler
**Spec**: `sdd/specs/qworker-query-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements the `NamedHandlerWrapper` — the wire-format class that carries
a handler name string + positional/keyword arguments from client to server via
cloudpickle serialization. It extends `QueueWrapper` so it integrates with the
existing serialization and dispatch infrastructure.

Implements Spec Module 1.

---

## Scope

- Implement `NamedHandlerWrapper` class in `qw/wrappers/named.py`
- Extends `QueueWrapper` from `qw/wrappers/base.py`
- Stores `handler_name: str`, `args`, `kwargs`
- Sets `queued=False` by default (named handlers are always immediate execution)
- Implement `__repr__` and `__str__` including handler name for logging
- Add export in `qw/wrappers/__init__.py`
- Write unit tests including cloudpickle roundtrip serialization test

**NOT in scope**: Handler resolution logic (lives in TASK-042 HandlerRegistry),
client integration (TASK-044), server dispatch (TASK-045).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/wrappers/named.py` | CREATE | NamedHandlerWrapper class |
| `qw/wrappers/__init__.py` | MODIFY | Add NamedHandlerWrapper to imports and __all__ |
| `tests/test_named_handler_wrapper.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow

Follow the `FuncWrapper` pattern at `qw/wrappers/func.py`:

```python
# qw/wrappers/func.py (lines 7-33) — pattern to follow:
class FuncWrapper(QueueWrapper):
    def __init__(self, host, func, *args, **kwargs):
        super(FuncWrapper, self).__init__(*args, **kwargs)
        self.host = host
        self.func, self.args, self.kwargs = func, args, kwargs

    async def __call__(self):
        # Execute the wrapped function
        ...

    def __repr__(self) -> str:
        return '<%s> from %s' % (self.func.__name__, self.host)
```

The `NamedHandlerWrapper` is similar but stores a handler NAME (string) instead of
a function reference. It does NOT resolve or call the handler itself — that happens
in the server's `handle_named_handler` (TASK-045).

```python
class NamedHandlerWrapper(QueueWrapper):
    def __init__(self, handler_name: str, *args, **kwargs):
        # Force queued=False — named handlers are always immediate
        kwargs.setdefault('queued', False)
        super().__init__(*args, **kwargs)
        self._handler_name = handler_name
        self.args = args
        self.kwargs = kwargs

    @property
    def handler_name(self) -> str:
        return self._handler_name
```

### Key Constraints

- Must extend `QueueWrapper` so `isinstance(task, QueueWrapper)` returns True
  (this matters for server dispatch ordering — see TASK-045)
- `queued` must default to `False` — named handlers bypass the queue
- Must survive cloudpickle.dumps() / cloudpickle.loads() roundtrip
- The `__call__` method is NOT expected to resolve the handler — leave it as a
  no-op or raise NotImplementedError. Resolution happens server-side.

### References in Codebase

```python
# qw/wrappers/base.py — QueueWrapper.__init__ signature (line 18):
def __init__(self, coro=None, *args, **kwargs):
    self._queued: bool = kwargs.pop('queued', True)    # line 19
    self._debug: bool = kwargs.pop('debug', False)     # line 20
    self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())  # line 21
    self._container_config = kwargs.pop('container_config', None)  # line 25
    self.args = args                                    # line 28
    self.kwargs = kwargs                                # line 29
    self.loop = None                                    # line 30
    self.retries = 0                                    # line 32
    self.coro = coro                                    # line 34

# qw/wrappers/__init__.py — current exports (lines 9-24):
from .func import FuncWrapper                           # line 9
from .base import QueueWrapper                          # line 10
try:                                                    # line 11
    from .di_task import TaskWrapper                     # line 12
except Exception as e:                                  # line 13
    TaskWrapper = None                                  # line 16
__all__ = ('QueueWrapper', 'FuncWrapper', 'TaskWrapper')  # line 20
```

---

## Codebase Contract

### Verified Imports

```python
from qw.wrappers.base import QueueWrapper   # qw/wrappers/base.py:14
import cloudpickle                           # already in dependencies (pyproject.toml:45)
```

### QueueWrapper.__init__ kwargs consumed by pop()

The parent `__init__` pops these kwargs: `queued`, `debug`, `id`, `container_config`.
Any kwargs NOT in this set pass through to `self.kwargs`. The `NamedHandlerWrapper`
must ensure `queued` is set before calling `super().__init__()`.

### Does NOT Exist

- ~~`qw/wrappers/named.py`~~ — does not exist; this task creates it
- ~~`NamedHandlerWrapper`~~ — does not exist anywhere in the codebase
- ~~`QueueWrapper.handler_name`~~ — no such attribute on the base class

---

## Acceptance Criteria

- [ ] `NamedHandlerWrapper` class implemented in `qw/wrappers/named.py`
- [ ] Extends `QueueWrapper` — `isinstance(wrapper, QueueWrapper)` is `True`
- [ ] `handler_name` property returns the stored name string
- [ ] `queued` defaults to `False`
- [ ] `args` and `kwargs` stored correctly for later handler invocation
- [ ] `__repr__` and `__str__` include the handler name
- [ ] cloudpickle roundtrip: `loads(dumps(wrapper))` preserves name, args, kwargs
- [ ] Exported from `qw/wrappers/__init__.py` and in `__all__`
- [ ] All tests pass: `pytest tests/test_named_handler_wrapper.py -v`
- [ ] Import works: `from qw.wrappers import NamedHandlerWrapper`
- [ ] Import works: `from qw.wrappers.named import NamedHandlerWrapper`

---

## Test Specification

```python
# tests/test_named_handler_wrapper.py
import pytest
import cloudpickle
from qw.wrappers.named import NamedHandlerWrapper
from qw.wrappers.base import QueueWrapper


class TestNamedHandlerWrapper:
    def test_init_stores_handler_name(self):
        w = NamedHandlerWrapper("test.handler", "arg1", key="val")
        assert w.handler_name == "test.handler"

    def test_extends_queue_wrapper(self):
        w = NamedHandlerWrapper("test.handler")
        assert isinstance(w, QueueWrapper)

    def test_queued_defaults_false(self):
        w = NamedHandlerWrapper("test.handler")
        assert w.queued is False

    def test_args_and_kwargs(self):
        w = NamedHandlerWrapper("test.handler", "slug_name", conditions={"id": 1})
        assert w.args == ("slug_name",)
        assert w.kwargs["conditions"] == {"id": 1}

    def test_repr_includes_handler_name(self):
        w = NamedHandlerWrapper("querysource.remote.query_handler")
        assert "querysource.remote.query_handler" in repr(w)

    def test_str_includes_handler_name(self):
        w = NamedHandlerWrapper("querysource.remote.query_handler")
        assert "querysource.remote.query_handler" in str(w)

    def test_cloudpickle_roundtrip(self):
        original = NamedHandlerWrapper(
            "test.handler", "my-slug", conditions={"store_id": 42}
        )
        data = cloudpickle.dumps(original)
        restored = cloudpickle.loads(data)
        assert restored.handler_name == "test.handler"
        assert restored.args == ("my-slug",)
        assert restored.kwargs["conditions"] == {"store_id": 42}
        assert restored.queued is False

    def test_has_uuid_id(self):
        import uuid
        w = NamedHandlerWrapper("test.handler")
        assert isinstance(w.id, uuid.UUID)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qworker-query-handler.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** `qw/wrappers/named.py` following the FuncWrapper pattern
5. **Modify** `qw/wrappers/__init__.py` to export `NamedHandlerWrapper`
6. **Write tests** in `tests/test_named_handler_wrapper.py`
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-043-named-handler-wrapper.md`
9. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
