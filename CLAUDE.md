# AI-Parrot Development Guide for Claude

## Project

Async-first Python framework for AI Agents and Chatbots.
See @.agent/CONTEXT.md for full architectural context.

**Main Branch**: `main`

## Development Environment

### Package Management & Virtual Environment

**CRITICAL RULES:**
1. **Package Manager**: Use **`uv`** exclusively for package management
   ```bash
   uv pip install <package>
   uv pip list
   uv add <package>
   ```

2. **Virtual Environment**: ALWAYS activate before Python operations
   ```bash
   source .venv/bin/activate
   ```
   **NEVER** run `uv`, `python`, or `pip` commands without activating first.

3. **Dependencies**: Manage all dependencies via `pyprmodioject.toml`


## Tool-Centric Architecture

AI-Parrot's agents interact with the world through tools. When creating tools:

1. **Location**: Place all external API/service wrappers in `parrot/tools/`
2. **Decorator Pattern**: Use `@tool` for simple functions
   ```python
   from parrot.tools import tool

   @tool
   def get_weather(location: str) -> str:
       """Get the current weather for a location."""
       return f"Weather in {location}: Sunny, 25°C"
   ```

3. **Toolkit Pattern**: Use `AbstractToolkit` for complex tool collections
4. **Documentation**: Every tool MUST have clear docstrings explaining purpose, parameters, and return values

## Async-First Development

AI-Parrot is built on async/await patterns

## Integration Patterns

AI-Parrot supports multiple integration methods:

### 1. A2A (Agent-to-Agent)
Native protocol for agent discovery and communication

### 2. MCP (Model Context Protocol)
Expose agents as MCP servers or consume external MCP servers

### 3. OpenAPI Integration
Consume any OpenAPI spec as a dynamic toolkit using `OpenAPIToolkit`

## Non-Negotiable Rules

### Environment
- Package manager: `uv` exclusively (`uv add`, `uv pip install`)
- ALWAYS activate venv before any command: `source .venv/bin/activate`
- NEVER run python/uv/pip without activating first

### Code Standards
- All functions and classes: Google-style docstrings + strict type hints
- Pydantic models for all data structures
- async/await throughout — no blocking I/O in async contexts
- Logger (`self.logger`) instead of print statements

### Workflow: Think → Act → Reflect
1. For complex tasks: create plan in `artifacts/plan_[task_id].md` first
2. Implement incrementally
3. Run `pytest` after ANY logic change — no exceptions
4. Save evidence to `artifacts/logs/`

### Security
- Never commit API keys — use environment variables
- Never run `rm -rf` or system-level deletions
- No form submissions or logins without user approval

## Key References
- Architecture & patterns: @.agent/CONTEXT.md
- SDD workflow: @docs/sdd/WORKFLOW.md
- Skills: @.agent/skills/
- Workflows: @.agent/workflows/

# SDD Workflow & Worktree Policy

---

## Git Configuration

- **Integration branch**: `dev`
- **Production branch**: `main`
- **Worktrees branch from**: the CURRENT branch (`HEAD`), never hardcoded to `main`

## Worktree Creation

> **CRITICAL**: Do NOT use `claude --worktree`. It branches from the repo's default
> branch (`main`), which does not contain SDD artifacts.
>
> Always create worktrees manually from the current branch:

```bash
# Standard pattern: create worktree from current branch
git worktree add -b <branch-name> .claude/worktrees/<worktree-name> HEAD
```

### Quick reference

```bash
# From dev (most common)
git checkout dev
git worktree add -b feat-014-videoreel-visual-changes \
  .claude/worktrees/feat-014-videoreel-visual-changes HEAD

# From another feature branch (sub-features)
git checkout feat/ontology-rag
git worktree add -b feat-014-sub-task \
  .claude/worktrees/feat-014-sub-task HEAD

# Then launch Claude inside the worktree
cd .claude/worktrees/feat-014-videoreel-visual-changes
claude   # interactive, manual /sdd-start
# or
claude --agent sdd-worker --model sonnet --verbose
```

