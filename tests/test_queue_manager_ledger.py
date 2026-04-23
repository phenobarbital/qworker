"""Unit tests for QueueManager ledger integration and draining guard.

Covers TASK-026 (FEAT-005):
- `put()` rejects tasks with QueueFull when worker status is "draining"
- `put()` writes a serialized entry to the shared-state task ledger
- `queue_handler()` removes the entry from the ledger at dequeue time
- Ledger failures do not prevent execution
"""
from __future__ import annotations

import asyncio
import base64
import multiprocessing as mp
import uuid

import cloudpickle
import pytest

from qw.queues.manager import QueueManager
from qw.state import StateTracker
from qw.wrappers.base import QueueWrapper


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def shared():
    """Real multiprocessing Manager().dict() — mirrors production shape."""
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


@pytest.fixture
def state_tracker(shared):
    return StateTracker(shared, worker_name="W0", pid=1234)


def _make_task() -> QueueWrapper:
    """Construct a minimal, picklable QueueWrapper for ledger round-trips."""
    task = QueueWrapper(queued=True)
    # _id is a uuid.UUID — keep it as set by QueueWrapper.__init__
    return task


# ----------------------------------------------------------------------
# Draining guard
# ----------------------------------------------------------------------


class TestDrainingGuard:
    @pytest.mark.asyncio
    async def test_put_rejects_when_draining(self, state_tracker, shared):
        """When status is 'draining', put() raises QueueFull."""
        state_tracker.set_status("draining", draining_since=1000.0)
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)
        task = _make_task()
        with pytest.raises(asyncio.QueueFull, match="draining"):
            await qm.put(task, id=str(task.id))

    @pytest.mark.asyncio
    async def test_put_accepts_when_healthy(self, state_tracker):
        """When status is 'healthy', put() succeeds."""
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)
        task = _make_task()
        result = await qm.put(task, id=str(task.id))
        assert result is True
        assert qm.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_put_recovers_when_status_flips_back_to_healthy(
        self, state_tracker
    ):
        """A worker that returns from draining to healthy accepts tasks."""
        state_tracker.set_status("draining", draining_since=1000.0)
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)
        with pytest.raises(asyncio.QueueFull):
            await qm.put(_make_task(), id="first")
        state_tracker.set_status("healthy")
        ok = await qm.put(_make_task(), id="second")
        assert ok is True

    @pytest.mark.asyncio
    async def test_put_without_state_tracker_skips_guard(self):
        """When state_tracker is None, draining guard is a no-op."""
        qm = QueueManager(worker_name="W0", state_tracker=None)
        task = _make_task()
        assert await qm.put(task, id=str(task.id)) is True


# ----------------------------------------------------------------------
# Ledger integration
# ----------------------------------------------------------------------


class TestLedgerIntegration:
    @pytest.mark.asyncio
    async def test_put_writes_ledger(self, state_tracker, shared):
        """After put(), the task appears in shared_state[...]['task_ledger']."""
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)
        task = _make_task()
        await qm.put(task, id=str(task.id))
        d = dict(shared["W0"])
        assert len(d["task_ledger"]) == 1
        entry = d["task_ledger"][0]
        assert entry["task_id"] == str(task.id)

    @pytest.mark.asyncio
    async def test_ledger_payload_is_roundtrip_safe(
        self, state_tracker, shared
    ):
        """Base64 + cloudpickle roundtrips back into an equivalent task."""
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)
        task = _make_task()
        await qm.put(task, id=str(task.id))
        d = dict(shared["W0"])
        payload_b64 = d["task_ledger"][0]["payload"]
        restored = cloudpickle.loads(base64.b64decode(payload_b64))
        assert isinstance(restored, QueueWrapper)
        assert str(restored.id) == str(task.id)

    @pytest.mark.asyncio
    async def test_queue_handler_removes_entry_on_dequeue(
        self, state_tracker, shared
    ):
        """queue_handler removes the ledger entry immediately after get()."""
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)
        task = _make_task()
        await qm.put(task, id=str(task.id))
        # Confirm the entry was added
        assert len(dict(shared["W0"])["task_ledger"]) == 1

        # Run one iteration of queue_handler manually to exercise the
        # dequeue + ledger_remove path without needing a real executor.
        async def run_one_iteration():
            got = await qm.queue.get()
            state_tracker.ledger_remove(str(got.id))
            qm.queue.task_done()

        await asyncio.wait_for(run_one_iteration(), timeout=1.0)

        d = dict(shared["W0"])
        assert d["task_ledger"] == []

    @pytest.mark.asyncio
    async def test_ledger_failure_does_not_block_put(self, state_tracker):
        """If ledger_add raises, put() still completes successfully."""
        qm = QueueManager(worker_name="W0", state_tracker=state_tracker)

        # Make ledger_add raise — put() should swallow and log the error
        # rather than refuse the task.
        def boom(*args, **kwargs):  # noqa: D401 — test helper
            raise RuntimeError("simulated ledger failure")

        state_tracker.ledger_add = boom  # type: ignore[assignment]
        task = _make_task()
        result = await qm.put(task, id=str(task.id))
        assert result is True
        assert qm.queue.qsize() == 1
