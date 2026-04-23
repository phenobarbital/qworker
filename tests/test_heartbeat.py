"""Unit tests for QWorker heartbeat loop and draining check (TASK-027).

Covers:
- `_heartbeat_loop()` writes heartbeat timestamp while `_running` is True
- `_heartbeat_loop()` exits on `asyncio.CancelledError`
- `_is_draining()` reads `StateTracker.get_status()` correctly
- `_is_draining()` returns False when `_state` is None
- `_is_draining()` is robust to read errors
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import time
from unittest.mock import MagicMock

import pytest

from qw.conf import WORKER_HEARTBEAT_INTERVAL
from qw.server import QWorker
from qw.state import StateTracker


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


def _make_worker(shared_state=None) -> QWorker:
    """Build a QWorker without starting it (no network, no subprocess).

    Each test drives the specific method under test. The event loop and
    queue attributes default to None — only the heartbeat and draining
    helpers are exercised directly.
    """
    # Use a throw-away loop so the QWorker.__init__ does not reach into
    # asyncio.get_event_loop() (which warns on recent Python versions).
    loop = asyncio.new_event_loop()
    try:
        worker = QWorker(
            host="127.0.0.1",
            port=0,
            worker_id=0,
            name="TestWorker",
            event_loop=loop,
            shared_state=shared_state,
        )
    finally:
        loop.close()
    return worker


# ----------------------------------------------------------------------
# _is_draining
# ----------------------------------------------------------------------


class TestIsDraining:
    def test_is_draining_false_without_state(self):
        """When shared_state is None, _is_draining() always returns False."""
        worker = _make_worker(shared_state=None)
        assert worker._is_draining() is False

    def test_is_draining_false_when_healthy(self, shared):
        """Default status after init is 'healthy'."""
        worker = _make_worker(shared_state=shared)
        assert worker._is_draining() is False

    def test_is_draining_true_when_marked(self, shared):
        """After StateTracker.set_status('draining'), helper returns True."""
        worker = _make_worker(shared_state=shared)
        worker._state.set_status("draining", draining_since=time.time())
        assert worker._is_draining() is True

    def test_is_draining_recovers_when_status_flips_back(self, shared):
        """Promoting back to healthy flips the helper back to False."""
        worker = _make_worker(shared_state=shared)
        worker._state.set_status("draining", draining_since=time.time())
        assert worker._is_draining() is True
        worker._state.set_status("healthy")
        assert worker._is_draining() is False

    def test_is_draining_swallows_read_errors(self):
        """If the state tracker raises, _is_draining() returns False."""
        worker = _make_worker(shared_state=None)
        fake = MagicMock()
        fake.get_status.side_effect = RuntimeError("proxy is dead")
        worker._state = fake
        assert worker._is_draining() is False


# ----------------------------------------------------------------------
# _heartbeat_loop
# ----------------------------------------------------------------------


class TestHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_heartbeat_writes_timestamp(self, shared, monkeypatch):
        """The loop writes at least one heartbeat while running."""
        # Speed the loop up so the test is fast.
        monkeypatch.setattr("qw.server.WORKER_HEARTBEAT_INTERVAL", 0.01)

        worker = _make_worker(shared_state=shared)
        assert worker._state.get_heartbeat() == 0.0

        task = asyncio.create_task(worker._heartbeat_loop())
        try:
            # Wait until we observe at least one heartbeat write.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if worker._state.get_heartbeat() > 0:
                    break
                await asyncio.sleep(0.01)
        finally:
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert worker._state.get_heartbeat() > 0

    @pytest.mark.asyncio
    async def test_heartbeat_exits_on_cancel(self, shared, monkeypatch):
        """Cancelling the task terminates the loop cleanly.

        The implementation handles CancelledError via a `break` (not
        re-raise), so the task finishes normally rather than raising.
        Either outcome is acceptable as long as the task is done quickly.
        """
        monkeypatch.setattr("qw.server.WORKER_HEARTBEAT_INTERVAL", 0.05)

        worker = _make_worker(shared_state=shared)
        task = asyncio.create_task(worker._heartbeat_loop())

        # Let the first iteration run, then cancel.
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.CancelledError:
            # acceptable — the loop may propagate cancel on some paths
            pass
        assert task.done()

    @pytest.mark.asyncio
    async def test_heartbeat_no_state_tracker_is_safe(self, monkeypatch):
        """Running with _state=None does not raise."""
        monkeypatch.setattr("qw.server.WORKER_HEARTBEAT_INTERVAL", 0.01)
        worker = _make_worker(shared_state=None)
        task = asyncio.create_task(worker._heartbeat_loop())
        await asyncio.sleep(0.05)
        worker._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # No exception propagated = success


# ----------------------------------------------------------------------
# Config sanity
# ----------------------------------------------------------------------


def test_worker_heartbeat_interval_is_int():
    """TASK-024 constant is imported cleanly into the server module."""
    assert isinstance(WORKER_HEARTBEAT_INTERVAL, int)
    assert WORKER_HEARTBEAT_INTERVAL > 0