### Cleanup

```bash
# After PR merge
git worktree remove .claude/worktrees/<name>
# or prune all dead worktrees
git worktree prune
```

### .gitignore

```gitignore
.claude/worktrees/
```

## SDD Auto-Commit Rule

> **CRITICAL**: Every SDD command that creates or modifies files MUST commit them
> to the current branch before finishing. Uncommitted files are invisible to
> worktrees and other sessions.

| Command | What it commits |
|---------|----------------|
| `/sdd-brainstorm` | `sdd/proposals/<n>.brainstorm.md` |
| `/sdd-proposal` | `sdd/proposals/<n>.proposal.md` |
| `/sdd-spec` | `sdd/specs/<n>.spec.md` |
| `/sdd-task` | `sdd/tasks/active/TASK-*` + `sdd/tasks/.index.json` |
| `/sdd-start` | Index status update (`in-progress`) + implementation code |
| `/sdd-done` | Index status update (`done`) + task file moves |

Commit message convention:
```
sdd: <action> for <feature-name>
```

## Isolation Model

Worktrees isolate **features** from each other. Tasks within a feature run
sequentially in the same worktree via `/sdd-start TASK-<NNN>`.

```
Terminal 1 (in .claude/worktrees/feat-007):     Terminal 2 (in .claude/worktrees/feat-008):
  /sdd-start TASK-001 → commit                   /sdd-start TASK-010 → commit
  /sdd-start TASK-002 → commit (sees 001)         /sdd-start TASK-011 → commit
  /sdd-start TASK-003 → commit (sees 001+2)       /sdd-start TASK-012 → commit
  push, PR against dev                            push, PR against dev
```

## Typical Workflow

```bash
# 1. Ensure you're on dev with latest
git checkout dev && git pull origin dev

# 2. Create and approve a spec (committed to dev automatically)
/sdd-spec videoreel-visual-changes -- ...
/sdd-task sdd/specs/videoreel-visual-changes.spec.md

# 3. Create worktree from dev
git worktree add -b feat-014-videoreel-visual-changes \
  .claude/worktrees/feat-014 HEAD

# 4. Enter worktree and work
cd .claude/worktrees/feat-014

# Manual (task-by-task):
claude
/sdd-start TASK-069
/sdd-start TASK-070
/sdd-done FEAT-014

# Or autonomous:
claude --agent sdd-worker --dangerously-skip-permissions --model sonnet --verbose
/sdd-done FEAT-014

# 5. Push and PR
git push origin feat-014-videoreel-visual-changes
# Create PR against dev

# 6. Cleanup after merge
cd ~/proyectos/...   # back to main repo
git worktree remove .claude/worktrees/feat-014
```

## Autonomous Agent (`sdd-worker`)

The `sdd-worker` agent (`.claude/agents/sdd-worker.md`) implements all tasks for
a feature sequentially. Launch it **inside** a manually-created worktree:

```bash
cd .claude/worktrees/<feature-worktree>
claude --agent sdd-worker --model sonnet --verbose
```

Key properties: uses Sonnet, implements EXACTLY what tasks
specify (no redesigns), commits after each task.

For background execution:
```bash
cd .claude/worktrees/feat-014
tmux new -s feat-014 \
  "claude --agent sdd-worker --model sonnet --verbose"
# Ctrl+B, D to detach — tmux attach -t feat-014 to reconnect
```

## Task Index Schema

Both `feature_id` and `feature` must be present in `sdd/tasks/.index.json`:
```json
{
  "id": "TASK-<NNN>",
  "feature_id": "FEAT-<NNN>",
  "feature": "<feature-slug>"
}
```
Commands resolve features by matching either field (exact, numeric suffix, or substring).

### When NOT to Use Worktrees

