"""End-to-end integration tests for FEAT-005 (TASK-031).

These tests wire together the StateTracker, QueueManager,
ProcessSupervisor, and HealthServer modules built in TASK-024..TASK-030
and verify the full lifecycle: worker crash detection, task rescue,
draining rejection, drain-timeout kill, self-recovery, and health
reporting.

Process spawning is mocked so the tests can run quickly and
deterministically without binding TCP ports or requiring a live Redis
instance. A real `multiprocessing.Manager().dict()` is still used for
shared state so cross-process semantics (pickling, full-value
replacement) are exercised faithfully.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from qw.health import HTTP_200, HTTP_503, HealthServer
from qw.queues.manager import QueueManager
from qw.state import StateTracker
from qw.supervisor import ProcessSupervisor
from qw.wrappers.base import QueueWrapper


# ----------------------------------------------------------------------
# Helpers / fixtures
# ----------------------------------------------------------------------


def _make_worker_state(
    pid: int = 12345,
    *,
    heartbeat: float | None = None,
    status: str = "healthy",
    draining_since: float | None = None,
    task_ledger: list | None = None,
    queue: list | None = None,
) -> dict:
    return {
        "pid": pid,
        "heartbeat": heartbeat if heartbeat is not None else time.time(),
        "status": status,
        "draining_since": draining_since,
        "task_ledger": list(task_ledger or []),
        "queue": list(queue or []),
        "tcp_executing": [],
        "redis_executing": [],
        "broker_executing": [],
        "completed": [],
    }


def _mock_process(
    alive: bool = True,
    name: str = "Worker-8888_0",
    pid: int = 12345,
) -> MagicMock:
    p = MagicMock()
    p.is_alive.return_value = alive
    p.name = name
    p.pid = pid
    return p


def _make_supervisor(
    shared,
    job_list,
    *,
    prefix: str = "Worker-8888",
    heartbeat_timeout: float = 30.0,
    drain_timeout: float = 300.0,
) -> ProcessSupervisor:
    return ProcessSupervisor(
        shared_state=shared,
        job_list=job_list,
        worker_name_prefix=prefix,
        host="127.0.0.1",
        port=8888,
        debug=False,
        notify_empty=False,
        health_port=8080,
        check_interval=10.0,
        heartbeat_timeout=heartbeat_timeout,
        drain_timeout=drain_timeout,
    )


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


# ----------------------------------------------------------------------
# TEST 1 — Worker crash recovery E2E
# ----------------------------------------------------------------------


class TestWorkerCrashRecoveryE2E:
    def test_dead_worker_triggers_rescue_and_respawn(self, shared):
        """A dead worker gets its ledger rescued and a replacement spawned."""
        shared["Worker-8888_0"] = _make_worker_state(
            pid=99999,
            task_ledger=[
                {
                    "task_id": "orphan-1",
                    "payload": "cGF5bG9hZA==",  # "payload" in base64
                    "enqueued_at": 1000.0,
                },
                {
                    "task_id": "orphan-2",
                    "payload": "cGF5bG9hZC0y",
                    "enqueued_at": 1001.0,
                },
            ],
        )
        mock_proc = _mock_process(alive=False)
        job_list = [mock_proc]

        sup = _make_supervisor(shared, job_list)

        # Mock out the Redis client and the mp.Process constructor so we
        # don't actually spawn a subprocess or hit Redis.
        mock_redis_module = MagicMock()
        mock_r = MagicMock()
        mock_redis_module.Redis.from_url.return_value = mock_r

        saved = sys.modules.get("redis")
        sys.modules["redis"] = mock_redis_module
        try:
            with patch("qw.supervisor.mp.Process") as mock_mp_process:
                new_proc = MagicMock()
                new_proc.pid = 12345
                mock_mp_process.return_value = new_proc
                sup._check_worker(0, mock_proc, "Worker-8888_0")
        finally:
            if saved is not None:
                sys.modules["redis"] = saved
            else:
                sys.modules.pop("redis", None)

        # Tasks were rescued to Redis
        assert mock_r.xadd.call_count == 2
        # Process was terminated (dead process reports alive=False, so
        # terminate() is skipped, but join is still attempted)
        mock_proc.join.assert_called()
        # Slot was replaced with a new process
        assert job_list[0] is new_proc
        # Ledger was cleared
        assert dict(shared["Worker-8888_0"])["task_ledger"] == []


# ----------------------------------------------------------------------
# TEST 2 — Stuck worker drain cycle
# ----------------------------------------------------------------------


class TestStuckWorkerDrainCycle:
    def test_stale_heartbeat_marks_draining(self, shared):
        """No heartbeat for HEARTBEAT_TIMEOUT → state flips to draining."""
        shared["Worker-8888_0"] = _make_worker_state(
            heartbeat=time.time() - 60,  # 60s stale
            status="healthy",
        )
        mock_proc = _mock_process(alive=True)
        sup = _make_supervisor(
            shared, [mock_proc], heartbeat_timeout=30.0
        )
        sup._check_worker(0, mock_proc, "Worker-8888_0")

        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "draining"
        assert d["draining_since"] is not None

    def test_drain_timeout_kills_and_respawns(self, shared):
        """A worker draining longer than DRAIN_TIMEOUT is killed."""
        shared["Worker-8888_0"] = _make_worker_state(
            heartbeat=time.time() - 600,
            status="draining",
            draining_since=time.time() - 400,
        )
        mock_proc = _mock_process(alive=True)
        sup = _make_supervisor(
            shared, [mock_proc], drain_timeout=300.0
        )
        with patch.object(sup, "_kill_and_respawn") as mock_kill:
            sup._check_worker(0, mock_proc, "Worker-8888_0")
            mock_kill.assert_called_once_with(
                0, mock_proc, "Worker-8888_0"
            )


# ----------------------------------------------------------------------
# TEST 3 — Draining client retry (TCP rejection path)
# ----------------------------------------------------------------------


class TestDrainingClientRetry:
    @pytest.mark.asyncio
    async def test_draining_worker_rejects_new_tasks(self, shared):
        """QueueManager.put() on a draining worker raises QueueFull so
        the TCP client can retry onto a healthy sibling."""
        tracker = StateTracker(shared, worker_name="Worker-8888_0", pid=12345)
        tracker.set_status("draining", draining_since=time.time())

        qm = QueueManager(
            worker_name="Worker-8888_0", state_tracker=tracker
        )
        task = QueueWrapper(queued=True)

        with pytest.raises(asyncio.QueueFull, match="draining"):
            await qm.put(task, id=str(task.id))

    @pytest.mark.asyncio
    async def test_healthy_sibling_still_accepts_tasks(self, shared):
        """A different worker (healthy) on the same shared_state accepts
        tasks normally — simulating the client-retry landing on a healthy
        peer via SO_REUSEPORT."""
        # Two workers share the same Manager dict
        drain_tracker = StateTracker(
            shared, worker_name="Worker-8888_0", pid=1001
        )
        drain_tracker.set_status("draining", draining_since=time.time())

        healthy_tracker = StateTracker(
            shared, worker_name="Worker-8888_1", pid=1002
        )

        healthy_qm = QueueManager(
            worker_name="Worker-8888_1", state_tracker=healthy_tracker
        )
        task = QueueWrapper(queued=True)
        ok = await healthy_qm.put(task, id=str(task.id))
        assert ok is True


# ----------------------------------------------------------------------
# TEST 4 — Self-recovery
# ----------------------------------------------------------------------


class TestSelfRecovery:
    def test_heartbeat_resumes_restores_healthy(self, shared):
        """If a draining worker's heartbeat becomes fresh again, the
        supervisor promotes it back to "healthy"."""
        shared["Worker-8888_0"] = _make_worker_state(
            heartbeat=time.time(),  # FRESH — worker unblocked itself
            status="draining",
            draining_since=time.time() - 10,
        )
        mock_proc = _mock_process(alive=True)
        sup = _make_supervisor(
            shared, [mock_proc], heartbeat_timeout=30.0
        )
        sup._check_worker(0, mock_proc, "Worker-8888_0")
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None

    @pytest.mark.asyncio
    async def test_recovered_worker_accepts_tasks_again(self, shared):
        """After the supervisor marks a worker healthy again, the
        QueueManager stops rejecting new tasks."""
        tracker = StateTracker(
            shared, worker_name="Worker-8888_0", pid=12345
        )
        tracker.set_status("draining", draining_since=time.time())
        qm = QueueManager(
            worker_name="Worker-8888_0", state_tracker=tracker
        )

        # While draining, put() is rejected.
        with pytest.raises(asyncio.QueueFull, match="draining"):
            await qm.put(QueueWrapper(queued=True), id="first")

        # Promote back to healthy — supervisor self-recovery path.
        tracker.set_status("healthy")

        # Now put() succeeds.
        task = QueueWrapper(queued=True)
        ok = await qm.put(task, id=str(task.id))
        assert ok is True


# ----------------------------------------------------------------------
# TEST 5 — Health endpoint reporting
# ----------------------------------------------------------------------


class TestHealthReadyDraining:
    def test_readiness_503_when_draining(self, shared):
        shared["Worker-8888_0"] = _make_worker_state(status="draining")
        mock_q = MagicMock()
        mock_q.snapshot.return_value = {
            "size": 0,
            "max_size": 4,
            "base_size": 4,
            "grow_margin": 2,
            "ceiling": 6,
            "grow_events": 0,
            "discard_events": 0,
            "full": False,
        }
        hs = HealthServer(
            queue=mock_q,
            worker_name="Worker-8888_0",
            shared_state=shared,
        )
        status, body = hs._readiness()
        assert status == HTTP_503
        payload = json.loads(body)
        assert payload["status"] == "draining"
        assert payload["worker"] == "Worker-8888_0"

    def test_readiness_200_when_healthy(self, shared):
        shared["Worker-8888_0"] = _make_worker_state(status="healthy")
        mock_q = MagicMock()
        mock_q.snapshot.return_value = {
            "size": 0,
            "max_size": 4,
            "base_size": 4,
            "grow_margin": 2,
            "ceiling": 6,
            "grow_events": 0,
            "discard_events": 0,
            "full": False,
        }
        hs = HealthServer(
            queue=mock_q,
            worker_name="Worker-8888_0",
            shared_state=shared,
        )
        status, _ = hs._readiness()
        assert status == HTTP_200


# ----------------------------------------------------------------------
# TEST 6 — /supervisor/status endpoint
# ----------------------------------------------------------------------


class TestSupervisorStatusEndpoint:
    def test_returns_all_workers_with_expected_fields(self, shared):
        now = time.time()
        shared["Worker-8888_0"] = _make_worker_state(
            pid=1001,
            heartbeat=now,
            status="healthy",
        )
        shared["Worker-8888_1"] = _make_worker_state(
            pid=1002,
            heartbeat=now - 45,
            status="draining",
            draining_since=now - 30,
            task_ledger=[
                {"task_id": "t1", "payload": "x", "enqueued_at": now - 40},
            ],
        )
        mock_q = MagicMock()
        hs = HealthServer(
            queue=mock_q,
            worker_name="Worker-8888_0",
            shared_state=shared,
        )
        status, body = hs._supervisor_status()
        assert status == HTTP_200
        payload = json.loads(body)
        assert set(payload["workers"].keys()) == {
            "Worker-8888_0", "Worker-8888_1"
        }

        w1 = payload["workers"]["Worker-8888_1"]
        assert w1["status"] == "draining"
        assert w1["pid"] == 1002
        assert w1["draining_since"] is not None
        assert w1["heartbeat_age_s"] is not None
        # All required fields from the spec are present
        for key in (
            "pid",
            "status",
            "heartbeat_age_s",
            "draining_since",
            "task_ledger_depth",
            "queue_size",
        ):
            assert key in w1
        assert w1["task_ledger_depth"] == 1

    def test_returns_503_without_shared_state(self):
        hs = HealthServer(queue=MagicMock(), worker_name="W0")
        status, _ = hs._supervisor_status()
        assert status == HTTP_503


# ----------------------------------------------------------------------
# TEST 7 — StateTracker ↔ Supervisor shared-state round trip
# ----------------------------------------------------------------------


class TestSupervisorObservesWorkerWrites:
    def test_supervisor_reads_heartbeat_written_by_statetracker(
        self, shared
    ):
        """StateTracker writes on the worker side; the supervisor reads
        on the parent side. Smoke test that the two halves agree."""
        tracker = StateTracker(
            shared, worker_name="Worker-8888_0", pid=7777
        )
        assert tracker.get_heartbeat() == 0.0

        tracker.update_heartbeat()
        mock_proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, [mock_proc])

        read_state = sup._read_state("Worker-8888_0")
        assert read_state["heartbeat"] > 0
        assert abs(read_state["heartbeat"] - tracker.get_heartbeat()) < 0.01

    def test_supervisor_mark_draining_visible_to_statetracker(
        self, shared
    ):
        """Supervisor writes status; StateTracker sees it (the QWorker
        uses this to decide whether to reject new tasks)."""
        tracker = StateTracker(
            shared, worker_name="Worker-8888_0", pid=7777
        )
        assert tracker.get_status() == "healthy"

        sup = _make_supervisor(shared, [])
        sup._mark_draining("Worker-8888_0")

        assert tracker.get_status() == "draining"
        # StateTracker can flip it back
        tracker.set_status("healthy")
        sup_state = sup._read_state("Worker-8888_0")
        assert sup_state["status"] == "healthy"
