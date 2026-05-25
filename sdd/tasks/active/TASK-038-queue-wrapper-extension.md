# TASK-038: QueueWrapper Extension

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-032
**Assigned-to**: unassigned

---

## Context

Extends `QueueWrapper` with an optional `container_config` attribute so tasks
can declare their container execution target. Also updates `QClient.publish()`
to include container config in the Redis Stream payload.

Implements Spec Section 3 (Module 7).

---

## Scope

- Modify `qw/wrappers/base.py`:
  - Add `container_config: ContainerConfig | None = None` to `QueueWrapper.__init__`
  - Accept `container_config` from kwargs, default to None
  - Add `container_config` property
- Modify `qw/client.py`:
  - `publish()` method: accept optional `container_config` kwarg
  - Pass it through to `get_wrapped_function()` / wrapper constructor
  - Container config is serialized with the task via cloudpickle (already handles arbitrary objects)
- Write tests for backward compatibility + new functionality

**NOT in scope**: Backend dispatcher (TASK-039), config file mappings (TASK-039)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/wrappers/base.py` | MODIFY | Add container_config attribute |
| `qw/client.py` | MODIFY | Pass container_config through publish() |
| `tests/test_wrapper_extension.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
# In QueueWrapper.__init__ — follow existing kwargs.pop pattern:
class QueueWrapper:
    def __init__(self, coro=None, *args, **kwargs):
        self._queued: bool = kwargs.pop('queued', True)
        self._debug: bool = kwargs.pop('debug', False)
        self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())
        # NEW: container execution config
        self._container_config = kwargs.pop('container_config', None)
        ...

    @property
    def container_config(self):
        return self._container_config

    @container_config.setter
    def container_config(self, value):
        self._container_config = value
```

### Key Constraints
- Backward compatible: existing code that creates QueueWrapper/FuncWrapper without
  `container_config` must work identically
- FuncWrapper and TaskWrapper inherit from QueueWrapper — they get the attribute automatically
- `container_config` must survive cloudpickle serialization (it will, since Pydantic models
  are picklable)
- The import of `ContainerConfig` should be lazy/conditional to avoid circular imports
  (only needed for type hints)

### References in Codebase
```python
# qw/wrappers/base.py:14 — current __init__ signature
def __init__(self, coro=None, *args, **kwargs):
    self._queued: bool = kwargs.pop('queued', True)
    self._debug: bool = kwargs.pop('debug', False)
    self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())

# qw/wrappers/func.py:9 — FuncWrapper.__init__
def __init__(self, host, func, *args, **kwargs):
    super(FuncWrapper, self).__init__(*args, **kwargs)

# qw/client.py:484 — publish signature
async def publish(self, fn: Any, *args, use_wrapper: bool = True, **kwargs):

# qw/client.py:510-518 — wrapper creation in publish
func = self.get_wrapped_function(fn, host, *args, use_wrapper=use_wrapper, queued=True, **kwargs)
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/wrappers/base.py:10-28 (current)
class QueueWrapper:
    _queued: bool = True
    _debug: bool = False
    def __init__(self, coro=None, *args, **kwargs):
        self._queued: bool = kwargs.pop('queued', True)
        self._debug: bool = kwargs.pop('debug', False)
        self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())
        self.args = args
        self.kwargs = kwargs
        self.loop = None
        self.retries = 0
        self.coro = coro

# qw/wrappers/func.py:7-13
class FuncWrapper(QueueWrapper):
    def __init__(self, host, func, *args, **kwargs):
        super(FuncWrapper, self).__init__(*args, **kwargs)
        self.host = host
        self._retry = None
        self.func, self.args, self.kwargs = func, args, kwargs

# qw/client.py:484
async def publish(self, fn: Any, *args, use_wrapper: bool = True, **kwargs):
```

### Does NOT Exist
- No `container_config` field on QueueWrapper currently
- No `ContainerConfig` import in `qw/wrappers/` currently
- `kwargs` in FuncWrapper.__init__ are passed to `super().__init__(**kwargs)` —
  so `container_config` popped in QueueWrapper works transparently for FuncWrapper
  (BUT NOTE: FuncWrapper reassigns `self.kwargs = kwargs` on line 13, which is the
  original kwargs dict — the `container_config` key is already popped at that point)

---

## Acceptance Criteria

- [ ] `QueueWrapper(coro=fn, container_config=cfg)` stores the config
- [ ] `QueueWrapper(coro=fn)` works without container_config (None)
- [ ] `FuncWrapper(host, fn, container_config=cfg)` inherits the attribute
- [ ] `wrapper.container_config` returns the stored config
- [ ] Cloudpickle roundtrip preserves container_config
- [ ] `QClient.publish(fn, container_config=cfg)` passes config to wrapper
- [ ] Existing tests still pass (no regression)
- [ ] All tests pass: `pytest tests/test_wrapper_extension.py -v`

---

## Test Specification

```python
import pytest
import cloudpickle
from qw.wrappers.base import QueueWrapper
from qw.wrappers.func import FuncWrapper
from qw.backends.models import ContainerConfig


class TestQueueWrapperExtension:
    def test_default_no_config(self):
        async def dummy(): pass
        w = QueueWrapper(coro=dummy)
        assert w.container_config is None

    def test_with_container_config(self):
        async def dummy(): pass
        cfg = ContainerConfig(backend="docker", image="python:3.12")
        w = QueueWrapper(coro=dummy, container_config=cfg)
        assert w.container_config is not None
        assert w.container_config.backend == "docker"

    def test_funcwrapper_inherits(self):
        def fn(): pass
        cfg = ContainerConfig(backend="k8s", image="worker:latest")
        w = FuncWrapper("localhost", fn, container_config=cfg)
        assert w.container_config is not None
        assert w.container_config.backend == "k8s"

    def test_funcwrapper_backward_compat(self):
        def fn(): pass
        w = FuncWrapper("localhost", fn)
        assert w.container_config is None

    def test_cloudpickle_roundtrip(self):
        async def dummy(): pass
        cfg = ContainerConfig(backend="docker", image="python:3.12")
        w = QueueWrapper(coro=dummy, container_config=cfg)
        data = cloudpickle.dumps(w)
        w2 = cloudpickle.loads(data)
        assert w2.container_config.backend == "docker"
        assert w2.container_config.image == "python:3.12"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-032 is completed (`qw/backends/models.py` exists)
3. **Implement** changes to `qw/wrappers/base.py` and `qw/client.py`
4. **Run tests**: `pytest tests/test_wrapper_extension.py -v`
5. **Run existing tests** to check for regressions
6. **Verify** acceptance criteria
7. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

*(Agent fills this in when done)*
