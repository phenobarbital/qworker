# Flowtask SDD Workflow for Claude Code

## Overview

This document defines the **Spec-Driven Development (SDD)** methodology for Flowtask, optimized for Claude Code and Antigravity with multi-agent task distribution.

The key idea: specifications are the Single Source of Truth (SSOT). Claude Code agents
consume spec documents and produce **Task Artifacts** — discrete, self-contained files
in `sdd/tasks/active/` that can be independently picked up and executed by any Claude Code
agent in parallel.

> **Note**: This workflow is for Flowtask (the task orchestration framework).
> For AI-Parrot (the library consumed by Flowtask), see `sdd/WORKFLOW-parrot.md`.

---

## The SDD Lifecycle

```
                                 ┌─ /sdd-proposal → discuss → brainstorm ──────────┐
                                 │                                                   │
                                 ├─ /sdd-brainstorm → explore options ───────────────┤
                                 │                                                   │
                                 ├─ /sdd-spec → scaffold spec ───────────────────────┤
[Human] ────────────────────────┤                                           Feature Spec → [Planner] Tasks → [Executors] Code → [Reviewer] Validation
                                 │                                                   ↑              ↑                                       |
                                 ├─ /sdd-task → decomposes spec into tasks ──────────┘              └────────── Feedback Loop ──────────────┘
                                 │                                                                                                          |
                                 └────── /sdd-start → begin task implementation ────────────────────────────────────────────────────────────┘
```

### Phase 0 — Feature Proposal *(optional)*
Start here when the idea is not yet well-defined. Use `/sdd-proposal` to discuss
a feature in non-technical language. The agent walks through motivation, scope,
and impact with you, producing `sdd/proposals/<feature>.proposal.md`.

The proposal can then automatically scaffold a formal spec (Phase 1).

### Phase 1 — Feature Specification
Start here when you already know what you want to build. Use `/sdd-spec` to scaffold
`sdd/specs/<feature>.spec.md`, or accept one auto-generated from `/sdd-proposal`.

### Phase 2 — Task Generation (Claude Code Planner Agent)
Run `/sdd-task <spec-file>` to decompose the spec into Task Artifacts.

Each task is written to `sdd/tasks/active/TASK-<NNN>-<slug>.md`.
The index `sdd/tasks/.index.json` is updated with task metadata.

Tasks are designed to be:
- **Atomic** — completable independently
- **Bounded** — clear scope, no ambiguity
- **Testable** — every task includes its own test criteria
- **Assignable** — formatted so any Claude Code agent can start immediately

### Phase 3 — Task Execution (Claude Code Executor Agents)
Each executor agent picks up a task file:
```bash
# In a new Claude Code session:
claude "Read sdd/tasks/active/TASK-003-selenium-interface.md and implement it"
```

Tasks declare their dependencies, so agents know what must be done first.

### Phase 4 — Validation (Claude Code Reviewer Agent)
After execution, tasks move to `sdd/tasks/completed/`.
A reviewer agent validates against the Test Specification using `/sdd-codereview`.

---

## Branch Strategy

```
main ──────────────────────────── production (PR target)
  └── dev ─────────────────────── integration branch (SDD state lives here)
        ├── feat-001-new-comp ─── feature worktree
        ├── feat-002-parser ───── feature worktree
        └── feat-003-interface ── feature worktree
```

- **`main`**: Production branch. All PRs target `main`.
- **`dev`**: Integration branch. SDD state (index, task files) is committed here.
- **`feat-<ID>-<slug>`**: Feature branches created as worktrees from `dev`.

### `dev` Sync Policy (MANDATORY)

**`dev` must always be up to date with `main` before starting any new work.**

Before running `/sdd-task` or `/sdd-start`, sync `dev`:
```bash
git checkout dev
git pull origin main --rebase
```

**Why**: Worktrees branch from the current state of `dev`. If `dev` is stale, feature branches
start from an outdated base and will have conflicts when merging back to `main`.

