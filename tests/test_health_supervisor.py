"""Unit tests for HealthServer FEAT-005 enhancements (TASK-030).

Covers:
- `/health/ready` returns 503 with `"status": "draining"` when the
  worker is draining
- `/health/ready` preserves the existing queue-capacity behavior when
  the worker is healthy
- `HealthServer` still works when `shared_state=None`
- `/supervisor/status` returns a per-worker snapshot
- `/supervisor/status` returns 503 when `shared_state` is None
"""
from __future__ import annotations

import json
import multiprocessing as mp
import time
from unittest.mock import MagicMock

import pytest

from qw.health import HealthServer, HTTP_200, HTTP_503


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


@pytest.fixture
def mock_queue():
    """Returns a mock QueueManager whose snapshot() looks healthy."""
    q = MagicMock()
    q.snapshot.return_value = {
        "size": 1,
        "max_size": 4,
        "base_size": 4,
        "grow_margin": 2,
        "ceiling": 6,
        "grow_events": 0,
        "discard_events": 0,
        "full": False,
    }
    return q


def _seed_worker(
    shared,
    name: str,
    *,
    pid: int = 12345,
    status: str = "healthy",
    heartbeat: float | None = None,
    draining_since: float | None = None,
    task_ledger: list | None = None,
    queue_items: list | None = None,
) -> None:
    shared[name] = {
        "pid": pid,
        "status": status,
        "heartbeat": heartbeat if heartbeat is not None else time.time(),
        "draining_since": draining_since,
        "task_ledger": list(task_ledger or []),
        "queue": list(queue_items or []),
        "tcp_executing": [],
        "redis_executing": [],
        "broker_executing": [],
        "completed": [],
    }


# ----------------------------------------------------------------------
# /health/ready draining precedence
# ----------------------------------------------------------------------


class TestReadinessDraining:
    def test_returns_503_when_draining(self, shared, mock_queue):
        """A draining worker fails readiness even with queue capacity."""
        _seed_worker(
            shared,
            "W0",
            status="draining",
            draining_since=time.time(),
        )
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._readiness()
        assert status == HTTP_503
        payload = json.loads(body)
        assert payload["status"] == "draining"
        assert payload["worker"] == "W0"

    def test_returns_200_when_healthy(self, shared, mock_queue):
        """Healthy worker with non-full queue → 200."""
        _seed_worker(shared, "W0", status="healthy")
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._readiness()
        assert status == HTTP_200
        payload = json.loads(body)
        assert payload["status"] == "ok"

    def test_works_without_shared_state(self, mock_queue):
        """Backwards-compatible: no shared_state → queue-only logic."""
        hs = HealthServer(queue=mock_queue, worker_name="W0")
        status, _ = hs._readiness()
        assert status == HTTP_200

    def test_queue_at_ceiling_still_reports_full(self, shared, mock_queue):
        """Healthy + queue full → 503 "full" (existing behavior)."""
        _seed_worker(shared, "W0", status="healthy")
        mock_queue.snapshot.return_value = {
            "size": 6,
            "max_size": 6,
            "base_size": 4,
            "grow_margin": 2,
            "ceiling": 6,
            "grow_events": 0,
            "discard_events": 0,
            "full": True,
        }
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._readiness()
        assert status == HTTP_503
        payload = json.loads(body)
        assert payload["status"] == "full"

    def test_draining_beats_queue_full(self, shared, mock_queue):
        """If both draining AND full, response says "draining"."""
        _seed_worker(shared, "W0", status="draining", draining_since=1000.0)
        mock_queue.snapshot.return_value = {
            "size": 6,
            "max_size": 6,
            "base_size": 4,
            "grow_margin": 2,
            "ceiling": 6,
            "grow_events": 0,
            "discard_events": 0,
            "full": True,
        }
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._readiness()
        assert status == HTTP_503
        payload = json.loads(body)
        assert payload["status"] == "draining"


# ----------------------------------------------------------------------
# /supervisor/status
# ----------------------------------------------------------------------


class TestSupervisorStatus:
    def test_returns_all_workers(self, shared, mock_queue):
        now = time.time()
        _seed_worker(
            shared,
            "W0",
            status="healthy",
            heartbeat=now,
            task_ledger=[{"task_id": "t1", "payload": "x", "enqueued_at": 1}],
            queue_items=[{"task_id": "q1"}],
        )
        _seed_worker(
            shared,
            "W1",
            status="draining",
            heartbeat=now - 45,
            draining_since=now - 30,
        )
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._supervisor_status()
        assert status == HTTP_200
        payload = json.loads(body)
        assert "workers" in payload
        assert set(payload["workers"].keys()) == {"W0", "W1"}

        w0 = payload["workers"]["W0"]
        assert w0["status"] == "healthy"
        assert w0["task_ledger_depth"] == 1
        assert w0["queue_size"] == 1
        assert isinstance(w0["heartbeat_age_s"], (int, float))

        w1 = payload["workers"]["W1"]
        assert w1["status"] == "draining"
        assert w1["draining_since"] is not None
        assert w1["heartbeat_age_s"] is not None
        assert w1["heartbeat_age_s"] >= 44.9  # ~45s

    def test_heartbeat_age_is_none_when_never_written(
        self, shared, mock_queue
    ):
        _seed_worker(shared, "W0", heartbeat=0.0)
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._supervisor_status()
        assert status == HTTP_200
        payload = json.loads(body)
        assert payload["workers"]["W0"]["heartbeat_age_s"] is None

    def test_returns_503_without_shared_state(self, mock_queue):
        hs = HealthServer(queue=mock_queue, worker_name="W0")
        status, body = hs._supervisor_status()
        assert status == HTTP_503
        payload = json.loads(body)
        assert "error" in payload

    def test_route_dispatches_to_supervisor_status(self, shared, mock_queue):
        _seed_worker(shared, "W0")
        hs = HealthServer(
            queue=mock_queue, worker_name="W0", shared_state=shared
        )
        status, body = hs._route("/supervisor/status")
        assert status == HTTP_200
        payload = json.loads(body)
        assert "workers" in payload


# ----------------------------------------------------------------------
# Route fall-through
# ----------------------------------------------------------------------


class TestRouteFallThrough:
    def test_unknown_path_still_returns_404(self, mock_queue):
        hs = HealthServer(queue=mock_queue, worker_name="W0")
        status, body = hs._route("/does-not-exist")
        assert status == "404 Not Found"
        payload = json.loads(body)
        assert payload["error"] == "not found"

    def test_liveness_unaffected(self, mock_queue):
        hs = HealthServer(queue=mock_queue, worker_name="W0")
        status, body = hs._route("/health/live")
        assert status == HTTP_200
        payload = json.loads(body)
        assert payload["status"] == "alive"
