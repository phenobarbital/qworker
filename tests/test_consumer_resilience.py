"""Unit tests for Consumer Resilience (TASK-023).

Tests cover consumer loop surviving task exceptions, callback errors,
and the monitor respawning dead consumers.
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
    # Use a small queue size to keep tests fast
    manager = QueueManager(worker_name="test_worker")
    manager._monitor_interval = 0.1  # speed up monitor loops for testing
    return manager

@pytest.mark.asyncio
class TestConsumerResilience:
    async def test_consumer_survives_task_exception(self, qm):
        t1 = FuncWrapper(host="local", func=failing_func)
        t2 = FuncWrapper(host="local", func=succeed_func)
        
        await qm.put(t1, id="t1")
        await qm.put(t2, id="t2")
        
        await qm.fire_consumers()
        
        # Wait for queue to process both
        await asyncio.wait_for(qm.queue.join(), timeout=2.0)
        
        assert qm.queue.empty()
        await qm.empty_queue()

    async def test_consumer_survives_callback_error(self, qm):
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
        await qm.fire_consumers()
        await asyncio.sleep(0.05)
        
        # Kill a consumer manually
        # consumers includes queue_handler tasks, _shrink_task, _monitor_task
        consumer_task = None
        for t in qm.consumers:
            if t is not qm._monitor_task and t is not qm._shrink_task:
                consumer_task = t
                break
        
        assert consumer_task is not None
        consumer_task.cancel()
        
        # Wait for monitor to detect and respawn
        await asyncio.sleep(0.3)
        
        snap = qm.snapshot()
        assert snap["respawn_events"] > 0
        assert snap["consumer_alive"] == snap["consumer_total"]
        
        await qm.empty_queue()

    async def test_snapshot_includes_consumer_health(self, qm):
        await qm.fire_consumers()
        snap = qm.snapshot()
        
        assert "consumer_alive" in snap
        assert "consumer_total" in snap
        assert "respawn_events" in snap
        
        assert snap["consumer_alive"] == snap["consumer_total"]
        assert snap["consumer_alive"] > 0
        
        await qm.empty_queue()