The commands `/sdd-task` and `/sdd-start` enforce this automatically — they will sync `dev`
with `main` before proceeding and stop if there are conflicts.

---

## Flowtask Codebase Reference

When creating specs and tasks, reference these key patterns:

| Pattern | Example | Location |
|---------|---------|----------|
| Component base class | `AbstractFlow` (start, close, run) | `flowtask/components/abstract.py` |
| Task runner | `Task` class | `flowtask/tasks/task.py` |
| Abstract task | `AbstractTask` | `flowtask/tasks/abstract.py` |
| Interface pattern | Service wrappers (Selenium, Azure, Google) | `flowtask/interfaces/` |
| Database interfaces | DB connection abstractions | `flowtask/interfaces/databases/` |
| Dataframe handlers | Pandas/data processing | `flowtask/interfaces/dataframes/` |
| Parser pattern | YAML, JSON, TOML parsers (Cython-compiled) | `flowtask/parsers/` |
| Service pattern | Bot services, file services, task services | `flowtask/services/` |
| Hooks/Events | Event-driven notifications | `flowtask/events/`, `flowtask/hooks/` |
| YAML task definitions | Task programs | `tasks/programs/<client>/` |
| Models | Pydantic data models | `flowtask/models.py` |
| Utils | Shared utilities | `flowtask/utils/` |
| Tests | pytest-based tests | `tests/` |

### Common Component Types
- **Downloads** (get data from external sources): `DownloadFrom.py`, `DownloadFromS3.py`, `DownloadFromSFTP.py`, `DownloadFromSharepoint.py`, `DownloadFromIMAP.py`, `DownloadFromFTP.py`
- **Uploads** (send data to external destinations): `UploadTo.py`, `UploadToS3.py`, `UploadToSFTP.py`, `UploadToSharepoint.py`
- **Database operations**: `CopyToPg.py`, `CopyToBigQuery.py`, `CopyToMongoDB.py`, `TableOutput/`, `TableInput.py`, `QueryToPandas.py`, `QueryToInsert.py`, `QueryIterator.py`
- **Scrapers**: `Amazon.py`, `BestBuy.py`, `Walmart.py`, `CompanyScraper/`, `DispatchMe.py`
- **API integrations**: `Zoom.py`, `ZoomUs.py`, `DialPad.py`, `Workday/`
- **Data transformers**: `FilterRows.py`, `TransformRows/`, `TransposeRows.py`, `UniqueRows.py`
- **Row operations** (prefix `t`): `tFilter.py`, `tJoin.py`, `tMerge.py`, `tGroup.py`, `tPivot.py`, `tConcat.py`, `tMelt.py`, `tExplode.py`, `tOrder.py`, `tPluckCols.py`, `tUnnest.py`
- **File operations**: `FileOpen.py`, `FileCopy.py`, `Unzip.py`, `Uncompress.py`

---

## Task Artifact Format

Every task file (`sdd/tasks/active/TASK-<NNN>-<slug>.md`) follows this structure:

```markdown
# TASK-<NNN>: <Title>

**Feature**: <parent feature name>
**Feature ID**: FEAT-<NNN>
**Spec**: sdd/specs/<feature>.spec.md
**Status**: pending | in-progress | done
**Priority**: high | medium | low
**Effort**: S | M | L | XL
**Depends-on**: TASK-<X>, TASK-<Y>   (or "none")
**Assigned-to**: (agent session ID or "unassigned")

## Context
Brief explanation of why this task exists and how it fits the feature.

## Scope
Exactly what this task must implement. Be precise.

## Files to Create/Modify
- `flowtask/components/NewComponent.py` — new component
- `flowtask/interfaces/new_service.py` — service interface
- `tests/test_new_component.py` — unit tests

## Implementation Notes
Technical guidance for the agent: patterns to follow, existing code to reference,
gotchas, constraints.

## Reference Code
Existing patterns in the codebase the agent should follow:
- See `flowtask/components/abstract.py` for AbstractFlow pattern
- See `flowtask/components/CopyTo.py` for data mover pattern
- See `flowtask/interfaces/selenium_service.py` for browser automation pattern

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] All tests pass: `pytest tests/test_<module>.py -v`

## Test Specification
```python
# Minimal test scaffold the agent must make pass
def test_component_runs():
    ...

