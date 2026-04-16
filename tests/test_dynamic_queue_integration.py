"""Integration tests for dynamic queue sizing (TASK-019).

Tests cover the three integration rows from spec §4.
"""
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


@pytest.mark.asyncio
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

    async def test_health_readiness_fields_present(self, grown_qm):
        """The /health/ready body must contain all required fields."""
        hs = HealthServer(queue=grown_qm, worker_name="w")
        status, body = hs._readiness()
        payload = json_decoder(body)
        q = payload["queue"]
        required_keys = {"size", "max_size", "base_size", "grow_margin",
                         "grow_events", "discard_events", "full"}
        assert required_keys.issubset(q.keys())
