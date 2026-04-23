"""Unit tests for ProcessSupervisor (TASK-028, FEAT-005).

These tests exercise the state machine in isolation using mocked
processes and a real ``multiprocessing.Manager().dict()``. Integration
tests with real subprocesses live in ``tests/test_supervisor_integration.py``.
"""
from __future__ import annotations

import multiprocessing as mp
import time
from unittest.mock import MagicMock, patch

import pytest

from qw.supervisor import ProcessSupervisor


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


def _seed_worker(
    shared,
    name: str,
    *,
    pid: int = 12345,
    heartbeat: float | None = None,
    status: str = "healthy",
    draining_since: float | None = None,
    task_ledger: list | None = None,
) -> None:
    """Pre-populate a worker entry in shared_state (as StateTracker would)."""
    shared[name] = {
        "pid": pid,
        "heartbeat": heartbeat if heartbeat is not None else time.time(),
        "status": status,
        "draining_since": draining_since,
        "task_ledger": list(task_ledger or []),
        "queue": [],
        "tcp_executing": [],
        "redis_executing": [],
        "broker_executing": [],
        "completed": [],
    }


def _mock_process(alive: bool = True, name: str = "Worker-8888_0") -> MagicMock:
    p = MagicMock()
    p.is_alive.return_value = alive
    p.name = name
    p.pid = 12345
    return p


def _make_supervisor(
    shared,
    job_list,
    *,
    prefix: str = "Worker-8888",
    heartbeat_timeout: float = 30.0,
    drain_timeout: float = 300.0,
    check_interval: float = 10.0,
    kill_grace: float = 10.0,
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
        check_interval=check_interval,
        heartbeat_timeout=heartbeat_timeout,
        drain_timeout=drain_timeout,
        kill_grace=kill_grace,
    )


# ----------------------------------------------------------------------
# Construction / daemon thread
# ----------------------------------------------------------------------


class TestConstruction:
    def test_is_daemon_thread(self, shared):
        sup = _make_supervisor(shared, job_list=[])
        assert sup.daemon is True
        assert sup.name == "QW.Supervisor"

    def test_stop_sets_event(self, shared):
        sup = _make_supervisor(shared, job_list=[])
        assert not sup._stop_event.is_set()
        sup.stop()
        assert sup._stop_event.is_set()


# ----------------------------------------------------------------------
# State machine — detection
# ----------------------------------------------------------------------


class TestCheckWorkerStateMachine:
    def test_dead_process_triggers_kill_and_respawn(self, shared):
        _seed_worker(shared, "Worker-8888_0")
        proc = _mock_process(alive=False)
        sup = _make_supervisor(shared, job_list=[proc])
        with patch.object(sup, "_kill_and_respawn") as mock_kill:
            sup._check_worker(0, proc, "Worker-8888_0")
            mock_kill.assert_called_once_with(0, proc, "Worker-8888_0")

    def test_stale_heartbeat_marks_draining(self, shared):
        _seed_worker(
            shared,
            "Worker-8888_0",
            heartbeat=time.time() - 60,  # very stale
            status="healthy",
        )
        proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, job_list=[proc], heartbeat_timeout=30)
        sup._check_worker(0, proc, "Worker-8888_0")
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "draining"
        assert d["draining_since"] is not None

    def test_fresh_heartbeat_leaves_healthy_alone(self, shared):
        _seed_worker(
            shared,
            "Worker-8888_0",
            heartbeat=time.time(),
            status="healthy",
        )
        proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, job_list=[proc], heartbeat_timeout=30)
        sup._check_worker(0, proc, "Worker-8888_0")
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None

    def test_zero_heartbeat_not_yet_draining(self, shared):
        """A worker that hasn't written a heartbeat yet is not penalised."""
        _seed_worker(
            shared,
            "Worker-8888_0",
            heartbeat=0.0,
            status="healthy",
        )
        proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, job_list=[proc], heartbeat_timeout=30)
        sup._check_worker(0, proc, "Worker-8888_0")
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "healthy"