def test_component_handles_edge_case():
    ...
```

## Output
When complete, the agent must:
1. Commit code in the worktree
2. SDD state update happens on `dev` (index + task file move)
3. Add a brief completion note below

### Completion Note
(Agent fills this in when done)
```

---

## Task Index Schema (`sdd/tasks/.index.json`)

```json
{
  "tasks": [
    {
      "id": "TASK-001",
      "slug": "base-component-interface",
      "title": "Define base component interface",
      "feature_id": "FEAT-001",
      "feature": "feature-slug",
      "spec": "sdd/specs/feature-slug.spec.md",
      "status": "pending",
      "priority": "high",
      "effort": "M",
      "depends_on": [],
      "parallel": false,
      "parallelism_notes": "",
      "assigned_to": null,
      "started_at": null,
      "completed_at": null,
      "file": "sdd/tasks/active/TASK-001-base-component-interface.md"
    }
  ]
}
```

---

## Parallelism Rules

Claude Code agents can work in parallel when tasks have no shared dependencies:

```
TASK-001 (base interface)
    ├── TASK-002 (selenium handler)    ← parallel after 001
    ├── TASK-003 (database handler)    ← parallel after 001
    └── TASK-004 (api client)          ← parallel after 001
            └── TASK-005 (integration tests) ← waits for 002, 003, 004
```

A Claude Code agent should **never start a task** if its `depends_on` tasks
are not in `sdd/tasks/completed/`.

Tasks marked `parallel: true` in the index CAN run in separate worktrees simultaneously.

---

## Commands Reference

These commands are available as Claude Code slash commands (`.claude/commands/`):

| Command | Description |
|---|---|
| `/sdd-proposal` | Propose and discuss a feature idea before building a spec |
| `/sdd-brainstorm` | Explore multiple approaches with library references |
| `/sdd-spec` | Scaffold a new Feature Specification |
| `/sdd-task <spec.md>` | Decompose a spec into Task Artifacts |
| `/sdd-start <TASK-ID>` | Pick up and implement a task |
| `/sdd-status` | Show task index status summary |
| `/sdd-next` | Suggest next unblocked tasks to assign |
| `/sdd-codereview` | Code review a completed task |
| `/sdd-done` | Verify, push, and cleanup a feature |

---

## Quality Rules for Agents

1. **Never modify files outside the task scope** — respect boundaries
2. **Follow existing patterns** — reference code mentioned in the task
3. **Write tests first** — TDD approach per task
4. **Update the index** — always update `.index.json` on completion
5. **Small commits** — one task = one logical commit
6. **Ask via the spec** — if unclear, note the ambiguity in the completion note
   and let the Planner agent refine the spec for the next iteration
7. **async/await throughout** — no blocking I/O in async contexts
8. **Google-style docstrings** — all public functions and classes
9. **Type hints** — strict typing on all function signatures
10. **Pydantic models** — for all data structures
11. **pytest** — all tests use pytest, run with `pytest tests/ -v`

---

## Cross-Project Development (Flowtask + AI-Parrot)

Flowtask consumes AI-Parrot as a library. When a feature requires changes to both:

1. **Parrot changes first**: Use `sdd/WORKFLOW-parrot.md` patterns for any changes
   to the `parrot` library.
2. **Flowtask changes second**: Reference the parrot changes in the flowtask spec
   as external dependencies.
3. **Link specs**: In the flowtask spec's dependencies section, reference the
   parrot feature/task that must be completed first.
