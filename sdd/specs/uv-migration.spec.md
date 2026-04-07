# Feature Specification: UV Migration

**Feature ID**: FEAT-001
**Date**: 2026-04-08
**Author**: Jesus Lara
**Status**: draft
**Target version**: 1.14.0

---

## 1. Motivation & Business Requirements

### Problem Statement

QWorker currently uses a legacy build system based on `setuptools` + `Cython` + `flit`, with:
- `pyproject.toml` using `setuptools==67.6.1` and `Cython==3.0.11` as build requirements
- `setup.py` compiling Cython extensions (`qw/exceptions.pyx`, `qw/utils/json.pyx`)
- A `Makefile` using raw `pip` commands instead of `uv`
- A `release.yml` GitHub Actions workflow that builds Cython manylinux wheels per Python version
- Pinned, fragile dependency versions (`asyncio==3.4.3`, `async-timeout==4.0.3`, etc.)

This makes installation complex (requires a C compiler), CI slow (multi-Python matrix for native wheels), and is inconsistent with the `ai-parrot` project which has already migrated to `uv`.

### Goals
- Migrate `pyproject.toml` to a modern PEP 621-compliant format using `uv` as the package manager
- Remove `setup.py` entirely (no more Cython build step)
- Convert `qw/exceptions.pyx` to pure Python `qw/exceptions.py`
- Convert `qw/utils/json.pyx` to pure Python `qw/utils/json.py`
- Remove all Cython artifacts (`.pxd`, `.c`, `.cpp`, `.so` files)
- Update `Makefile` to use `uv` commands, adapted from `ai-parrot/Makefile`
- Fix `release.yml` to build pure-Python wheels (no manylinux, no Cython)
- Update `MANIFEST.in` if needed, or remove if `pyproject.toml` handles packaging

### Non-Goals (explicitly out of scope)
- Migrating to a monorepo/workspace layout (QWorker is a single package)
- Changing the runtime behavior or API of QWorker
- Adding new dependencies or features
- Changing the minimum Python version (stays `>=3.9`)

---

## 2. Architectural Design

### Overview

This is a build-system migration, not a feature. The changes are:

1. **Cython removal**: Convert `.pyx` files to `.py`, delete `.pxd`/`.c`/`.cpp`/`.so` artifacts
2. **pyproject.toml rewrite**: Full PEP 621 `[project]` table, `uv`-compatible, remove `setup.py`
3. **Makefile modernization**: Replace `pip` with `uv` commands
4. **CI fix**: Simplify `release.yml` to build a single pure-Python wheel + sdist

