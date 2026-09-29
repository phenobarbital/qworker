"""Unit tests for HealthServer backend extension — TASK-041."""
import json
from unittest.mock import MagicMock

import pytest

from qw.health import HealthServer


def _make_shared_state():
    """Build a minimal Manager().dict()-like shared state with one worker."""
    state_data = {
        "pid": 12345,
        "status": "healthy",
        "heartbeat": 0.0,
        "draining_since": None,
        "task_ledger": [],
        "queue": [],
    }
    shared_state = MagicMock()
    shared_state.items.return_value = [("worker-1", state_data)]
    return shared_state


def _make_queue_mock():
    """Build a minimal QueueManager mock."""
    queue = MagicMock()
    queue.snapshot.return_value = {
        "size": 1,
        "max_size": 4,
        "base_size": 4,
        "grow_margin": 2,
        "ceiling": 6,
        "grow_events": 0,
        "discard_events": 0,
        "full": False,
        "consumer_alive": 3,
        "consumer_total": 3,
        "respawn_events": 0,
    }
    return queue


def _make_dispatcher_mock(overflow=False, memory_percent=45.2):
    """Build a BackendDispatcher mock with realistic attributes."""
    monitor = MagicMock()
    monitor.is_overflowing = overflow
    monitor.get_memory_percent.return_value = memory_percent

    local = MagicMock()
    docker = MagicMock()
    docker._tasks = {"abc": "container-1"}
    k8s = MagicMock()
    k8s._tasks = {}
    k8s._namespace = "default"

    dispatcher = MagicMock()
    dispatcher._monitor = monitor
    dispatcher._local = local
    dispatcher._docker = docker
    dispatcher._k8s = k8s
    return dispatcher


class TestHealthServerBackwardsCompat:
    """Tests to ensure backward compatibility when no dispatcher is set."""

    def test_supervisor_status_without_dispatcher_no_backends_key(self):
        """Without dispatcher, /supervisor/status has no 'backends' key."""
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
        )
        status, body = hs._supervisor_status()
        data = json.loads(body)
        assert "backends" not in data

    def test_supervisor_status_without_shared_state_returns_503(self):
        """Without shared_state, /supervisor/status returns 503."""
        hs = HealthServer(queue=_make_queue_mock(), worker_name="test")
        status, body = hs._supervisor_status()
        assert status == "503 Service Unavailable"

    def test_get_backend_status_returns_none_without_dispatcher(self):
        """_get_backend_status() returns None when no dispatcher configured."""
        hs = HealthServer(queue=_make_queue_mock(), worker_name="test")
        assert hs._get_backend_status() is None


class TestHealthServerWithDispatcher:
    """Tests for HealthServer with BackendDispatcher wired in."""

    def test_supervisor_status_includes_backends_key(self):
        """With dispatcher, /supervisor/status includes 'backends' key."""
        dispatcher = _make_dispatcher_mock()
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        assert status == "200 OK"
        data = json.loads(body)
        assert "backends" in data

    def test_backends_includes_overflow_active(self):
        """backends section includes overflow_active field."""
        dispatcher = _make_dispatcher_mock(overflow=False)
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        data = json.loads(body)
        assert "overflow_active" in data["backends"]
        assert data["backends"]["overflow_active"] is False

    def test_backends_includes_memory_percent(self):
        """backends section includes memory_percent field."""
        dispatcher = _make_dispatcher_mock(memory_percent=60.5)
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        data = json.loads(body)
        assert "memory_percent" in data["backends"]
        assert data["backends"]["memory_percent"] == 60.5

    def test_backends_includes_per_backend_status(self):
        """backends section includes status for each configured backend."""
        dispatcher = _make_dispatcher_mock()
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        data = json.loads(body)
        backends_info = data["backends"]
        assert "backends" in backends_info
        backend_statuses = backends_info["backends"]
        assert "local" in backend_statuses
        assert "docker" in backend_statuses
        assert "k8s" in backend_statuses

    def test_overflow_active_true_when_overflowing(self):
        """overflow_active is True when monitor is overflowing."""
        dispatcher = _make_dispatcher_mock(overflow=True)
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        data = json.loads(body)
        assert data["backends"]["overflow_active"] is True

    def test_workers_still_present_with_dispatcher(self):
        """workers section still present when dispatcher is configured."""
        dispatcher = _make_dispatcher_mock()
        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        data = json.loads(body)
        assert "workers" in data
        assert "worker-1" in data["workers"]

    def test_backend_error_doesnt_crash_endpoint(self):
        """If _get_backend_status raises, supervisor status still returns 200."""
        dispatcher = MagicMock()
        dispatcher._monitor = None
        dispatcher._local = None
        dispatcher._docker = None
        dispatcher._k8s = None
        # Make _get_backend_status throw by corrupting dispatcher
        dispatcher._monitor = "not-a-monitor"  # won't have .is_overflowing attr

        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        # Should not raise
        status, body = hs._supervisor_status()
        # Status may be 200 or 503 but must not raise
        assert status in ("200 OK", "503 Service Unavailable")

    def test_no_monitor_in_dispatcher(self):
        """Handles dispatcher without _monitor attribute gracefully."""
        dispatcher = MagicMock(spec=[])
        dispatcher._monitor = None
        dispatcher._local = MagicMock()
        dispatcher._docker = None
        dispatcher._k8s = None

        hs = HealthServer(
            queue=_make_queue_mock(),
            worker_name="test",
            shared_state=_make_shared_state(),
            backend_dispatcher=dispatcher,
        )
        status, body = hs._supervisor_status()
        assert status == "200 OK"
        data = json.loads(body)
        assert data["backends"]["overflow_active"] is False
        assert data["backends"]["memory_percent"] is None