- **Hotfixes on `main`**: Work directly on `main` or a short-lived `hotfix/*` branch.
- **Documentation-only changes**: No code conflicts possible, work on `develop` directly.
- **Single-task features**: If a spec has only one task, a worktree adds overhead
  with no benefit. Work directly on a feature branch.
- **Exploratory brainstorming**: `/sdd-brainstorm` doesn't produce code — no worktree needed.
- **Quick bug fixes**: If the fix is a single commit, skip the worktree ceremony.

<!-- parrot:wiki:begin -->
## Codebase Knowledge Graph (LLM Wiki)

This repository maintains a machine-first knowledge graph of the
codebase (pages + typed edges over a local SQLite plane, built by
`wikitoolkit build`). For ANY question about the codebase — where
something lives, how modules relate, what a subsystem does — you MUST
run a scoped wiki query FIRST, before Grep/Glob/Read or any shell
search (`grep`/`rg`/`find`/`cat` via Bash):

- `wikitoolkit query "<question>"` — token-budgeted, ranked page
  stubs for a scoped question. ALWAYS start here.
- `wikitoolkit page <id>` — read one page in full (file summaries,
  API outlines, content). Use the ids returned by `query`.
- `wikitoolkit related <id>` — follow typed edges (`contains`,
  `references`) to neighbouring files/modules.
- `wikitoolkit status` — plane statistics and staleness.
- `wikitoolkit build` — refresh the graph after large changes
  (a git post-commit hook may already keep it fresh).

These same operations are also exposed as native MCP tools —
`wiki_query`, `wiki_page`, `wiki_related`, `wiki_remember`, `wiki_note`,
`wiki_status` — via the `wikitoolkit` MCP stdio server registered in
this repo's `.mcp.json` (FEAT-403). If they appear in your tool list,
prefer calling them directly; they have equal standing with Grep/Read
at tool-selection time instead of competing via a Bash-invoked CLI.

**Query discipline** (avoids the two most common ways the wiki
"fails" — which are usually caller error, not missing coverage):

1. **Query for the *thing*, not for your *hypothesis* about it.** The
   ranking is lexical — extra concept words steer it toward those
   concepts. To locate a class or feature, name the symbol/module/
   subsystem you want (`"attestation model service"`), not your theory
   about where it might live.
2. **Follow the thread before falling back.** If a result scores low
   or names a parent module, resolve it with `wikitoolkit page <id>`
   or `wikitoolkit related <id>` — one hop usually lands the real
   page. Do NOT jump to grep just because the first `query` didn't
   rank the exact page first.

Only fall back to Grep/Glob/Read (or shell search) once a clean query
*and* a page/related follow-up have genuinely come up empty — and say
so before you do. Consider `wikitoolkit build` if results look stale.

**Saving knowledge (persistent memory).** The wiki is also your
durable memory — what you save here survives this session and is
found by future `wikitoolkit query` calls ("the agent forgets, the
graph does not"). When you learn a durable fact, make a decision, or
extract a lesson worth keeping, SAVE it:

- `wikitoolkit remember "<fact>" --category [note|decision|lesson|concept]
  [--title "<short title>"] [--link <page_id> --rel <relation>]` —
  file new knowledge (idempotent: same title+category updates the
  existing memory). Link it to the pages it is about.
- `wikitoolkit note <page_id> "<text>"` — append an attributed,
  dated note to an existing page.
- `wikitoolkit link <src_id> <dst_id> --rel <relation>` — connect
  two pages with a typed, asserted edge.
- `wikitoolkit memories` — list saved memories;
  `wikitoolkit audit` — the attributed write log.

Save selectively: durable decisions, gotchas, and cross-file
relationships — not session chatter. Every write is attributed and
auditable.

The `/parrotwiki` command wraps these (e.g. `/parrotwiki query how
does ingest work`, `/parrotwiki remember <fact>`, `/parrotwiki --wiki`
to export a human-readable markdown wiki).
<!-- parrot:wiki:end -->