### Component Diagram
```
pyproject.toml (PEP 621) ──→ uv build ──→ pure-Python wheel + sdist
                                │
Makefile (uv commands) ────────┘
                                │
release.yml (simplified) ──→ uv build ──→ PyPI publish
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `qw/exceptions.pyx` | replaced by | `qw/exceptions.py` (pure Python) |
| `qw/utils/json.pyx` | replaced by | `qw/utils/json.py` (pure Python) |
| `setup.py` | deleted | replaced by `pyproject.toml` |
| `pyproject.toml` | rewritten | PEP 621 format |
| `Makefile` | rewritten | `uv`-based commands |
| `.github/workflows/release.yml` | rewritten | pure-Python build |

### Data Models

No data model changes. Exceptions and JSON utilities retain the same public API.

### New Public Interfaces

No new public interfaces. All existing imports remain valid:
```python
from qw.exceptions import QWException, ConfigError, ParserError, DiscardedTask, ProcessNotFound
from qw.utils.json import JSONContent, json_encoder, json_decoder
```

---

## 3. Module Breakdown

### Module 1: Pure-Python Exceptions
- **Path**: `qw/exceptions.py` (replaces `qw/exceptions.pyx` + `qw/exceptions.pxd`)
- **Responsibility**: Convert all Cython `cdef class` exceptions to standard Python classes
- **Depends on**: nothing

### Module 2: Pure-Python JSON Utilities
- **Path**: `qw/utils/json.py` (replaces `qw/utils/json.pyx`)
- **Responsibility**: Convert `cdef class JSONContent` and `cpdef` functions to pure Python
- **Depends on**: Module 1 (imports `ParserError` from exceptions)

### Module 3: Cython Artifact Cleanup
- **Responsibility**: Delete all Cython-generated files:
  - `qw/exceptions.pxd`, `qw/exceptions.c`, `qw/exceptions.cpython-*.so`
  - `qw/utils/json.cpp`, `qw/utils/json.cpython-*.so` (if any)
  - `qw/exceptions.pyx`, `qw/utils/json.pyx` (originals, after conversion)
- **Depends on**: Modules 1 and 2

### Module 4: pyproject.toml Migration
- **Path**: `pyproject.toml`
- **Responsibility**: Rewrite to PEP 621 `[project]` format with:
  - `[build-system]` using only `setuptools` (no Cython)
  - `[project]` table with name, version (dynamic from `qw/version.py`), dependencies, etc.
  - `[project.scripts]` for the `qw` console entry point
  - `[project.optional-dependencies]` for extras like `tasks`
  - `[dependency-groups]` for dev dependencies
  - `[tool.uv]` section if needed
  - Relaxed dependency versions (use `>=` instead of `==`)
- **Depends on**: nothing

### Module 5: Remove setup.py
- **Path**: `setup.py` (delete)
- **Responsibility**: Remove the legacy setup.py after pyproject.toml handles everything
- **Depends on**: Module 4

### Module 6: Makefile Modernization
- **Path**: `Makefile`
- **Responsibility**: Rewrite using `uv` commands, adapted from `ai-parrot/Makefile` patterns:
  - `venv`: `uv venv`
  - `install`: `uv sync --frozen --no-dev`
  - `develop`: `uv sync`
  - `lock`: `uv lock`
  - `test`: `uv run pytest`
  - `format`/`lint`: `uv run black`/`uv run pylint`
  - `release`: `uv build` + `uv publish`
  - `clean`: remove build artifacts (no more `.so` files)
  - `bump-patch`/`bump-minor`/`bump-major`: version management
- **Depends on**: Module 4

### Module 7: release.yml Fix
- **Path**: `.github/workflows/release.yml`
- **Responsibility**: Simplify CI to:
  - Single Python version build (pure-Python wheel is version-agnostic)
  - Use `uv` to build sdist + wheel
  - Publish to PyPI using `uv publish` or `twine`
  - Remove manylinux wheel building entirely
- **Depends on**: Module 4

### Module 8: MANIFEST.in Update
- **Path**: `MANIFEST.in`
- **Responsibility**: Update to exclude `.pyx`, `.pxd`, `.c`, `.cpp` files. Or remove entirely if `pyproject.toml` `[tool.setuptools.packages.find]` handles it.
- **Depends on**: Module 4

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_exceptions_importable` | Module 1 | `from qw.exceptions import QWException, ConfigError, ParserError, DiscardedTask, ProcessNotFound` |
| `test_exception_hierarchy` | Module 1 | All exceptions inherit from `QWException` which inherits from `Exception` |
| `test_exception_attributes` | Module 1 | `QWException` has `message`, `status`, `stacktrace` attributes |
| `test_json_content_encode` | Module 2 | `JSONContent().encode({"key": "value"})` returns valid JSON string |
| `test_json_content_decode` | Module 2 | `JSONContent().decode('{"key": "value"}')` returns dict |
| `test_json_encoder_function` | Module 2 | `json_encoder(obj)` works as standalone function |
| `test_json_decoder_function` | Module 2 | `json_decoder(obj)` works as standalone function |
| `test_json_parser_error` | Module 2 | Invalid JSON raises `ParserError` |

### Integration Tests
| Test | Description |
|---|---|
| `test_uv_build` | `uv build` produces a valid wheel and sdist |
| `test_pip_install_wheel` | The built wheel installs cleanly in a fresh venv |
| `test_existing_imports` | All existing import patterns in the codebase still work |

### Test Data / Fixtures
```python
@pytest.fixture
def json_content():
    from qw.utils.json import JSONContent
    return JSONContent()
```

---

## 5. Acceptance Criteria

