# TASK-004: Rewrite pyproject.toml to PEP 621 with uv

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

The current `pyproject.toml` uses a legacy format with `setuptools==67.6.1` + `Cython==3.0.11`
as build requirements and `[tool.flit.metadata]` for metadata. It must be rewritten to a modern
PEP 621 `[project]` table that works with `uv`.

Implements: Spec Module 4.

---

## Scope

- Rewrite `pyproject.toml` with:
  - `[build-system]` using `setuptools>=75.0` only (no Cython, no flit)
  - `[project]` table with: name (`qworker`), dynamic version from `qw/version.py`, description, authors, license, classifiers, `requires-python = ">=3.11"`
  - `[project.dependencies]` with relaxed version pins (from current `setup.py` `install_requires`):
    - Remove `asyncio==3.4.3` entirely
    - Remove `orjson` (handled transitively by datamodel)
    - Move `modin` and `dask` to `[project.optional-dependencies.data]`
    - Relax `==` pins to `>=`
  - `[project.optional-dependencies]` with `tasks` (flowtask) and `data` (modin, dask) extras
  - `[project.scripts]` for the `qw` console entry point: `qw = "qw.__main__:main"`
  - `[dependency-groups]` for dev dependencies (pytest, black, pylint, mypy, etc.)
  - `[tool.setuptools.packages.find]` to auto-discover packages
  - `[tool.setuptools.dynamic]` for version from `qw/version.py`
  - Keep existing `[tool.pytest.ini_options]` section (already present)
- The `[tool.flit.metadata]` section must be removed

**NOT in scope**: Deleting `setup.py` (TASK-005), modifying Makefile (TASK-006), modifying release.yml (TASK-007).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY (full rewrite) | PEP 621 format with uv compatibility |

---

## Codebase Contract (Anti-Hallucination)

### Current pyproject.toml Structure
```toml
# pyproject.toml lines 1-9: build-system with setuptools + Cython
[build-system]
requires = ['setuptools==67.6.1', 'Cython==3.0.11', 'wheel==0.44.0', 'flit']
build-backend = "setuptools.build_meta"

# lines 11-36: [tool.flit.metadata] — REMOVE entirely
# lines 40-49: [tool.pytest.ini_options] — KEEP
```

### Current Dependencies from setup.py (lines 104-118)
```python
install_requires=[
    'asyncio==3.4.3',              # REMOVE (stdlib)
    'ciso8601>=2.2.0',             # KEEP
    'navconfig[uvloop,default]>=1.7.9',  # KEEP
    'asyncdb[default]>=2.9.0',     # KEEP
    'async-notify[default]>=1.3.4',# KEEP
    'cloudpickle>=3.0.0',          # KEEP
    'jsonpickle>=3.0.2',           # KEEP
    'beautifulsoup4>=4.12.3',      # KEEP
    'async-timeout==4.0.3',        # RELAX to >=4.0.3
    'msgpack==1.1.0',              # RELAX to >=1.1.0
    'aiormq==6.9.2',               # RELAX to >=6.9.2
    'modin==0.32.0',               # MOVE to [data] extra, relax to >=0.32.0
    'dask[complete]==2024.8.2'     # MOVE to [data] extra, relax to >=2024.8.0
],
extras_require={
    "tasks": ['flowtask>=5.8.20']  # KEEP as [project.optional-dependencies.tasks]
},
```

### Entry Point from setup.py (line 125-128)
```python
entry_points={
    'console_scripts': ['qw = qw.__main__:main']
},
```

### Version metadata (qw/version.py)
```python
__title__ = 'qworker'       # line 5
__version__ = '1.13.2'      # line 9
```

### Reference: ai-parrot pyproject.toml patterns
```toml
# ai-parrot uses:
[build-system]
requires = ["setuptools>=67.6.1"]
build-backend = "setuptools.build_meta"

[tool.uv.workspace]  # NOT needed for single-package

[dependency-groups]
dev = ["pytest>=7.2.2", "black", "pylint", "mypy", ...]
```

