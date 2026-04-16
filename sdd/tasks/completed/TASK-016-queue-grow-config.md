# TASK-016: Add dynamic-queue-sizing config constants

**Feature**: dynamic-queue-sizing
**Spec**: `sdd/specs/dynamic-queue-sizing.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements Module 3 of the spec. The `QueueSizePolicy` (TASK-017) and the
`QueueManager` changes (TASK-018) need three new env-backed settings:
`WORKER_QUEUE_GROW_MARGIN`, `WORKER_QUEUE_WARN_THRESHOLD`, and
`WORKER_QUEUE_SHRINK_COOLDOWN`. All other work in this feature depends on
these constants existing, so it lands first.

Defaults are chosen so that upgrading to this feature with no env changes
still produces an improvement (margin=2, warn at 80 %, shrink after 30 s)
while `WORKER_QUEUE_GROW_MARGIN=0` restores today's byte-identical behaviour.

---

## Scope

- Add three config entries to `qw/conf.py` near the existing `WORKER_QUEUE_SIZE`:
  - `WORKER_QUEUE_GROW_MARGIN: int` — env `WORKER_QUEUE_GROW_MARGIN`, fallback `2`.
  - `WORKER_QUEUE_WARN_THRESHOLD: float` — env `WORKER_QUEUE_WARN_THRESHOLD`,
    fallback `0.80`.
  - `WORKER_QUEUE_SHRINK_COOLDOWN: int` — env `WORKER_QUEUE_SHRINK_COOLDOWN`,
    fallback `30` (seconds).
- Keep the existing `WORKER_QUEUE_SIZE` untouched (still the base size).
- Group the three new settings under a short section comment
  (`### Dynamic Queue Sizing`) for readability.

**NOT in scope**:
- Wiring these values into `QueueManager` / `QueueSizePolicy` (TASK-017, TASK-018).
- Validation / clamping of out-of-range values (policy enforces invariants).
- Documentation updates beyond the in-file comment.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/conf.py` | MODIFY | Add three new settings |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Already present in qw/conf.py — reuse, do not re-import:
from navconfig import config, ENVIRONMENT, ENV  # qw/conf.py:1
```

### Existing Signatures to Use
```python
# qw/conf.py  (verified on 2026-04-16)
# Line 1:  from navconfig import config, ENVIRONMENT, ENV
# Line 19: WORKER_QUEUE_SIZE = config.getint('WORKER_QUEUE_SIZE', fallback=4)
# Line 22: WORKER_RETRY_INTERVAL = config.getint('WORKER_RETRY_INTERVAL', fallback=10)
# config.getint(name, fallback=<int>)   → int
# config.getfloat(name, fallback=<float>) → float   (supported by navconfig)
# config.get(name, fallback=<str>)      → str
```

### Does NOT Exist
- ~~`config.getpercent()`~~ — no such helper. Use `config.getfloat`.
- ~~`WORKER_QUEUE_GROW_MARGIN` / `WORKER_QUEUE_WARN_THRESHOLD` /
  `WORKER_QUEUE_SHRINK_COOLDOWN`~~ — do not exist yet; this task adds them.
- ~~A separate `qw/queues/conf.py`~~ — do not create. All worker settings live
  in `qw/conf.py`.

---

## Implementation Notes

### Pattern to Follow
```python
# qw/conf.py — follow the style already used at lines 12-25
### Dynamic Queue Sizing
WORKER_QUEUE_GROW_MARGIN = config.getint(
    'WORKER_QUEUE_GROW_MARGIN', fallback=2
)
WORKER_QUEUE_WARN_THRESHOLD = config.getfloat(
    'WORKER_QUEUE_WARN_THRESHOLD', fallback=0.80
)
WORKER_QUEUE_SHRINK_COOLDOWN = config.getint(
    'WORKER_QUEUE_SHRINK_COOLDOWN', fallback=30
)
```

### Key Constraints
- Place the new block immediately after `WORKER_QUEUE_SIZE` (line 19) so
  related settings stay together.
- Do not alter `WORKER_QUEUE_SIZE`'s default (still `4`).
- Match existing quoting style in `qw/conf.py` (single quotes for env names).

---

## Acceptance Criteria

- [ ] `from qw.conf import WORKER_QUEUE_GROW_MARGIN, WORKER_QUEUE_WARN_THRESHOLD, WORKER_QUEUE_SHRINK_COOLDOWN` succeeds.
- [ ] With no env vars set: values are `2`, `0.80`, `30`.
- [ ] Setting `WORKER_QUEUE_GROW_MARGIN=5` in the environment yields `5`.
- [ ] `WORKER_QUEUE_SIZE` is still reachable and unchanged.
- [ ] `python -c "import qw.conf"` runs with zero warnings.

---

## Test Specification

No dedicated test module — this task is config-only. Verification is done by
importing the module (acceptance criteria above) and letting downstream tasks
(TASK-017 onwards) exercise the values.

---

## Agent Instructions

When you pick up this task:

1. Read the spec at `sdd/specs/dynamic-queue-sizing.spec.md` §2, §3 (Module 3),
   and §7.
2. Re-read `qw/conf.py` to confirm line 19 still holds `WORKER_QUEUE_SIZE`.
3. Add the three settings as shown in the pattern above.
4. Run `python -c "from qw.conf import WORKER_QUEUE_GROW_MARGIN,
   WORKER_QUEUE_WARN_THRESHOLD, WORKER_QUEUE_SHRINK_COOLDOWN; print(WORKER_QUEUE_GROW_MARGIN)"`.
5. Move this file to `sdd/tasks/completed/TASK-016-queue-grow-config.md`
   and update the index.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6
**Date**: 2026-04-16
**Notes**: Added 3 constants after WORKER_QUEUE_SIZE. Used float() cast instead of config.getfloat() as navconfig does not expose that method.

**Deviations from spec**: Used float(config.get(...)) instead of config.getfloat() since navconfig does not implement getfloat.
