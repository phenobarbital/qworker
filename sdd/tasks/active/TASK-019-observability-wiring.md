# TASK-019: Wire dynamic queue metrics into check_state + health + integration tests

**Feature**: dynamic-queue-sizing
**Spec**: `sdd/specs/dynamic-queue-sizing.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-018
**Assigned-to**: unassigned

---

## Context

Implements Module 4 of the spec. Now that `QueueManager.snapshot()` is
available (TASK-018), this task replaces the hardcoded
`WORKER_QUEUE_SIZE` values that appear in the TCP `check_state` response
and in the Kubernetes readiness endpoint, and writes the integration
tests that guarantee no regressions at the worker level.

This is also the task that brings FEAT-003 to feature-complete: once
merged, operators see dynamic `max_size`/`base_size`/`grow_margin` in
both `qw info` (via FEAT-002) and `/health/ready`.

---

## Scope

- Modify `QWorker.worker_check_state` (`qw/server.py:476-499`):
  - Replace the hardcoded `"max_size": WORKER_QUEUE_SIZE` at line 489 with
    values from `self.queue.snapshot()`.
  - The `"queue"` sub-dict must include: `size`, `max_size`, `base_size`,
    `grow_margin`, `ceiling`, `grow_events`, `discard_events`, `full`,
    `empty`, `consumers`.
  - Keep the outer response shape (`versions`, `workers`, `worker`, `queue`)
    unchanged for backward compatibility.
- Modify `HealthServer._readiness` (`qw/health.py:138-154`):
  - Extend the JSON body with `max_size`, `base_size`, `grow_margin`,
    `grow_events`, `discard_events`.
  - Readiness **must consider the ceiling, not the base**: return 200 as
    long as the queue is not at `ceiling` (i.e. `policy.full()` equivalent).
    Use `self._queue.full()` — it already reflects `queue._maxsize` which
    is dynamic after TASK-018.
- Remove the now-unused `WORKER_QUEUE_SIZE` import from `qw/server.py:38`
  if no other callsite needs it (grep to confirm). If anything else uses
  it, leave the import.
- Write `tests/test_dynamic_queue_integration.py` covering the three
  integration-test rows from spec §4.
- No changes to `StateTracker` unless needed for tests — the optional
  counters it mentions in spec §2 are already available on
  `QueueManager.snapshot()`, so we prefer reading from there.

**NOT in scope**:
- CLI-side changes to `qw info` — FEAT-002's existing rendering already
  consumes `check_state`; the new fields will appear automatically.
- Changes to documentation files (`docs/`) — spec §5 does not list docs as
  acceptance criteria.
- Adding Prometheus metrics or any other new export surface.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/server.py` | MODIFY | Replace hardcoded queue max_size in `worker_check_state` |
| `qw/health.py` | MODIFY | Extend `_readiness` body; keep liveness untouched |
| `tests/test_dynamic_queue_integration.py` | CREATE | §4 integration tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# qw/server.py  (verified 2026-04-16)
from .conf import (
    WORKER_DEFAULT_HOST,         # line 29
    WORKER_DEFAULT_PORT,         # line 30
    WORKER_DEFAULT_QTY,          # line 31
    expected_message,            # line 32
    WORKER_SECRET_KEY,           # line 33
    REDIS_WORKER_STREAM,         # line 34
    REDIS_WORKER_GROUP,          # line 35
    WORKER_USE_STREAMS,          # line 36
    WORKER_REDIS,                # line 37
    WORKER_QUEUE_SIZE,           # line 38  ← may be removable after this task
    WORKER_HEALTH_ENABLED,       # line 39
    WORKER_HEALTH_PORT,          # line 40
)
from .state import StateTracker                  # line 44
from .queues import QueueManager                 # line 45

