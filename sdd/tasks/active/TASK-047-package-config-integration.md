# TASK-047: Package Configuration & Integration Tests

**Feature**: qworker-query-handler
**Spec**: `sdd/specs/qworker-query-handler.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-042, TASK-043, TASK-044, TASK-045, TASK-046
**Assigned-to**: unassigned

---

## Context

This is the final integration task that wires everything together: entry_points
declaration in `pyproject.toml`, optional dependency group, configuration settings,
and integration tests that verify the full client → server → handler roundtrip.

Implements Spec Module 6 + integration tests from Spec Section 4.

---

## Scope

- Add `querysource` optional dependency group to `pyproject.toml`
- Add `[project.entry-points."qworker.handlers"]` section to `pyproject.toml`
- Add `HANDLER_ENTRY_POINTS_GROUP` config to `qw/conf.py`
- Write integration tests verifying the full dispatch roundtrip
- Verify backward compatibility: existing function-based dispatch is unaffected

**NOT in scope**: Implementation of registry (TASK-042), wrapper (TASK-043),
client (TASK-044), server (TASK-045), or handler (TASK-046) — this task only
wires them together and validates the integration.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY | Add `querysource` extras + entry_points |
| `qw/conf.py` | MODIFY | Add `HANDLER_ENTRY_POINTS_GROUP` setting |
| `tests/test_named_handler_integration.py` | CREATE | Integration tests |

---

## Implementation Notes

### pyproject.toml Changes

```toml
# Add to [project.optional-dependencies] (after existing groups):
querysource = ["querysource"]

# Add new section:
[project.entry-points."qworker.handlers"]
"querysource.remote.query_handler" = "qw.handlers.querysource:query_handler"
```

### qw/conf.py Changes

```python
# Add near the bottom, before the settings.py override block (before line 143):
HANDLER_ENTRY_POINTS_GROUP = config.get(
    'HANDLER_ENTRY_POINTS_GROUP', fallback='qworker.handlers'
)
```

### Integration Test Strategy

The integration tests should verify the full roundtrip WITHOUT needing a running
server. Use direct function calls to simulate the client → server → handler flow:

1. Client creates `NamedHandlerWrapper` from a string
2. cloudpickle roundtrip (serialize + deserialize)
3. Server dispatch logic detects `NamedHandlerWrapper`
4. Registry resolves handler name
5. Handler executes and returns result

### References in Codebase

```python
# pyproject.toml — existing optional-dependencies (lines 51-59):
[project.optional-dependencies]
tasks = ["flowtask>=5.8.20"]
data = ["modin>=0.32.0", "dask[complete]>=2024.8.0"]
docker = ["docker>=7.0"]
k8s = ["kubernetes>=29.0"]
containers = ["docker>=7.0", "kubernetes>=29.0", "psutil>=5.9"]

# qw/conf.py — config pattern (line 15):
WORKER_DEFAULT_HOST = config.get('WORKER_DEFAULT_HOST', fallback='0.0.0.0')

# qw/conf.py — settings.py override block (line 143):
try:
    from settings.settings import (...)
except ImportError:
    pass
```

---

## Codebase Contract

### Verified Signatures (from previous tasks)

```python
# From TASK-042:
from qw.registry import HandlerRegistry, handler_registry
# handler_registry.register(name, handler)
# handler_registry.resolve(name) -> callable

# From TASK-043:
from qw.wrappers import NamedHandlerWrapper
# NamedHandlerWrapper(handler_name, *args, **kwargs)
# .handler_name -> str
# .args, .kwargs

# From TASK-044:
from qw.client import QClient
# QClient.get_wrapped_function(fn, host, *args, **kwargs)
# When fn is str -> returns NamedHandlerWrapper

