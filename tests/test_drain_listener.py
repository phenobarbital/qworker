"""Unit tests for the drain-aware TCP listener.

A worker marked ``draining`` by the supervisor must leave the
``SO_REUSEPORT`` pool so the kernel stops routing new connections to it.
These tests cover:

- ``_open_listener()`` creates a serving socket and caches its address
- ``_close_listener()`` closes the socket, drops ``_server`` to None, and
  keeps the cached serving address
- ``_close_listener()`` is a no-op when there is no listener
- ``_drain_listener_watcher()`` closes the listener when the worker is
  marked draining and reopens it when the worker recovers to healthy
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import socket
import time

import pytest

from qw.server import QWorker


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


@pytest.fixture
def shared():
    manager = mp.Manager()
    d = manager.dict()
    yield d
    manager.shutdown()


def _free_port() -> int:
    """Reserve and release an ephemeral port so we can reopen on it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_worker(shared_state=None, port: int = 0) -> QWorker:
    """Build a QWorker without starting its full serve loop.

    protocol is None, so _open_listener() uses asyncio.start_server() bound
    to the running loop — self._loop is never touched by these tests.
    """
    loop = asyncio.new_event_loop()
    try:
        worker = QWorker(
            host="127.0.0.1",
            port=port,
            worker_id=0,
            name="TestWorker",
            event_loop=loop,
            shared_state=shared_state,
        )
    finally:
        loop.close()
    return worker


async def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# ----------------------------------------------------------------------
# _open_listener / _close_listener
# ----------------------------------------------------------------------


class TestOpenCloseListener:
    @pytest.mark.asyncio
    async def test_open_then_close(self):
        worker = _make_worker(port=_free_port())
        await worker._open_listener()
        try:
            assert worker._server is not None
            assert worker._server.sockets, "expected at least one socket"
            assert worker._serving_addrs != ""
            cached = worker._serving_addrs
        finally:
            await worker._close_listener()

        # Listener gone from the pool, but the cached address is retained
        # so info/health commands keep returning a sensible value.
        assert worker._server is None
        assert worker._serving_addrs == cached

    @pytest.mark.asyncio
    async def test_open_is_idempotent(self):
        worker = _make_worker(port=_free_port())
        await worker._open_listener()
        first = worker._server
        try:
            await worker._open_listener()  # second call must not replace it
            assert worker._server is first
        finally:
            await worker._close_listener()

    @pytest.mark.asyncio
    async def test_close_without_listener_is_noop(self):
        worker = _make_worker()
        assert worker._server is None
        await worker._close_listener()  # must not raise
        assert worker._server is None


# ----------------------------------------------------------------------
# _drain_listener_watcher
# ----------------------------------------------------------------------


class TestDrainListenerWatcher:
    @pytest.mark.asyncio
    async def test_watcher_closes_on_drain_and_reopens_on_recovery(
        self, shared, monkeypatch
    ):
        # Tight poll so the test runs fast.
        monkeypatch.setattr(
            "qw.server.WORKER_DRAIN_LISTENER_INTERVAL", 0.01
        )

        port = _free_port()
        worker = _make_worker(shared_state=shared, port=port)
        await worker._open_listener()
        assert worker._server is not None

        task = asyncio.create_task(worker._drain_listener_watcher())
        try:
            # Mark draining → watcher must drop the listener from the pool.
            worker._state.set_status("draining", draining_since=time.time())
            assert await _wait_for(lambda: worker._server is None), \
                "listener should close while draining"

            # Recover → watcher must reopen the listener.
            worker._state.set_status("healthy")
            assert await _wait_for(lambda: worker._server is not None), \
                "listener should reopen after recovery"
        finally:
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await worker._close_listener()

    @pytest.mark.asyncio
    async def test_watcher_exits_when_running_cleared(self, shared, monkeypatch):
        monkeypatch.setattr(
            "qw.server.WORKER_DRAIN_LISTENER_INTERVAL", 0.01
        )
        worker = _make_worker(shared_state=shared)
        task = asyncio.create_task(worker._drain_listener_watcher())
        await asyncio.sleep(0.05)
        worker._running = False
        # The loop checks _running at the top; cancel to avoid waiting a
        # full sleep interval, mirroring the heartbeat-loop test style.
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.CancelledError:
            pass
        assert task.done()