# qw/health.py  (verified 2026-04-16)
from .queues import QueueManager                 # line 17
from datamodel.parsers.json import json_encoder  # line 18
```

### Existing Signatures to Use
```python
# qw/server.py:476-499  (verified 2026-04-16, reproduced for clarity)
async def worker_check_state(self, writer: asyncio.StreamWriter):
    ## TODO: add last executed task
    addrs = ', '.join(str(sock.getsockname()) for sock in self._server.sockets)
    status = {
        "versions": get_versions(),
        "workers": WORKER_DEFAULT_QTY,
        "worker": {
            "name": self.name,
            "address": self.server_address,
            "serving": addrs,
            "redis": WORKER_REDIS
        },
        "queue": {
            "max_size": WORKER_QUEUE_SIZE,                # line 489  ← REPLACE
            "size": self.queue.size(),                    # line 490
            "full": self.queue.full(),                    # line 491
            "empty": self.queue.empty(),                  # line 492
            "consumers": len(self.queue.consumers)        # line 493
        },
    }
    await self.response_keepalive(status=status, writer=writer)
```

```python
# qw/health.py:138-154  (verified 2026-04-16)
def _readiness(self) -> tuple[str, str]:
    queue_full = self._queue.full()                       # line 140
    queue_size = self._queue.size()                       # line 141
    body = json_encoder({
        "status": "full" if queue_full else "ok",
        "queue": {
            "size": queue_size,                           # line 146
            "full": queue_full,                           # line 147
        },
        "worker": self._worker_name,
    })
    if queue_full:
        return HTTP_503, body                             # line 153
    return HTTP_200, body                                 # line 154
```

```python
# qw/queues/manager.py  (after TASK-018 — verify before coding)
# Public accessors introduced by TASK-018:
@property
def max_size(self) -> int: ...
@property
def base_size(self) -> int: ...
@property
def grow_margin(self) -> int: ...
def snapshot(self) -> dict:
    # keys: size, max_size, base_size, grow_margin, ceiling,
    #       grow_events, discard_events, full
```

### Does NOT Exist
- ~~`QWorker.queue_info()`~~ — call `self.queue.snapshot()` directly.
- ~~`HealthServer.get_snapshot()`~~ — use `self._queue.snapshot()`.
- ~~`WORKER_QUEUE_MAXSIZE`~~ — config has no such name; use
  `WORKER_QUEUE_SIZE` (base) and the runtime `max_size` from snapshot.
- ~~A separate `/health/queue` endpoint~~ — reuse `/health/ready`.
- ~~`StateTracker.record_queue_grow()`~~ — not needed; policy counters
  already live on `QueueManager._policy` and surface via `snapshot()`.

---

## Implementation Notes

### Pattern to Follow — server.py
```python
# qw/server.py
async def worker_check_state(self, writer: asyncio.StreamWriter):
    addrs = ', '.join(str(sock.getsockname()) for sock in self._server.sockets)
    snap = self.queue.snapshot()
    status = {
        "versions": get_versions(),
        "workers": WORKER_DEFAULT_QTY,
        "worker": {
            "name": self.name,
            "address": self.server_address,
            "serving": addrs,
            "redis": WORKER_REDIS,
        },
        "queue": {
            **snap,                                   # adds 8 keys from snapshot
            "empty": self.queue.empty(),              # keep existing key
            "consumers": len(self.queue.consumers),   # keep existing key
        },
    }
    await self.response_keepalive(status=status, writer=writer)
```

### Pattern to Follow — health.py
```python
# qw/health.py
def _readiness(self) -> tuple[str, str]:
    snap = self._queue.snapshot()
    queue_full = snap["full"]  # reflects dynamic max_size
    body = json_encoder({
        "status": "full" if queue_full else "ok",
        "queue": {
            "size": snap["size"],
            "max_size": snap["max_size"],
            "base_size": snap["base_size"],
            "grow_margin": snap["grow_margin"],
            "grow_events": snap["grow_events"],
            "discard_events": snap["discard_events"],
            "full": queue_full,
        },
        "worker": self._worker_name,
    })
    return (HTTP_503 if queue_full else HTTP_200), body