- [ ] `qw/exceptions.py` exists as pure Python, all exception classes have same API
- [ ] `qw/utils/json.py` exists as pure Python, `JSONContent`, `json_encoder`, `json_decoder` have same API
- [ ] No `.pyx`, `.pxd`, `.c`, `.cpp`, or `.so` files remain in the repository
- [ ] `setup.py` is deleted
- [ ] `pyproject.toml` is PEP 621 compliant and `uv build` produces a valid wheel
- [ ] `uv sync` installs all dependencies correctly
- [ ] `Makefile` uses `uv` commands throughout
- [ ] `.github/workflows/release.yml` builds pure-Python wheel and publishes to PyPI
- [ ] All existing imports (`from qw.exceptions import ...`, `from qw.utils.json import ...`) work unchanged
- [ ] `uv run pytest` passes (existing tests still work)
- [ ] No Cython build dependency required for installation

---

## 6. Codebase Contract

> **CRITICAL -- Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.

### Verified Imports
```python
# These imports are used throughout the codebase and MUST continue to work:
from qw.exceptions import QWException       # used in: qw/server.py:22, qw/protocols.py:11, qw/queues/manager.py:20, qw/discovery.py:6, qw/client.py:22
from qw.exceptions import ParserError       # used in: qw/server.py:24, qw/client.py:23
from qw.exceptions import DiscardedTask     # used in: qw/server.py:25, qw/client.py:25
from qw.exceptions import ConfigError       # used in: qw/utils/functions.py:5
from qw.exceptions import ProcessNotFound   # defined in exceptions.pyx:39 (not currently imported elsewhere)
from qw.utils.json import json_encoder, json_decoder  # used in: qw/protocols.py:12
from qw.utils.json import json_decoder      # used in: qw/discovery.py:8
```

### Existing Class Signatures (from Cython source)
```python
# qw/exceptions.pyx — to be converted to qw/exceptions.py
class QWException(Exception):               # line 5 (cdef class)
    status: int = 400                       # line 8
    stacktrace = None                       # line 12 (set in __init__)
    message: str                            # line 18 (set in __init__)
    def __init__(self, message: str, status: int = 400, **kwargs): ...  # line 10
    def __str__(self) -> str: ...           # line 18
    def get(self) -> str: ...               # line 21

class ConfigError(QWException):             # line 25
    def __init__(self, message: str = None): ...  # line 27

class ParserError(QWException):             # line 30
    def __init__(self, message: str = None): ...  # line 32

class DiscardedTask(QWException):           # line 35
    def __init__(self, message: str = None): ...  # line 36

class ProcessNotFound(QWException):         # line 39
    def __init__(self, message: str = None): ...  # line 40

# qw/utils/json.pyx — to be converted to qw/utils/json.py
class JSONContent:                          # line 13 (cdef class)
    def __call__(self, obj, **kwargs): ...  # line 20
    def default(self, obj): ...             # line 23
    def encode(self, obj, **kwargs) -> str: ... # line 47
    dumps = encode                          # line 65
    def decode(self, obj): ...              # line 67
    loads = decode                          # line 77

def json_encoder(obj) -> str: ...           # line 80 (cpdef)
def json_decoder(obj): ...                  # line 83 (cpdef)
```

### Key Dependencies in json.pyx
```python
import orjson                               # json.pyx:10
from asyncpg.pgproto import pgproto         # json.pyx:5 — used for pgproto.UUID
from dataclasses import _MISSING_TYPE, MISSING  # json.pyx:6
```

### Current Build Files
- `pyproject.toml` — line 1-50: uses `setuptools==67.6.1` + `Cython==3.0.11` build-system
- `setup.py` — line 1-135: defines Cython extensions, install_requires, entry_points
- `Makefile` — line 1-33: uses raw `pip` commands
- `.github/workflows/release.yml` — line 1-98: builds manylinux Cython wheels
- `MANIFEST.in` — line 1-10: includes `qw/` graft (includes .pyx files)
- `qw/version.py` — line 3: `__version__ = '1.13.2'`

