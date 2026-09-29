# TASK-042: HandlerRegistry

**Feature**: qworker-query-handler
**Spec**: `sdd/specs/qworker-query-handler.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements the general-purpose `HandlerRegistry` — the server-side
component that maps string handler names to callable functions. It is the
foundation that both the server dispatch (TASK-045) and the querysource handler
(TASK-046) depend on.

Implements Spec Module 2.

---

## Scope

- Implement `HandlerRegistry` class in `qw/registry.py`
- Implement module-level `handler_registry` singleton instance
- Implement `register(name, handler)` for programmatic registration
- Implement `resolve(name)` with two-tier resolution:
  1. Explicit registry map (`_handlers` dict)
  2. `importlib.metadata.entry_points(group="qworker.handlers")` — lazy scan, cached
  3. Raise `QWException` if not found
- Implement `list_handlers()` returning `{name: repr(handler)}`
- Write unit tests

**NOT in scope**: Server integration (TASK-045), client changes (TASK-044),
the querysource handler itself (TASK-046), config settings (TASK-047).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/registry.py` | CREATE | HandlerRegistry class + singleton |
| `tests/test_registry.py` | CREATE | Unit tests for registry |

---

## Implementation Notes

### Pattern to Follow

Follow the singleton pattern used by other qworker modules. The registry is a
plain Python class — no Pydantic, no async, no base class needed.

```python
import importlib.metadata
from navconfig.logging import logging
from qw.exceptions import QWException

class HandlerRegistry:
    def __init__(self):
        self._handlers: dict[str, callable] = {}
        self._cache: dict[str, callable] = {}
        self._entry_points_scanned: bool = False
        self.logger = logging.getLogger('QW.Registry')

    def register(self, name: str, handler: callable) -> None:
        ...

    def resolve(self, name: str) -> callable:
        ...

    def list_handlers(self) -> dict[str, str]:
        ...

    def _scan_entry_points(self) -> None:
        ...

handler_registry = HandlerRegistry()
```

### Key Constraints

- Entry_points scan must be lazy — only triggered on first unresolved name
- Scan results must be cached in `_cache` to avoid repeated slow lookups
- Explicit `register()` always takes priority over entry_points
- `resolve()` must raise `QWException` with the handler name in the message
- Use `importlib.metadata.entry_points(group=...)` (Python 3.11+ stdlib)
- Thread-safe: each worker process has its own registry instance; no locking needed

### References in Codebase

- `qw/exceptions.py` — `QWException` for "handler not found" errors
- `qw/conf.py` — pattern for configuration constants (TASK-047 adds the config)

---

## Codebase Contract

### Verified Imports

```python
from qw.exceptions import QWException          # qw/exceptions.py (server.py:17 imports it)
from navconfig.logging import logging           # used across qworker (e.g., executor/__init__.py:5)
import importlib.metadata                       # stdlib, Python 3.11+
```

### Does NOT Exist

- ~~`qw/registry.py`~~ — does not exist; this task creates it
- ~~`handler_registry`~~ — no module-level instance exists anywhere
- ~~`HandlerRegistry`~~ — no such class exists
- ~~`qw.conf.HANDLER_ENTRY_POINTS_GROUP`~~ — config setting does not exist yet (TASK-047)

---

## Acceptance Criteria

- [ ] `HandlerRegistry` class implemented with `register()`, `resolve()`, `list_handlers()`
- [ ] Module-level `handler_registry` singleton exported
- [ ] `register("name", fn)` stores handler; `resolve("name")` returns it
- [ ] `resolve("unknown")` raises `QWException` with handler name in message
- [ ] Entry_points discovery works (tested with mock)
- [ ] Explicit registration takes priority over entry_points
- [ ] Entry_points only scanned once (lazy, cached)
- [ ] All tests pass: `pytest tests/test_registry.py -v`
- [ ] Import works: `from qw.registry import HandlerRegistry, handler_registry`

---

## Test Specification

```python
# tests/test_registry.py
import pytest
from unittest.mock import patch, MagicMock
from qw.registry import HandlerRegistry, handler_registry
from qw.exceptions import QWException


@pytest.fixture
def registry():
    return HandlerRegistry()


@pytest.fixture
def sample_handler():
    async def handler(slug=None, conditions=None, **options):
        return {"result": "ok"}
    return handler


class TestHandlerRegistry:
    def test_register_and_resolve(self, registry, sample_handler):
        registry.register("test.handler", sample_handler)
        resolved = registry.resolve("test.handler")
        assert resolved is sample_handler

    def test_resolve_unknown_raises(self, registry):
        with pytest.raises(QWException, match="test.unknown"):
            registry.resolve("test.unknown")

    def test_explicit_overrides_entry_points(self, registry, sample_handler):
        registry.register("ep.handler", sample_handler)
        resolved = registry.resolve("ep.handler")
        assert resolved is sample_handler

    def test_list_handlers(self, registry, sample_handler):
        registry.register("test.handler", sample_handler)
        handlers = registry.list_handlers()
        assert "test.handler" in handlers

    def test_entry_points_lazy_scan(self, registry):
        assert registry._entry_points_scanned is False
        with pytest.raises(QWException):
            registry.resolve("nonexistent")
        assert registry._entry_points_scanned is True

    def test_entry_points_discovery(self, registry):
        mock_handler = lambda: None
        mock_ep = MagicMock()
        mock_ep.name = "discovered.handler"
        mock_ep.load.return_value = mock_handler
        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            resolved = registry.resolve("discovered.handler")
            assert resolved is mock_handler

    def test_singleton_exists(self):
        assert handler_registry is not None
        assert isinstance(handler_registry, HandlerRegistry)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/qworker-query-handler.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** `qw/registry.py` following the scope and pattern above
5. **Write tests** in `tests/test_registry.py`
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-042-handler-registry.md`
8. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-05-26
**Notes**: All 16 unit tests pass. HandlerRegistry implements register(), resolve(),
list_handlers(), and _scan_entry_points() with lazy entry_points caching.
Module-level handler_registry singleton exported.

**Deviations from spec**: none

**Deviations from spec**: none | describe if any