```

### Key Constraints
- The TCP `check_state` response is consumed by external monitoring code
  (FEAT-002's `qw info`, potentially external dashboards). **Never remove
  an existing key** — only add. `size`, `full`, `empty`, `consumers` must
  remain present.
- Readiness currently considers `full()` true only when `qsize ==
  _maxsize`. After TASK-018, `_maxsize` is dynamic, so `full()` naturally
  reports 503 only when the ceiling is reached — no extra logic needed.
- Keep the `/health/live` endpoint byte-identical.

### References in Codebase
- `qw/server.py:420-461` — `worker_health` and `worker_check_state`
  response shapes (follow the same style).
- `qw/health.py:138-162` — the only two handlers in `HealthServer`.

---

## Acceptance Criteria

- [ ] `check_state` TCP response includes `base_size`, `grow_margin`,
      `max_size` (dynamic), `ceiling`, `grow_events`, `discard_events` in
      the `"queue"` sub-dict — alongside the pre-existing `size`, `full`,
      `empty`, `consumers` keys.
- [ ] With a worker whose policy has grown the queue to `base+1`, the
      `check_state` response shows `max_size = base + 1`, `grow_events >= 1`.
- [ ] `/health/ready` body includes `max_size`, `base_size`, `grow_margin`,
      `grow_events`, `discard_events`.
- [ ] `/health/ready` returns 200 when the queue is at `base_size` (with
      grow margin available) and 503 only at `ceiling`.
- [ ] All integration tests in `tests/test_dynamic_queue_integration.py`
      pass.
- [ ] `pytest tests/ -v` — full suite green.
- [ ] `ruff check qw/server.py qw/health.py` clean.

---

## Test Specification

```python
# tests/test_dynamic_queue_integration.py
import asyncio
import pytest

from qw.queues import QueueManager, QueueSizePolicy
from qw.health import HealthServer
from qw.wrappers import FuncWrapper
from datamodel.parsers.json import json_decoder


def _noop():
    return "ok"


def _task() -> FuncWrapper:
    return FuncWrapper(host="local", func=_noop)


@pytest.fixture
def grown_qm() -> QueueManager:
    qm = QueueManager(
        worker_name="w",
        policy=QueueSizePolicy(
            base_size=4, grow_margin=2, warn_ratio=0.8,
            shrink_cooldown=60.0, low_watermark_ratio=0.5,
        ),
    )
    return qm


class TestDynamicQueueIntegration:
    async def test_check_state_reports_dynamic_maxsize(self, grown_qm):
        # Grow the queue past base
        for _ in range(5):
            await grown_qm.put(_task(), id="x")
        snap = grown_qm.snapshot()
        assert snap["base_size"] == 4
        assert snap["grow_margin"] == 2
        assert snap["max_size"] == 5
        assert snap["grow_events"] == 1

    async def test_health_readiness_uses_ceiling(self, grown_qm):
        hs = HealthServer(queue=grown_qm, worker_name="w")
        # Fill to base (4) — should still be 200 because margin=2
        for _ in range(4):
            await grown_qm.put(_task(), id="x")
        status, body = hs._readiness()
        assert status.startswith("200")
        payload = json_decoder(body)
        assert payload["queue"]["max_size"] >= 4
        # Fill to ceiling (6) — now 503
        for _ in range(2):
            await grown_qm.put(_task(), id="x")
        status, body = hs._readiness()
        assert status.startswith("503")

    async def test_concurrent_put_never_exceeds_ceiling(self):
        qm = QueueManager(
            worker_name="w",
            policy=QueueSizePolicy(
                base_size=4, grow_margin=2, warn_ratio=0.8,
                shrink_cooldown=60.0, low_watermark_ratio=0.5,
            ),
        )
        async def _do_put():
            try:
                await qm.put(_task(), id="x")
                return True
            except asyncio.QueueFull:
                return False
        results = await asyncio.gather(*[_do_put() for _ in range(20)])
        assert sum(1 for r in results if r) == 6   # base + margin
        assert qm.max_size == 6                    # never exceeded ceiling
```

---

## Agent Instructions

When you pick up this task:

1. Confirm TASK-018 is done (check `sdd/tasks/completed/`) — the accessors
   `QueueManager.snapshot()`, `.max_size`, `.base_size`, `.grow_margin`
   must exist.
2. Re-read `qw/server.py:476-499` and `qw/health.py:138-162` to confirm
   line numbers before editing.
3. Apply the patterns above. Keep the response shape additive — never
   drop keys.
4. Run `source .venv/bin/activate && pytest tests/ -v`. The full suite
   (including FEAT-002 tests at `tests/test_info_integration.py`) must
   still pass, since the `check_state` response shape is only additive.
5. After all criteria pass, move this file to
   `sdd/tasks/completed/TASK-019-observability-wiring.md` and update
   `.index.json`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