### Version metadata
```python
# qw/version.py:5
__title__ = 'qworker'
# qw/version.py:9
__version__ = '1.13.2'
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `qw/exceptions.py` | `qw/server.py` | `from qw.exceptions import` | `qw/server.py:22` |
| `qw/exceptions.py` | `qw/client.py` | `from qw.exceptions import` | `qw/client.py:22` |
| `qw/exceptions.py` | `qw/protocols.py` | `from qw.exceptions import` | `qw/protocols.py:11` |
| `qw/exceptions.py` | `qw/discovery.py` | `from qw.exceptions import` | `qw/discovery.py:6` |
| `qw/exceptions.py` | `qw/utils/functions.py` | `from qw.exceptions import` | `qw/utils/functions.py:5` |
| `qw/exceptions.py` | `qw/queues/manager.py` | `from qw.exceptions import` | `qw/queues/manager.py:20` |
| `qw/utils/json.py` | `qw/protocols.py` | `from qw.utils.json import` | `qw/protocols.py:12` |
| `qw/utils/json.py` | `qw/discovery.py` | `from qw.utils.json import` | `qw/discovery.py:8` |

### Does NOT Exist (Anti-Hallucination)
- ~~`qw/exceptions.py`~~ — does not yet exist (only `.pyx` version exists)
- ~~`qw/utils/json.py`~~ — does not yet exist (only `.pyx` version exists)
- ~~`uv.lock`~~ — does not exist yet (will be generated by `uv lock`)
- ~~`[project]` table in pyproject.toml~~ — does not exist, only `[tool.flit.metadata]`
- ~~`[project.dependencies]`~~ — dependencies are only in `setup.py` `install_requires`
- ~~`qw/utils/json.pxd`~~ — no `.pxd` file for json (only exceptions has one)

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Follow `ai-parrot/pyproject.toml` patterns for PEP 621 format
- Follow `ai-parrot/Makefile` patterns for `uv` commands (adapted for single-package)
- Use `[project.scripts]` instead of `setup.py` `entry_points`
- Use `>=` version constraints instead of `==` pinning for most dependencies

### Known Risks / Gotchas
- **Cython `cimport` in json.pyx**: `from qw.exceptions cimport ParserError` must become a regular `import`
- **Performance regression**: `JSONContent` and exceptions were Cython-compiled; pure Python will be marginally slower but this is negligible for exception classes and JSON serialization (orjson does the heavy lifting)
- **Compiled `.so` files in working trees**: Developers must clean old `.so` files or they'll shadow the new `.py` modules. The Makefile `clean` target should handle this.
- **`asyncio==3.4.3` dependency**: This is a backport package that conflicts with modern Python. Should be removed entirely (asyncio is in stdlib since Python 3.4).
- **PyPI secret**: `release.yml` uses `secrets.QUEUEWORKER_TOKEN` — this must remain configured

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `orjson` | `>=3.9.0` | JSON encoding/decoding in `qw/utils/json.py` |
| `asyncpg` | (existing) | `pgproto.UUID` type handling in JSON encoder |
| `uv` | `>=0.4.0` | Build and package management (development tool, not runtime) |

### Dependencies to Review/Update
The following `setup.py` dependencies have strict pins that should be relaxed:
- `asyncio==3.4.3` → **remove** (stdlib)
- `async-timeout==4.0.3` → `async-timeout>=4.0.3`
- `msgpack==1.1.0` → `msgpack>=1.1.0`
- `aiormq==6.9.2` → `aiormq>=6.9.2`
- `modin==0.32.0` → `modin>=0.32.0`
- `dask[complete]==2024.8.2` → `dask[complete]>=2024.8.0`

---

## 8. Open Questions

- [ ] Should `modin` and `dask` remain as core dependencies or move to an optional extra? They are heavy data-processing libraries. — *Owner: Jesus Lara*
- [ ] Should the minimum Python version be bumped from `>=3.9` to `>=3.10` or `>=3.11`? — *Owner: Jesus Lara*
- [ ] Is `asyncio==3.4.3` (backport) safe to remove? It should be, since Python >=3.9 includes asyncio. — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks)
- All 8 modules/tasks run sequentially in one worktree since they have linear dependencies
- **Cross-feature dependencies**: None — this is a standalone infrastructure migration

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-04-08 | Jesus Lara | Initial draft |