# From TASK-046:
from qw.handlers.querysource import query_handler
# async def query_handler(slug=None, conditions=None, **options) -> pd.DataFrame
```

### pyproject.toml Structure

```toml
# Line 51: [project.optional-dependencies] section exists
# Line 60: section ends before [project.scripts]
# Line 61: [project.scripts] section
# Line 64: [project.urls] section
# No existing [project.entry-points] section
```

### qw/conf.py Structure

```python
# Line 109: PACKAGE_LIST = config.getlist(...)
# Line 143: try: from settings.settings import ...
# New config should go between line 109-142 area
```

### Does NOT Exist

- ~~`[project.entry-points]`~~ — no entry_points section in pyproject.toml
- ~~`qw.conf.HANDLER_ENTRY_POINTS_GROUP`~~ — does not exist yet; this task adds it
- ~~`querysource` optional dependency group~~ — not yet in pyproject.toml

---

## Acceptance Criteria

- [ ] `pyproject.toml` has `querysource` optional dependency group
- [ ] `pyproject.toml` has entry_points for `querysource.remote.query_handler`
- [ ] `qw/conf.py` has `HANDLER_ENTRY_POINTS_GROUP` setting with default `"qworker.handlers"`
- [ ] Integration test: string dispatch → NamedHandlerWrapper → registry resolve → handler
  execute → result returned (full roundtrip without running server)
- [ ] Integration test: function-based dispatch still works (backward compat)
- [ ] All existing tests still pass (no regression)
- [ ] New tests pass: `pytest tests/test_named_handler_integration.py -v`
- [ ] `ruff check qw/` passes on all modified files

---

## Test Specification

```python
# tests/test_named_handler_integration.py
import pytest
import asyncio
import cloudpickle
import pandas as pd
from qw.client import QClient
from qw.registry import HandlerRegistry
from qw.wrappers.named import NamedHandlerWrapper


class TestNamedHandlerIntegration:
    @pytest.fixture
    def registry(self):
        return HandlerRegistry()

    @pytest.fixture
    def sample_handler(self):
        async def handler(slug=None, conditions=None, **options):
            return pd.DataFrame({"id": [1, 2], "value": [10, 20]})
        return handler

    def test_client_to_wrapper_roundtrip(self):
        """QClient creates NamedHandlerWrapper, cloudpickle roundtrip preserves it."""
        client = QClient(worker_list=[("localhost", 8888)])
        wrapper = client.get_wrapped_function(
            "test.handler", "localhost", "my-slug", conditions={"id": 42}
        )
        assert isinstance(wrapper, NamedHandlerWrapper)

        # Simulate wire transfer
        data = cloudpickle.dumps(wrapper)
        restored = cloudpickle.loads(data)

        assert restored.handler_name == "test.handler"
        assert "my-slug" in restored.args
        assert restored.kwargs["conditions"] == {"id": 42}

    async def test_full_dispatch_roundtrip(self, registry, sample_handler):
        """Full flow: string → wrapper → serialize → resolve → execute → result."""
        registry.register("test.handler", sample_handler)

        # Client side
        client = QClient(worker_list=[("localhost", 8888)])
        wrapper = client.get_wrapped_function(
            "test.handler", "localhost", "my-slug", conditions={"id": 42}
        )

        # Wire transfer
        data = cloudpickle.dumps(wrapper)
        restored = cloudpickle.loads(data)

        # Server side
        handler = registry.resolve(restored.handler_name)
        result = await handler(*restored.args, **restored.kwargs)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2

    def test_function_dispatch_unchanged(self):
        """Non-string fn still works through existing code path."""
        client = QClient(worker_list=[("localhost", 8888)])

        def my_func(x):
            return x * 2

        wrapper = client.get_wrapped_function(my_func, "localhost", 42)
        assert not isinstance(wrapper, NamedHandlerWrapper)
        assert callable(wrapper)

    async def test_mixed_string_and_function_dispatch(self, registry, sample_handler):
        """Both string and function dispatch work in the same client session."""
        registry.register("test.handler", sample_handler)
        client = QClient(worker_list=[("localhost", 8888)])

        # String dispatch
        w1 = client.get_wrapped_function("test.handler", "localhost", "slug1")
        assert isinstance(w1, NamedHandlerWrapper)

        # Function dispatch
        def my_func(x): return x
        w2 = client.get_wrapped_function(my_func, "localhost", 42)
        assert not isinstance(w2, NamedHandlerWrapper)

    def test_config_setting_exists(self):
        """HANDLER_ENTRY_POINTS_GROUP config is accessible."""
        from qw.conf import HANDLER_ENTRY_POINTS_GROUP
        assert HANDLER_ENTRY_POINTS_GROUP == "qworker.handlers"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qworker-query-handler.spec.md` for full context
2. **Check dependencies** — verify TASK-042 through TASK-046 are all in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Modify** `pyproject.toml` — add optional dep + entry_points
5. **Modify** `qw/conf.py` — add HANDLER_ENTRY_POINTS_GROUP
6. **Write tests** in `tests/test_named_handler_integration.py`
7. **Run ALL tests** to verify no regression: `pytest tests/ -v`
8. **Verify** all acceptance criteria are met
9. **Move this file** to `sdd/tasks/completed/TASK-047-package-config-integration.md`
10. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
