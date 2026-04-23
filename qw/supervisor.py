"""Process supervisor — monitors worker lifecycle and rescues orphaned tasks.

FEAT-005 Module 5. Runs as a daemon thread inside the parent
``SpawnProcess``. Periodically inspects every worker:

* If ``Process.is_alive()`` is ``False`` → rescue orphaned tasks from the
  worker's shared-state ledger, push them to the Redis stream so a
  sibling worker can consume them, then spawn a fresh replacement
  ``mp.Process`` with the same ``worker_id`` slot.
* If the worker's heartbeat is stale beyond ``WORKER_HEARTBEAT_TIMEOUT``
  and the worker is still marked ``"healthy"`` → flip the worker's
  status to ``"draining"`` so the ``QueueManager`` rejects new enqueue
  attempts and the TCP clients retry onto a healthy sibling via
  ``SO_REUSEPORT``.
* If a worker is already draining and its heartbeat resumes → restore
  the worker to ``"healthy"`` (self-recovery).
* If a worker has been draining for longer than ``WORKER_DRAIN_TIMEOUT``
  with no recovery → kill and respawn.

Reads and writes ``shared_state`` directly (as opposed to instantiating
``StateTracker``) because the supervisor operates across *all* workers.

CRITICAL: Manager proxies do NOT detect nested mutations. Every write
uses full-value replacement — see :mod:`qw.state` for the rule.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import time
from typing import Optional

from navconfig.logging import logging

from .conf import (
    REDIS_WORKER_STREAM,
    SUPERVISOR_CHECK_INTERVAL,
    SUPERVISOR_KILL_GRACE,
    WORKER_DRAIN_TIMEOUT,
    WORKER_HEARTBEAT_TIMEOUT,
    WORKER_REDIS,
)
from .server import start_server


class ProcessSupervisor(threading.Thread):
    """Monitors worker processes and manages their lifecycle.

    Runs as a daemon thread so it does not prevent the parent process
    from exiting cleanly.

    Args:
        shared_state: ``multiprocessing.Manager().dict()`` proxy shared
            with every worker. Keys are worker names, values are the
            per-worker state dicts initialized by ``StateTracker``.
        job_list: The mutable list of ``multiprocessing.Process``
            objects managed by ``SpawnProcess``. The supervisor replaces
            dead entries in-place.
        worker_name_prefix: The prefix used when workers were spawned,
            e.g. ``"Worker-8888"``. Worker names are
            ``f"{prefix}_{idx}"``.
        host: Host the worker TCP server binds to.
        port: Shared TCP port (``SO_REUSEPORT``).
        debug: Forwarded to ``start_server``.
        notify_empty: Forwarded to ``start_server``.
        health_port: Forwarded to ``start_server``.
        check_interval: Seconds between supervisor cycles.
        heartbeat_timeout: Seconds of heartbeat staleness that triggers
            a healthy → draining transition.
        drain_timeout: Seconds a worker can stay in ``"draining"``
            before the supervisor forcefully kills and respawns it.
    """

    def __init__(
        self,
        shared_state,
        job_list: list,
        worker_name_prefix: str,
        host: str,
        port: int,
        debug: bool,
        notify_empty: bool,
        health_port: int,
        check_interval: float = SUPERVISOR_CHECK_INTERVAL,
        heartbeat_timeout: float = WORKER_HEARTBEAT_TIMEOUT,
        drain_timeout: float = WORKER_DRAIN_TIMEOUT,
        kill_grace: float = SUPERVISOR_KILL_GRACE,
    ) -> None:
        super().__init__(daemon=True, name="QW.Supervisor")
        self._shared_state = shared_state
        self._job_list = job_list
        self._prefix = worker_name_prefix
        self._host = host
        self._port = port
        self._debug = debug
        self._notify_empty = notify_empty
        self._health_port = health_port
        self._check_interval = float(check_interval)
        self._heartbeat_timeout = float(heartbeat_timeout)
        self._drain_timeout = float(drain_timeout)
        # Stored as an instance attribute (consistent with the other
        # timeouts) so tests can override without patching module-level
        # constants, and so different supervisor instances can use
        # different kill-grace values if needed.
        self._kill_grace = float(kill_grace)
        self._stop_event = threading.Event()
        self.logger = logging.getLogger("QW.Supervisor")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the supervisor loop to exit at the next iteration."""
        self._stop_event.set()

    def run(self) -> None:
        """Main supervisor loop — called by ``Thread.start()``."""
        self.logger.info(
            "Supervisor started (interval=%ss, heartbeat_timeout=%ss, "
            "drain_timeout=%ss)",
            self._check_interval,
            self._heartbeat_timeout,
            self._drain_timeout,
        )
        while not self._stop_event.is_set():
            try:
                for idx, process in enumerate(list(self._job_list)):
                    worker_name = self._worker_name_for(idx, process)
                    if worker_name is None:
                        # Skip non-worker processes (e.g. NotifyWorker)
                        continue
                    try:
                        self._check_worker(idx, process, worker_name)
                    except Exception:  # pragma: no cover — defensive
                        self.logger.exception(
                            "Supervisor check failed for %s", worker_name
                        )
            except Exception:  # pragma: no cover — defensive outer guard
                self.logger.exception("Supervisor check cycle error")
            # Use the stop event as a sleep so stop() wakes us up
            # promptly.
            self._stop_event.wait(timeout=self._check_interval)
        self.logger.info("Supervisor stopped")

    # ------------------------------------------------------------------
    # Per-worker inspection and state machine
    # ------------------------------------------------------------------

    def _worker_name_for(self, idx: int, process) -> Optional[str]:
        """Return the worker name for this job-list slot, or None to skip.

        Only processes whose name starts with ``self._prefix`` are
        considered workers. Everything else (e.g. the ``NotifyWorker``)
        is skipped silently.
        """
        name = getattr(process, "name", "") or ""
        if not name.startswith(self._prefix):
            return None
        return f"{self._prefix}_{idx}"

    def _read_state(self, worker_name: str) -> dict:
        """Read a worker's state dict (thread-safe via Manager proxy)."""
        try:
            raw = self._shared_state.get(worker_name, {})
        except Exception:  # pragma: no cover — defensive
            return {}
        return dict(raw) if raw else {}

    def _check_worker(self, idx: int, process, worker_name: str) -> None:
        """Inspect a single worker and apply the state machine.

        Transitions are described in the spec (Section 2). This method
        performs at most ONE transition per cycle.
        """
        # 1) Dead process — highest priority.
        try:
            alive = process.is_alive()
        except Exception:  # pragma: no cover — defensive
            alive = False
        if not alive:
            self.logger.warning(
                "Worker %s (pid=%s) is not alive — rescuing and respawning",
                worker_name,
                getattr(process, "pid", None),
            )
            self._kill_and_respawn(idx, process, worker_name)
            return

        state = self._read_state(worker_name)
        if not state:
            # Worker hasn't registered in shared_state yet (first tick
            # after spawn). Nothing to check.
            return

        now = time.time()
        heartbeat = state.get("heartbeat", 0.0) or 0.0
        status = state.get("status", "healthy")
        draining_since = state.get("draining_since")
        heartbeat_age = now - heartbeat if heartbeat > 0 else float("inf")

        if status == "draining":
            # 2a) Recovery — heartbeat is fresh again.
            if heartbeat > 0 and heartbeat_age <= self._heartbeat_timeout:
                self.logger.info(
                    "Worker %s recovered (heartbeat age=%.1fs) — "
                    "restoring to healthy",
                    worker_name,
                    heartbeat_age,
                )
                self._mark_healthy(worker_name)
                return
            # 2b) Drain timeout expired — kill.
            if (
                draining_since is not None
                and (now - draining_since) > self._drain_timeout
            ):
                self.logger.warning(
                    "Worker %s drained for %.1fs (>%ss) — killing and "
                    "respawning",
                    worker_name,
                    now - draining_since,
                    self._drain_timeout,
                )
                self._kill_and_respawn(idx, process, worker_name)
                return
            # Still draining, still within budget. Nothing to do.
            return

        # 3) Healthy worker with stale heartbeat → mark draining.
        if heartbeat > 0 and heartbeat_age > self._heartbeat_timeout:
            self.logger.warning(
                "Worker %s heartbeat stale (age=%.1fs > %ss) — marking "
                "draining",
                worker_name,
                heartbeat_age,
                self._heartbeat_timeout,
            )
            self._mark_draining(worker_name)
            return

        # Healthy + fresh heartbeat (or no heartbeat yet): nothing to do.

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _mark_draining(self, worker_name: str) -> None:
        """Transition a worker from healthy → draining in shared state."""
        try:
            d = dict(self._shared_state[worker_name])
        except KeyError:
            return
        d["status"] = "draining"
        d["draining_since"] = time.time()
        self._shared_state[worker_name] = d

    def _mark_healthy(self, worker_name: str) -> None:
        """Transition a worker from draining → healthy in shared state."""
        try:
            d = dict(self._shared_state[worker_name])
        except KeyError:
            return
        d["status"] = "healthy"
        d["draining_since"] = None
        self._shared_state[worker_name] = d

    # ------------------------------------------------------------------
    # Task rescue + respawn
    # ------------------------------------------------------------------

    def _rescue_tasks(self, worker_name: str) -> int:
        """Read the worker's task ledger and push entries to the Redis stream.

        Ledger entries store the task as base64-encoded cloudpickle,
        which is exactly the payload shape ``start_subscription`` expects
        (see ``qw/server.py``). Rescued tasks get re-submitted as
        ``{"task": <b64 payload>, "uid": <task_id>}`` stream entries so a
        healthy sibling worker can pick them up via the existing consumer
        group.

        Uses the SYNC ``redis`` client (not ``redis.asyncio``) because
        the supervisor runs in a thread, not an event loop. The import
        is at method level so the module stays importable even when only
        the async redis client is available.

        Returns:
            The number of ledger entries that were successfully pushed.
        """
        try:
            d = dict(self._shared_state[worker_name])
        except KeyError:
            return 0
        entries = list(d.get("task_ledger", []))
        # Clear the ledger atomically so a subsequent _read_state sees
        # no residue (the process is about to be replaced anyway).
        d["task_ledger"] = []
        self._shared_state[worker_name] = d

        if not entries:
            return 0

        try:
            import redis  # noqa: WPS433 — deferred import, supervisor-only
        except ImportError:  # pragma: no cover — redis is a hard dep
            self.logger.error(
                "sync redis client unavailable — cannot rescue %d tasks "
                "from %s",
                len(entries),
                worker_name,
            )
            return 0

        pushed = 0
        try:
            r = redis.Redis.from_url(WORKER_REDIS)
            try:
                for entry in entries:
                    try:
                        r.xadd(
                            REDIS_WORKER_STREAM,
                            {
                                "task": entry["payload"],
                                "uid": entry["task_id"],
                            },
                        )
                        pushed += 1
                    except Exception as xerr:
                        self.logger.error(
                            "Failed to rescue task %s from %s: %s",
                            entry.get("task_id"),
                            worker_name,
                            xerr,
                        )
            finally:
                try:
                    r.close()
                except Exception:  # pragma: no cover — defensive
                    pass
        except Exception as rex:
            self.logger.error(
                "Redis connection for rescue failed (%s): %s",
                WORKER_REDIS,
                rex,
            )
            return pushed

        self.logger.info(
            "Rescued %d/%d tasks from %s",
            pushed,
            len(entries),
            worker_name,
        )
        return pushed

    def _respawn_worker(self, idx: int, worker_name: str):
        """Spawn a replacement ``mp.Process`` using the same slot id.

        Replicates the exact spawn pattern used by
        ``SpawnProcess.__init__`` in ``qw/process.py`` (lines 94–107 at
        the time of FEAT-005): same ``target=start_server``, same
        positional-arg tuple, same ``name=f"{prefix}_{idx}"``. Staying
        bit-for-bit aligned with that call site matters because the new
        process must:

        * join the same ``SO_REUSEPORT`` pool (same host/port),
        * register into the same slot in the shared Manager dict under
          the same ``worker_name`` (so ``StateTracker`` overwrites the
          dead entry cleanly),
        * be addressable by the supervisor in subsequent cycles under
          the same ``f"{prefix}_{idx}"`` name.

        If either the spawn pattern or ``start_server``'s signature ever
        changes in ``qw/process.py`` / ``qw/server.py``, this method
        MUST be updated in lockstep.

        Returns:
            The newly started ``mp.Process`` (already ``.start()``-ed),
            or ``None`` if spawning failed.
        """
        try:
            new_process = mp.Process(
                target=start_server,
                name=worker_name,
                args=(
                    idx,
                    self._host,
                    self._port,
                    self._debug,
                    self._notify_empty,
                    self._health_port,
                    self._shared_state,
                ),
            )
            new_process.start()
            self.logger.info(
                "Respawned %s (new pid=%s, slot=%d)",
                worker_name,
                new_process.pid,
                idx,
            )
            return new_process
        except Exception as err:
            self.logger.exception(
                "Failed to respawn %s (slot %d): %s",
                worker_name,
                idx,
                err,
            )
            return None

    def _kill_and_respawn(self, idx: int, process, worker_name: str) -> None:
        """Kill a stuck/dead worker and spawn a fresh replacement in its slot.

        Order of operations:

        1. Rescue any tasks still in the worker's ledger (pushes them
           onto the Redis stream so sibling workers can consume them).
        2. If the process is still alive, send SIGTERM, wait
           ``SUPERVISOR_KILL_GRACE`` seconds, then SIGKILL if needed.
        3. ``join(timeout=5)`` to reclaim resources.
        4. Spawn a new ``mp.Process`` with the same ``worker_id``/name
           and replace the dead entry in ``self._job_list[idx]``.
        """
        # 1) Rescue first so the ledger is preserved even if the process
        # resists termination.
        try:
            self._rescue_tasks(worker_name)
        except Exception as rescue_err:  # pragma: no cover — defensive
            self.logger.exception(
                "Task rescue failed for %s: %s", worker_name, rescue_err
            )

        # 2) Terminate gracefully, then force.
        try:
            if process.is_alive():
                try:
                    process.terminate()
                except Exception as term_err:  # pragma: no cover
                    self.logger.error(
                        "terminate() failed for %s: %s",
                        worker_name,
                        term_err,
                    )
                # Grace period for SIGTERM to take effect.
                grace_end = time.monotonic() + self._kill_grace
                while time.monotonic() < grace_end:
                    if not process.is_alive():
                        break
                    time.sleep(0.2)
                if process.is_alive():
                    self.logger.warning(
                        "Worker %s did not exit after SIGTERM — sending "
                        "SIGKILL",
                        worker_name,
                    )
                    try:
                        process.kill()
                    except Exception as kill_err:  # pragma: no cover
                        self.logger.error(
                            "kill() failed for %s: %s",
                            worker_name,
                            kill_err,
                        )
        except Exception:  # pragma: no cover — defensive
            self.logger.exception(
                "Unexpected error during termination of %s", worker_name
            )

        # 3) Reap so the OS can reclaim the pid.
        try:
            process.join(timeout=5)
        except Exception as join_err:  # pragma: no cover — defensive
            self.logger.error(
                "join() failed for %s: %s", worker_name, join_err
            )

        # 4) Respawn in the same slot.
        new_process = self._respawn_worker(idx, worker_name)
        if new_process is not None:
            try:
                self._job_list[idx] = new_process
            except IndexError:  # pragma: no cover — defensive
                self._job_list.append(new_process)
