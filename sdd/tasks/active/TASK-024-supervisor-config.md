# TASK-024: Add Supervisor Configuration Constants

**Feature**: process-supervisor
**Spec**: `sdd/specs/process-supervisor.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> Implements Module 1 from the spec. All other FEAT-005 tasks depend on these
> configuration constants. This is the foundation task that must land first.

---

## Scope

- Add 5 new configuration constants to `qw/conf.py`:
  - `WORKER_HEARTBEAT_INTERVAL` (int, default 5) — seconds between worker heartbeats
  - `WORKER_HEARTBEAT_TIMEOUT` (int, default 30) — seconds before a stale heartbeat triggers draining
  - `WORKER_DRAIN_TIMEOUT` (int, default 300) — seconds in draining state before force-kill
  - `SUPERVISOR_CHECK_INTERVAL` (int, default 10) — seconds between supervisor check cycles
  - `SUPERVISOR_KILL_GRACE` (int, default 10) — seconds to wait after SIGTERM before SIGKILL
- Add the new constants to the import list in `qw/server.py` (they will be used by later tasks)

**NOT in scope**:
- Any supervisor logic (TASK-028)
- Any heartbeat logic (TASK-027)
- Any state tracker changes (TASK-025)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/conf.py` | MODIFY | Add 5 new config constants after existing queue config section |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navconfig import config, ENVIRONMENT, ENV  # verified: qw/conf.py:1
```

### Existing Signatures to Use
```python
# qw/conf.py — existing pattern for config constants:
WORKER_QUEUE_SIZE = config.getint('WORKER_QUEUE_SIZE', fallback=4)          # line 19
WORKER_QUEUE_GROW_MARGIN = config.getint('WORKER_QUEUE_GROW_MARGIN', fallback=2)  # line 22-24
WORKER_CONSUMER_MONITOR_INTERVAL = config.getint('WORKER_CONSUMER_MONITOR_INTERVAL', fallback=60)  # line 31-33
WORKER_HEALTH_ENABLED = config.getboolean('WORKER_HEALTH_ENABLED', fallback=True)  # line 43
WORKER_HEALTH_PORT = config.getint('WORKER_HEALTH_PORT', fallback=8080)    # line 44
```

### Does NOT Exist
- ~~`WORKER_HEARTBEAT_INTERVAL`~~ — does not exist yet; must be added
- ~~`WORKER_HEARTBEAT_TIMEOUT`~~ — does not exist yet; must be added
- ~~`WORKER_DRAIN_TIMEOUT`~~ — does not exist yet; must be added
- ~~`SUPERVISOR_CHECK_INTERVAL`~~ — does not exist yet; must be added
- ~~`SUPERVISOR_KILL_GRACE`~~ — does not exist yet; must be added

---

## Implementation Notes

### Pattern to Follow
```python
# Follow the exact pattern used by existing config constants in qw/conf.py:
WORKER_HEARTBEAT_INTERVAL = config.getint('WORKER_HEARTBEAT_INTERVAL', fallback=5)
WORKER_HEARTBEAT_TIMEOUT = config.getint('WORKER_HEARTBEAT_TIMEOUT', fallback=30)
WORKER_DRAIN_TIMEOUT = config.getint('WORKER_DRAIN_TIMEOUT', fallback=300)
SUPERVISOR_CHECK_INTERVAL = config.getint('SUPERVISOR_CHECK_INTERVAL', fallback=10)
SUPERVISOR_KILL_GRACE = config.getint('SUPERVISOR_KILL_GRACE', fallback=10)
```

### Key Constraints
- Place the new constants in a logical group after the existing health check constants (after line 44)
- Use a section comment `## Process Supervisor` to group them

---

## Acceptance Criteria

- [ ] All 5 constants are defined in `qw/conf.py`
- [ ] Each uses `config.getint()` with the specified default
- [ ] Constants can be imported: `from qw.conf import WORKER_HEARTBEAT_INTERVAL`
- [ ] Existing tests still pass: `pytest tests/ -v`

---

## Test Specification

No dedicated test file needed — these are simple config reads. Verified by import in later tasks.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/process-supervisor.spec.md` for full context
2. **Check dependencies** — none for this task
3. **Verify the Codebase Contract** — confirm `qw/conf.py` structure matches
4. **Update status** in `tasks/.index.json` to `"in-progress"`
5. **Implement** the 5 config constants
6. **Verify** all acceptance criteria are met
7. **Move this file** to `tasks/completed/TASK-024-supervisor-config.md`
8. **Update index** status to `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
