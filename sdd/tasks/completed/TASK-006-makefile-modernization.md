# TASK-006: Rewrite Makefile with uv Commands

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-004
**Assigned-to**: unassigned

---

## Context

The current Makefile uses raw `pip` commands and references Cython/flit. It must be modernized
to use `uv` commands, following the patterns established in `ai-parrot/Makefile`.

Implements: Spec Module 6.

---

## Scope

- Rewrite `Makefile` with the following targets (adapted from ai-parrot):
  - `venv`: `uv venv --python 3.11 .venv`
  - `install`: `uv sync --frozen --no-dev`
  - `develop`: `uv sync` (includes dev deps)
  - `lock`: `uv lock`
  - `sync`: `uv sync`
  - `test`: `uv run pytest`
  - `format`: `uv run black qw`
  - `lint`: `uv run pylint --rcfile .pylintrc qw/*.py` + `uv run black --check qw`
  - `release`: `uv build` + `uv publish`
  - `clean`: remove `build/`, `dist/`, `*.egg-info/`, `__pycache__/`, `*.pyc`, `*.pyo` (no more `.so`)
  - `distclean`: remove `.venv` + `uv.lock`
  - `bump-patch`/`bump-minor`/`bump-major`: version management using `qw/version.py`
  - `add`/`add-dev`/`remove`: dependency management via `uv add`/`uv remove`
  - `update`: `uv lock --upgrade`
  - `info`: `uv tree`
  - `help`: list all targets
- Add `install-uv` target
- Remove all references to Cython, flit, or `pip`

**NOT in scope**: modifying pyproject.toml (TASK-004), release.yml (TASK-007), or MANIFEST.in (TASK-008).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `Makefile` | MODIFY (full rewrite) | uv-based build commands |

---

## Codebase Contract (Anti-Hallucination)

### Current Makefile (lines 1-33)
```makefile
venv:
	python3.11 -m venv .venv
install:
	pip install --upgrade navigator-session navigator-auth
	pip install --upgrade querysource
	pip install --upgrade flowtask
	pip install -e .
develop:
	pip install -e .
	pip install -Ur docs/requirements-dev.txt
	flit install --symlink
release:
	lint test clean
	flit publish
format:
	python -m black qw
lint:
	python -m pylint --rcfile .pylintrc qw/*.py
	python -m black --check qw
test:
	python -m coverage run -m qw.tests
	python -m coverage report
	python -m mypy qw/*.py
distclean:
	rm -rf .venv
```

### Reference: ai-parrot Makefile patterns
```makefile
PYTHON_VERSION := 3.11
export PIP_REQUIRE_VIRTUALENV=true
HAS_UV := $(shell command -v uv 2> /dev/null)

install-uv:
	curl -LsSf https://astral.sh/uv/install.sh | sh

venv:
	uv venv --python $(PYTHON_VERSION) .venv

install:
	uv sync --frozen --no-dev --all-packages

develop:
	uv sync --all-packages --all-extras

lock:
	uv lock

release: lint test clean
	uv build
	uv publish dist/*.tar.gz dist/*.whl

clean:
	rm -rf build/ dist/ *.egg-info/
	find . -name "*.pyc" -delete
	find . -type d -name __pycache__ -delete

bump-patch:
	$(call _bump,$(VERSION_FILE),2)
```

### Version file location
```python
# qw/version.py — VERSION_FILE for bump targets
__version__ = '1.13.2'  # line 9
```

### Does NOT Exist
- ~~`requirements.txt`~~ — no root requirements.txt (only in `docs/`)
- ~~`.pylintrc`~~ — referenced in current Makefile; verify if it exists before referencing

---

## Implementation Notes

### Key Constraints
- This is a single-package project (not a workspace), so no `--all-packages` or `--package` flags
- The `bump-*` targets should follow ai-parrot's `_bump` helper pattern using inline Python
- VERSION_FILE should be `qw/version.py`
- No references to Cython build steps (`build_ext`, `build_ext --inplace`)
- `.PHONY` should list all targets

---

## Acceptance Criteria

- [ ] `Makefile` uses `uv` for all package operations
- [ ] No references to `pip`, `flit`, `Cython`, or `build_ext`
- [ ] `make venv` creates a venv with `uv venv`
- [ ] `make develop` runs `uv sync`
- [ ] `make test` runs `uv run pytest`
- [ ] `make clean` removes build artifacts (no `.so` cleanup needed)
- [ ] `make bump-patch` increments version in `qw/version.py`
- [ ] `make help` lists all available targets

---

## Agent Instructions

When you pick up this task:

1. **Read** current `Makefile` and `ai-parrot/Makefile` for reference
2. **Check** if `.pylintrc` exists: `ls .pylintrc`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Rewrite** Makefile with uv-based targets
5. **Test**: `make help` should work
6. **Move this file** to `sdd/tasks/completed/TASK-006-makefile-modernization.md`
7. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-08
**Notes**: Rewrote Makefile with all uv-based targets: `install-uv`, `venv`, `install`, `develop`, `lock`, `sync`, `update`, `test`, `format`, `lint`, `release`, `clean`, `distclean`, `bump-patch`, `bump-minor`, `bump-major`, `add`, `add-dev`, `remove`, `info`, `help`. No references to pip, flit, or Cython. `.pylintrc` verified to exist. `make help` tested successfully.

**Deviations from spec**: none