class TestDrainingTransitions:
    def test_recovery_when_heartbeat_resumes(self, shared):
        _seed_worker(
            shared,
            "Worker-8888_0",
            heartbeat=time.time(),  # FRESH again
            status="draining",
            draining_since=time.time() - 10,
        )
        proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, job_list=[proc], heartbeat_timeout=30)
        sup._check_worker(0, proc, "Worker-8888_0")
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None

    def test_drain_timeout_exceeded_triggers_kill(self, shared):
        _seed_worker(
            shared,
            "Worker-8888_0",
            heartbeat=time.time() - 600,  # still stale
            status="draining",
            draining_since=time.time() - 400,  # > drain_timeout (300)
        )
        proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, job_list=[proc], drain_timeout=300)
        with patch.object(sup, "_kill_and_respawn") as mock_kill:
            sup._check_worker(0, proc, "Worker-8888_0")
            mock_kill.assert_called_once_with(0, proc, "Worker-8888_0")

    def test_still_draining_within_budget_does_nothing(self, shared):
        """A worker draining for less than drain_timeout is left alone."""
        _seed_worker(
            shared,
            "Worker-8888_0",
            heartbeat=time.time() - 60,
            status="draining",
            draining_since=time.time() - 30,  # well under 300s
        )
        proc = _mock_process(alive=True)
        sup = _make_supervisor(shared, job_list=[proc], drain_timeout=300)
        with patch.object(sup, "_kill_and_respawn") as mock_kill:
            sup._check_worker(0, proc, "Worker-8888_0")
            mock_kill.assert_not_called()
        # Still draining
        assert dict(shared["Worker-8888_0"])["status"] == "draining"


# ----------------------------------------------------------------------
# Status helpers
# ----------------------------------------------------------------------