### Does NOT Exist
- ~~`[project]` table~~ — does not exist in current pyproject.toml
- ~~`[project.dependencies]`~~ — dependencies are only in setup.py
- ~~`uv.lock`~~ — will be generated later by `uv lock`
- ~~`[tool.uv.workspace]`~~ — not needed for single package (don't add it)

---

## Implementation Notes

### Pattern to Follow
```toml
[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[project]
name = "qworker"
dynamic = ["version"]
description = "QueueWorker is asynchronous Task Queue implementation built on top of Asyncio."
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"
authors = [
    {name = "Jesus Lara", email = "jesuslarag@gmail.com"}
]
keywords = ["distributed objects", "workers", "asyncio", "task queue", "RPC"]
classifiers = [...]

dependencies = [
    "ciso8601>=2.2.0",
    "navconfig[uvloop,default]>=1.7.9",
    "asyncdb[default]>=2.9.0",
    "async-notify[default]>=1.3.4",
    "cloudpickle>=3.0.0",
    "jsonpickle>=3.0.2",
    "beautifulsoup4>=4.12.3",
    "async-timeout>=4.0.3",
    "msgpack>=1.1.0",
    "aiormq>=6.9.2",
]

[project.optional-dependencies]
tasks = ["flowtask>=5.8.20"]
data = [
    "modin>=0.32.0",
    "dask[complete]>=2024.8.0",
]

[project.scripts]
qw = "qw.__main__:main"

[project.urls]
Source = "https://github.com/phenobarbital/qworker"
Funding = "https://paypal.me/phenobarbital"

[tool.setuptools.dynamic]
version = {attr = "qw.version.__version__"}

[tool.setuptools.packages.find]
exclude = ["contrib", "docs", "tests", "settings"]

[dependency-groups]
dev = [
    "pytest>=7.2.2",
    "pytest-asyncio>=0.21.0",
    "black",
    "pylint",
    "mypy",
    "coverage",
]

# KEEP existing [tool.pytest.ini_options] section
```

### Key Constraints
- Version MUST be dynamic, read from `qw/version.py` via `[tool.setuptools.dynamic]`
- Do NOT add Cython anywhere
- Do NOT add `orjson` to dependencies (handled by datamodel transitively)
- Do NOT add `asyncio` to dependencies
- Update Python version classifiers to only include 3.11, 3.12, 3.13

---

## Acceptance Criteria

- [ ] `pyproject.toml` has `[project]` table with PEP 621 metadata
- [ ] `requires-python = ">=3.11"`
- [ ] No Cython in `[build-system].requires`
- [ ] No `asyncio` in dependencies
- [ ] No `orjson` in dependencies
- [ ] `modin` and `dask` are in `[project.optional-dependencies.data]`
- [ ] `[project.scripts]` defines `qw = "qw.__main__:main"`
- [ ] `[tool.flit.metadata]` section is removed
- [ ] `[tool.pytest.ini_options]` is preserved
- [ ] `uv sync` resolves dependencies (after `setup.py` removal in TASK-005)

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/uv-migration.spec.md` for full context
2. **Read current** `pyproject.toml` and `setup.py` to extract all metadata
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Rewrite** `pyproject.toml` following the pattern above
5. **Verify** the TOML is valid: `python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))"`
6. **Move this file** to `sdd/tasks/completed/TASK-004-pyproject-toml-migration.md`
7. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-08
**Notes**: Rewrote `pyproject.toml` to PEP 621 format. Removed `[tool.flit.metadata]`, added `[project]` table with `requires-python = ">=3.11"`, dynamic version from `qw/version.py`, all core deps with relaxed pins, `modin`/`dask` in `[data]` extra, `[project.scripts]` for `qw` entrypoint, `[dependency-groups]` for dev deps. No Cython, no `asyncio`, no `orjson` in deps. TOML validated successfully.

**Deviations from spec**: none
