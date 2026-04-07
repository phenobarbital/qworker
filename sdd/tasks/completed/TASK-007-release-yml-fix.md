# TASK-007: Simplify release.yml for Pure-Python Wheels

**Feature**: uv-migration
**Spec**: `sdd/specs/uv-migration.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-004
**Assigned-to**: unassigned

---

## Context

The current `release.yml` builds manylinux Cython wheels across multiple Python versions.
Since QWorker is now pure Python, the workflow can be drastically simplified to build a single
universal wheel + sdist and publish to PyPI.

Implements: Spec Module 7.

---

## Scope

- Rewrite `.github/workflows/release.yml` to:
  - Trigger on release created (keep existing trigger)
  - Use a single job (no matrix needed for pure-Python)
  - Install `uv`
  - Run `uv build` to produce sdist + universal wheel
  - Publish to PyPI using `uv publish` or `twine`
  - Keep using `secrets.QUEUEWORKER_TOKEN` for PyPI auth
- Remove manylinux wheel building (`RalfG/python-wheels-manylinux-build`)
- Remove cibuildwheel Windows build
- Remove the multi-artifact download/merge logic

**NOT in scope**: modifying pyproject.toml (TASK-004), Makefile (TASK-006), or any source code.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `.github/workflows/release.yml` | MODIFY (full rewrite) | Simplified pure-Python release |

---

## Codebase Contract (Anti-Hallucination)

### Current release.yml structure (lines 1-98)
```yaml
# Trigger: on release created
# Job "build": matrix [ubuntu-latest] x [3.9, 3.10, 3.11, 3.12]
#   - Uses RalfG/python-wheels-manylinux-build for manylinux wheels
#   - Uses cibuildwheel for Windows (conditional)
#   - Uploads wheel artifacts
# Job "deploy": needs build
#   - Downloads all artifacts
#   - Publishes manylinux wheels via twine
#   - Publishes Windows wheels via twine
#   - Uses secrets.QUEUEWORKER_TOKEN
```

### PyPI Secret
```
secrets.QUEUEWORKER_TOKEN  # MUST remain configured in GitHub repo settings
```

### Does NOT Exist
- ~~`.github/workflows/ci.yml`~~ — no separate CI workflow (only release.yml)
- ~~`secrets.PYPI_TOKEN`~~ — the secret is named `QUEUEWORKER_TOKEN`

---

## Implementation Notes

### Pattern to Follow
```yaml
name: Python package build and publish

on:
  release:
    types: [created]

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Build package
        run: uv build

      - name: Publish to PyPI
        env:
          UV_PUBLISH_TOKEN: ${{ secrets.QUEUEWORKER_TOKEN }}
        run: uv publish
```

### Key Constraints
- Keep the workflow name the same: "Python package build and publish"
- Keep the trigger: `on: release: types: [created]`
- Use `astral-sh/setup-uv@v4` to install uv in CI
- Use `uv publish` with `UV_PUBLISH_TOKEN` env var (cleaner than twine)
- No matrix needed — pure-Python wheel is version-agnostic
- A single `actions/setup-python` with 3.11 is sufficient

---

## Acceptance Criteria

- [ ] `.github/workflows/release.yml` builds a single pure-Python wheel + sdist
- [ ] No references to manylinux, cibuildwheel, or Cython
- [ ] Uses `uv build` and `uv publish`
- [ ] Uses `secrets.QUEUEWORKER_TOKEN` for PyPI auth
- [ ] Single job, no matrix (no multi-Python build)
- [ ] Workflow is valid YAML

---

## Agent Instructions

When you pick up this task:

1. **Read** current `.github/workflows/release.yml`
2. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
3. **Rewrite** the workflow following the pattern above
4. **Validate** YAML: `python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"`
5. **Move this file** to `sdd/tasks/completed/TASK-007-release-yml-fix.md`
6. **Update index** → `"done"`

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-08
**Notes**: Rewrote `.github/workflows/release.yml` to a single `build-and-publish` job using `astral-sh/setup-uv@v4`, `uv build`, and `uv publish` with `UV_PUBLISH_TOKEN: ${{ secrets.QUEUEWORKER_TOKEN }}`. Removed manylinux matrix, cibuildwheel, Windows build, and multi-artifact download logic. YAML validated successfully.

**Deviations from spec**: none
