"""Unit tests for Consumer Resilience (FEAT-004).

Covers:
  - Consumer loop surviving task exceptions, RuntimeErrors, and callback errors
  - Monitor detecting dead consumers and incrementing respawn count
  - snapshot() exposing consumer health fields
  - Integration: queue drains fully even when early tasks raise
"""
import asyncio
import pytest

from qw.queues.manager import QueueManager
from qw.wrappers import FuncWrapper


def failing_func():
    raise RuntimeError("This is a failure")


def succeed_func():
    return "success"


@pytest.fixture
def qm():
    """QueueManager with a fast monitor interval for test speed."""
    manager = QueueManager(worker_name="test_worker")
    manager._monitor_interval = 0.1  # speed up monitor loops for testing
    return manager


@pytest.mark.asyncio
class TestConsumerResilience:
    async def test_consumer_survives_task_exception(self, qm):
        """Consumer loop continues after a task raises RuntimeError."""
        t1 = FuncWrapper(host="local", func=failing_func)
        t2 = FuncWrapper(host="local", func=succeed_func)

        await qm.put(t1, id="t1")
        await qm.put(t2, id="t2")

        await qm.fire_consumers()

        # Both tasks must be consumed despite t1 raising
        await asyncio.wait_for(qm.queue.join(), timeout=2.0)

        assert qm.queue.empty()
        await qm.empty_queue()

    async def test_consumer_survives_runtime_error(self, qm):
        """Consumer loop continues when tasks raise generic RuntimeError."""
        def multi_fail():
            raise RuntimeError("persistent failure")

        tasks = [FuncWrapper(host="local", func=multi_fail) for _ in range(3)]
        t_ok = FuncWrapper(host="local", func=succeed_func)

        for i, t in enumerate(tasks):
            await qm.put(t, id=f"fail-{i}")
        await qm.put(t_ok, id="ok")

        await qm.fire_consumers()
        await asyncio.wait_for(qm.queue.join(), timeout=3.0)

        assert qm.queue.empty()
        await qm.empty_queue()

    async def test_consumer_survives_callback_error(self, qm):
        """Consumer loop continues when the done-callback raises."""
        async def bad_callback(task, result=None, **kwargs):
            raise ValueError("Callback failed")

        qm._callback = bad_callback

        t = FuncWrapper(host="local", func=succeed_func)
        await qm.put(t, id="t")

        await qm.fire_consumers()
        await asyncio.wait_for(qm.queue.join(), timeout=2.0)

        assert qm.queue.empty()
        await qm.empty_queue()

    async def test_monitor_detects_dead_consumer(self, qm):
        """Monitor replaces a manually cancelled consumer task."""
        await qm.fire_consumers()
        await asyncio.sleep(0.05)

        # Find a real queue_handler (not _shrink_task)
        consumer_task = next(
            (t for t in qm.consumers if t is not qm._shrink_task),
            None,
        )
        assert consumer_task is not None, "No queue_handler task found in consumers"
        consumer_task.cancel()

        # Wait for monitor tick + margin
        await asyncio.sleep(0.3)

        snap = qm.snapshot()
        assert snap["respawn_events"] > 0
        assert snap["consumer_alive"] == snap["consumer_total"]

        await qm.empty_queue()

    async def test_monitor_increments_respawn_count(self, qm):
        """Each dead consumer increments respawn_events independently."""
        await qm.fire_consumers()
        await asyncio.sleep(0.05)

        # Cancel every queue_handler task
        killed = [t for t in qm.consumers if t is not qm._shrink_task]
        for t in killed:
            t.cancel()

        # Allow monitor to detect and respawn all of them
        await asyncio.sleep(0.3)

        snap = qm.snapshot()
        assert snap["respawn_events"] >= len(killed), (
            f"Expected >= {len(killed)} respawn events, got {snap['respawn_events']}"
        )
        assert snap["consumer_alive"] == snap["consumer_total"]

        await qm.empty_queue()

    async def test_snapshot_includes_consumer_health(self, qm):
        """snapshot() contains consumer_alive, consumer_total, respawn_events."""
        await qm.fire_consumers()
        snap = qm.snapshot()

        assert "consumer_alive" in snap
        assert "consumer_total" in snap
        assert "respawn_events" in snap

        # All consumers alive at start; _shrink_task excluded from counts
        assert snap["consumer_alive"] == snap["consumer_total"]
        assert snap["consumer_alive"] > 0
        # base_size=4 → 3 queue_handlers (base_size - 1)
        assert snap["consumer_total"] == qm._policy.base_size - 1

        await qm.empty_queue()

    async def test_queue_drains_after_error(self, qm):
        """Integration: queue fully drains when the first task errors.

        Reproduces the production scenario: task #1 fails, tasks #2..N
        must still complete (not accumulate in 'queued' status forever).
        """
        results: list = []

        async def tracking_callback(task, result=None, **kwargs):
            results.append(result)

        qm._callback = tracking_callback

        n_tasks = 5
        tasks = [FuncWrapper(host="local", func=failing_func)] + [
            FuncWrapper(host="local", func=succeed_func)
            for _ in range(n_tasks - 1)
        ]
        for i, t in enumerate(tasks):
            await qm.put(t, id=str(i))

        await qm.fire_consumers()
        await asyncio.wait_for(qm.queue.join(), timeout=5.0)

        assert qm.queue.empty(), "Queue did not drain — consumers may have died"
        assert len(results) == n_tasks, (
            f"Expected {n_tasks} callbacks, got {len(results)}"
        )
        # First task failed, rest succeeded
        assert isinstance(results[0], BaseException)
        assert not any(isinstance(r, BaseException) for r in results[1:])

        await qm.empty_queue()
