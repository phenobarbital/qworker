"""Unit tests for QueueManager dynamic sizing (TASK-018).

Tests cover all acceptance criteria rows from spec §4 Module 2.
"""
import asyncio
import pytest

from qw.queues import QueueManager, QueueSizePolicy
from qw.wrappers import FuncWrapper


def _noop():
    return "ok"


def _mk_task() -> FuncWrapper:
    return FuncWrapper(host="local", func=_noop)


@pytest.fixture
def policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4,
        grow_margin=2,
        warn_ratio=0.8,
        shrink_cooldown=0.2,
        low_watermark_ratio=0.5,
    )


@pytest.fixture
def zero_margin_policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4, grow_margin=0, warn_ratio=0.8,
        shrink_cooldown=0.2, low_watermark_ratio=0.5,
    )


@pytest.mark.asyncio
class TestQueueManagerDynamic:
    async def test_put_warns_at_threshold(self, policy, caplog):
        qm = QueueManager(worker_name="w", policy=policy)
        caplog.set_level("WARNING", logger="QW.Queue")
        # Fill to 80 %: need 4 items in queue before the 5th put check fires.
        # At the 5th put: size=4 before put → 4/4=1.0 >= 0.8 → warn fires.
        for _ in range(5):
            await qm.put(_mk_task(), id="x")
        assert any("near limit" in r.message for r in caplog.records)

    async def test_put_grows_on_full(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        for _ in range(6):
            await qm.put(_mk_task(), id="x")
        assert qm.max_size == 6  # grew from 4 to 6
        assert qm.snapshot()["grow_events"] == 2

    async def test_put_discards_at_ceiling(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        for _ in range(6):
            await qm.put(_mk_task(), id="x")
        with pytest.raises(asyncio.QueueFull):
            await qm.put(_mk_task(), id="x")
        assert qm.snapshot()["discard_events"] == 1

    async def test_backward_compat_margin_zero(self, zero_margin_policy):
        qm = QueueManager(worker_name="w", policy=zero_margin_policy)
        for _ in range(4):
            await qm.put(_mk_task(), id="x")
        with pytest.raises(asyncio.QueueFull):
            await qm.put(_mk_task(), id="x")
        assert qm.max_size == 4  # never grew

    async def test_snapshot_fields(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        snap = qm.snapshot()
        assert set(snap.keys()) == {
            "size", "max_size", "base_size", "grow_margin",
            "ceiling", "grow_events", "discard_events", "full",
        }

    async def test_shrinks_after_cooldown(self, policy):
        qm = QueueManager(worker_name="w", policy=policy)
        # Grow to max
        for _ in range(6):
            await qm.put(_mk_task(), id="x")
        assert qm.max_size == 6
        # Drain below low watermark
        while not qm.queue.empty():
            qm.queue.get_nowait()
            qm.queue.task_done()
        # Reset high-usage timestamp to far in the past so cooldown is met
        qm._policy.note_high_usage(now=0.0)
        # Start the shrink monitor (fire_consumers spawns it)
        await qm.fire_consumers()
        # Wait for one or more shrink ticks (interval = max(1, 0.2/5) = 1.0s)
        await asyncio.sleep(1.5)
        # Cleanup
        await qm.empty_queue()
        assert qm.max_size <= 5  # shrank at least once

    async def test_default_policy_from_config(self):
        """QueueManager() still works without explicit policy (backward compat)."""
        qm = QueueManager(worker_name="x")
        assert qm.base_size >= 1
        assert qm.max_size == qm.base_size
