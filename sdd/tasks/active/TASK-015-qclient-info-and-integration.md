# TASK-015: QClient.info() Method & Integration Tests

**Feature**: expose-queue-threads
**Spec**: `sdd/specs/expose-queue-threads.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-011, TASK-014
**Assigned-to**: unassigned

---

## Context

> This is the final task. It adds a programmatic `QClient.info()` method for querying worker
> state from Python code (not just CLI), and writes integration tests that verify the full
> pipeline: shared state → TCP info command → response parsing.
> Implements: Spec Module 7 (QClient.info()) + Spec Section 4 (Integration Tests).

---

## Scope

- Modify `qw/client.py`:
  - Add `async def info(self) -> dict` method to `QClient` class
  - Follows the exact same pattern as `health()` (line 560-589):
    encode `'info'` → `get_worker_connection()` → `sendto_worker()` → read response →
    decode with `orjson.loads()` (info returns JSON, not cloudpickle)
- Write integration tests in `tests/test_info_integration.py`:
  - Test TCP `info` command returns valid JSON with expected structure
  - Test `QClient.info()` returns dict with worker state
  - Test that a queued task appears in the info response
  - Test auth behavior: remote requires signature, localhost does not
  - Test dead worker detection (if PID no longer exists)

**NOT in scope**:
- CLI formatting (TASK-014)
- State tracking instrumentation (TASK-009 through TASK-013)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/client.py` | MODIFY | Add info() method |
| `tests/test_info_integration.py` | CREATE | Integration tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Already in qw/client.py:
import asyncio                        # line 1
import cloudpickle                    # line 16
import orjson                         # line 18
from qw.utils import make_signature   # line 21
from qw.exceptions import QWException # line 22
from qw.conf import (
    WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT,
    WORKER_SECRET_KEY, expected_message
)  # lines 27-37
```

### Existing Signatures to Use
```python
# qw/client.py:58
class QClient:
    def __init__(self, worker_list=None, timeout=5):  # line 72
        self._workers: list                            # line 81-88
        self._worker_list: itertools.cycle             # line 82
        self.timeout: int = 5                          # line 69

    async def get_worker_connection(self):             # exists, used by health
    async def sendto_worker(self, data, writer):       # exists, used by health
    async def close(self, writer):                     # exists, used by health

    async def health(self) -> dict:  # line 560 — PATTERN TO FOLLOW
        task = 'health'
        serialized_task = task.encode('utf-8')
        reader, writer = await self.get_worker_connection()
        await self.sendto_worker(serialized_task, writer=writer)
        try:
            while True:
                serialized_result = await reader.read(-1)
                if reader.at_eof():
                    break
        except Exception as err:
            raise QWException(str(err)) from err
        finally:
            await self.close(writer)
        task_result = None
        try:
            task_result = cloudpickle.loads(serialized_result)
        except EOFError:
            pass
        except Exception:
            task_result = orjson.loads(serialized_result)
        return task_result
```

### Does NOT Exist
- ~~`QClient.info()`~~ — does not exist yet; this task creates it
- ~~`QClient.get_info()`~~ — does not exist; use `info()` as the method name
- ~~`QClient._send_command()`~~ — no generic command method exists; each command
  (health, info) implements its own send/receive

---

## Implementation Notes

### Pattern to Follow
```python
# info() follows the exact same pattern as health():
async def info(self) -> dict:
    """Query worker state via TCP 'info' command.

    Returns:
        dict: Worker state including queued, executing, and completed tasks.
    """
    task = 'info'
    serialized_task = task.encode('utf-8')
    reader, writer = await self.get_worker_connection()
    await self.sendto_worker(serialized_task, writer=writer)
    try:
        while True:
            serialized_result = await reader.read(-1)
            if reader.at_eof():
                break
    except Exception as err:
        raise QWException(str(err)) from err
    finally:
        await self.close(writer)
    # info always returns JSON (not cloudpickle)
    try:
        return orjson.loads(serialized_result)
    except Exception as err:
        raise QWException(
            f"Error parsing info response: {err}"
        ) from err
```

### Key Constraints
- The `info` command returns JSON (via `json_encoder` + `response_keepalive`), NOT
  cloudpickle. So `info()` should decode with `orjson.loads()` directly, unlike `health()`
  which tries cloudpickle first
- The `sendto_worker` method sends just the raw bytes — for pre-auth commands like `info`,
  this sends the command name directly (no length prefix, no signature). This matches
  how `health` works
- Integration tests may need to start a real QWorker in a subprocess or use a test fixture.
  Consider using a lightweight approach: start a QWorker in a background task with a known
  port, run the test, then shut it down

### References in Codebase
- `qw/client.py:560-589` — health() method (exact pattern to follow)
- `qw/client.py:147-200` — validate_connection and get_worker_connection

---

## Acceptance Criteria

- [ ] `QClient.info()` method exists and returns a dict
- [ ] Response contains "worker" and "state" keys
- [ ] "state" contains "queue", "tcp_executing", "redis_executing", "broker_executing", "completed" lists
- [ ] `info()` correctly decodes JSON response (not cloudpickle)
- [ ] Integration test: info command against a running worker returns valid structure
- [ ] Integration test: queued task appears in info response
- [ ] All tests pass: `pytest tests/test_info_integration.py -v`

---

## Test Specification

```python
# tests/test_info_integration.py
import pytest
import asyncio
from qw.client import QClient


class TestQClientInfo:
    @pytest.mark.asyncio
    async def test_info_returns_dict(self):
        """QClient.info() returns a dict with expected structure."""
        # NOTE: requires a running QWorker on default host/port
        # or a test fixture that starts one
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        result = await client.info()
        assert isinstance(result, dict)
        assert "worker" in result
        assert "state" in result

    @pytest.mark.asyncio
    async def test_info_state_structure(self):
        """State contains all expected source lists."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        result = await client.info()
        state = result["state"]
        assert "queue" in state
        assert "tcp_executing" in state
        assert "redis_executing" in state
        assert "broker_executing" in state
        assert "completed" in state

    @pytest.mark.asyncio
    async def test_info_worker_metadata(self):
        """Worker section contains name and pid."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        result = await client.info()
        worker = result["worker"]
        assert "name" in worker
        assert "pid" in worker
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/expose-queue-threads.spec.md` for full context
2. **Check dependencies**:
   - TASK-011 completed: TCP `info` command exists in QWorker
   - TASK-014 completed: CLI `qw info` works (confirms TCP command is functional)
3. **Verify the Codebase Contract**:
   - Read `qw/client.py:560-589` — confirm health() pattern still matches
   - Read `qw/server.py` — confirm `info` TCP command exists (from TASK-011)
4. **Update status** in `tasks/.index.json` → `"in-progress"`
5. **Implement** QClient.info() and integration tests
6. **Verify** all tests pass
7. **Move this file** to `tasks/completed/TASK-015-qclient-info-and-integration.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none | describe if any