class TestStatusHelpers:
    def test_mark_draining_sets_timestamp(self, shared):
        _seed_worker(shared, "Worker-8888_0")
        sup = _make_supervisor(shared, job_list=[])
        before = time.time()
        sup._mark_draining("Worker-8888_0")
        after = time.time()
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "draining"
        assert before <= d["draining_since"] <= after

    def test_mark_healthy_clears_draining_since(self, shared):
        _seed_worker(
            shared,
            "Worker-8888_0",
            status="draining",
            draining_since=time.time(),
        )
        sup = _make_supervisor(shared, job_list=[])
        sup._mark_healthy("Worker-8888_0")
        d = dict(shared["Worker-8888_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None

    def test_mark_draining_missing_worker_is_noop(self, shared):
        sup = _make_supervisor(shared, job_list=[])
        # Should not raise
        sup._mark_draining("nonexistent")


# ----------------------------------------------------------------------
# Task rescue
# ----------------------------------------------------------------------


class TestRescueTasks:
    def test_rescue_pushes_entries_to_redis_stream(self, shared):
        _seed_worker(
            shared,
            "Worker-8888_0",
            task_ledger=[
                {"task_id": "id-1", "payload": "b64-1", "enqueued_at": 1000.0},
                {"task_id": "id-2", "payload": "b64-2", "enqueued_at": 1001.0},
            ],
        )
        sup = _make_supervisor(shared, job_list=[])

        # Patch the redis module in sys.modules so the method-level
        # `import redis` inside _rescue_tasks picks up our mock.
        mock_redis_module = MagicMock()
        mock_r = MagicMock()
        mock_redis_module.Redis.from_url.return_value = mock_r

        import sys
        saved = sys.modules.get("redis")
        sys.modules["redis"] = mock_redis_module
        try:
            pushed = sup._rescue_tasks("Worker-8888_0")
        finally:
            if saved is not None:
                sys.modules["redis"] = saved
            else:
                sys.modules.pop("redis", None)

        assert pushed == 2
        assert mock_r.xadd.call_count == 2
        # Ledger was cleared
        d = dict(shared["Worker-8888_0"])
        assert d["task_ledger"] == []

    def test_rescue_empty_ledger_noop(self, shared):
        _seed_worker(shared, "Worker-8888_0", task_ledger=[])
        sup = _make_supervisor(shared, job_list=[])
        # Even without patching redis, an empty ledger returns early.
        assert sup._rescue_tasks("Worker-8888_0") == 0

    def test_rescue_clears_ledger_even_if_redis_fails(self, shared):
        """Ledger is drained up front so a crashing redis call does not
        cause duplicate rescues on the next cycle."""
        _seed_worker(
            shared,
            "Worker-8888_0",
            task_ledger=[
                {"task_id": "id-1", "payload": "b64-1", "enqueued_at": 1.0},
            ],
        )
        sup = _make_supervisor(shared, job_list=[])
        mock_redis_module = MagicMock()
        mock_redis_module.Redis.from_url.side_effect = RuntimeError(
            "redis down"
        )
        import sys
        saved = sys.modules.get("redis")
        sys.modules["redis"] = mock_redis_module
        try:
            pushed = sup._rescue_tasks("Worker-8888_0")
        finally:
            if saved is not None:
                sys.modules["redis"] = saved
            else:
                sys.modules.pop("redis", None)
        assert pushed == 0
        assert dict(shared["Worker-8888_0"])["task_ledger"] == []


# ----------------------------------------------------------------------
# Respawn
# ----------------------------------------------------------------------


class TestKillAndRespawn:
    def test_kill_and_respawn_replaces_job_list_slot(self, shared):
        _seed_worker(shared, "Worker-8888_0")
        old_proc = _mock_process(alive=False, name="Worker-8888_0")
        job_list = [old_proc]
        sup = _make_supervisor(shared, job_list=job_list)

        new_proc = MagicMock()
        new_proc.pid = 99999
        with patch.object(sup, "_rescue_tasks", return_value=0), \
                patch.object(sup, "_respawn_worker", return_value=new_proc):
            sup._kill_and_respawn(0, old_proc, "Worker-8888_0")

        assert job_list[0] is new_proc

    def test_kill_and_respawn_calls_rescue_first(self, shared):
        _seed_worker(shared, "Worker-8888_0")
        old_proc = _mock_process(alive=False)
        job_list = [old_proc]
        sup = _make_supervisor(shared, job_list=job_list)

        call_order = []
        with patch.object(
            sup, "_rescue_tasks",
            side_effect=lambda *a, **kw: call_order.append("rescue") or 0,
        ), patch.object(
            sup, "_respawn_worker",
            side_effect=lambda *a, **kw: (
                call_order.append("respawn") or MagicMock()
            ),
        ):
            sup._kill_and_respawn(0, old_proc, "Worker-8888_0")

        assert call_order.index("rescue") < call_order.index("respawn")

    def test_kill_and_respawn_escalates_to_sigkill_if_alive(self, shared):
        """If SIGTERM does not kill within the grace window, SIGKILL is used."""
        _seed_worker(shared, "Worker-8888_0")

        proc = MagicMock()
        proc.name = "Worker-8888_0"
        # Stays alive across all terminate() + is_alive() polls
        proc.is_alive.return_value = True

        # Short kill_grace via the constructor param (no module patching).
        sup = _make_supervisor(shared, job_list=[proc], kill_grace=0.05)
        with patch.object(sup, "_rescue_tasks", return_value=0), \
                patch.object(sup, "_respawn_worker", return_value=MagicMock()):
            sup._kill_and_respawn(0, proc, "Worker-8888_0")

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_kill_grace_defaults_to_config_constant(self, shared):
        """If `kill_grace` is not passed, the configured default wins."""
        from qw.conf import SUPERVISOR_KILL_GRACE

        sup = ProcessSupervisor(
            shared_state=shared,
            job_list=[],
            worker_name_prefix="W",
            host="127.0.0.1",
            port=8888,
            debug=False,
            notify_empty=False,
            health_port=8080,
        )
        assert sup._kill_grace == float(SUPERVISOR_KILL_GRACE)


# ----------------------------------------------------------------------
# Non-worker process filtering
# ----------------------------------------------------------------------


class TestWorkerNameFilter:
    def test_non_worker_process_is_skipped(self, shared):
        notify = _mock_process(alive=True, name="NotifyWorker_abc")
        sup = _make_supervisor(shared, job_list=[notify])
        assert sup._worker_name_for(0, notify) is None

    def test_worker_process_returns_name(self, shared):
        worker = _mock_process(alive=True, name="Worker-8888_0")
        sup = _make_supervisor(shared, job_list=[worker])
        assert sup._worker_name_for(0, worker) == "Worker-8888_0"


# ----------------------------------------------------------------------
# Run loop
# ----------------------------------------------------------------------


class TestRunLoop:
    def test_run_calls_check_worker_and_exits_on_stop(self, shared):
        _seed_worker(shared, "Worker-8888_0")
        proc = _mock_process(alive=True, name="Worker-8888_0")
        sup = _make_supervisor(
            shared, job_list=[proc], check_interval=0.05
        )
        calls = []

        def fake_check(idx, process, worker_name):
            calls.append(worker_name)
            # Stop after the first successful check to make the test
            # deterministic.
            sup.stop()

        with patch.object(sup, "_check_worker", side_effect=fake_check):
            sup.start()
            sup.join(timeout=2.0)

        assert not sup.is_alive()
        assert "Worker-8888_0" in calls
