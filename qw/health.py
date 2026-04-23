"""HTTP Health Check Server for Kubernetes readiness/liveness probes.

Runs a lightweight HTTP server using raw asyncio (no extra dependencies)
that exposes queue state so that an ALB/K8s can stop routing traffic
to workers whose queue is full.

Endpoints:
    GET /health/ready        - 200 if queue has capacity and the worker is
                               not draining, 503 otherwise.
    GET /health/live         - 200 always (process is alive).
    GET /health              - Alias for /health/ready.
    GET /supervisor/status   - 200 with a per-worker status snapshot
                               (heartbeat age, draining_since,
                               task_ledger_depth, queue_size). Intended
                               for Grafana dashboards. 503 if the shared
                               state is not wired to this HealthServer.
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from navconfig.logging import logging

from .queues import QueueManager
from datamodel.parsers.json import json_encoder


HTTP_200 = "200 OK"
HTTP_404 = "404 Not Found"
HTTP_503 = "503 Service Unavailable"


def _format_iso_utc(ts: float | None) -> str | None:
    """Format an epoch-seconds timestamp as ISO 8601 UTC ("Z").

    Returns ``None`` when ``ts`` is falsy (``None`` or 0). Used by the
    ``/supervisor/status`` endpoint so the response matches the spec
    example (``"2026-04-23T14:30:00Z"``).
    """
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(
            float(ts), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _http_response(status: str, body: str) -> bytes:
    """Build a minimal HTTP/1.1 response."""
    payload = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
        f"{body}"
    )
    return payload.encode("utf-8")


class HealthServer:
    """Minimal async HTTP server for health probes.

    Args:
        queue: QueueManager instance to inspect.
        host: Bind address (default ``0.0.0.0``).
        port: TCP port (default ``8080``).
        worker_name: Name shown in responses.
    """

    def __init__(
        self,
        queue: QueueManager,
        host: str = "0.0.0.0",
        port: int = 8080,
        worker_name: str = "",
        shared_state=None,
    ):
        self._queue = queue
        self._host = host
        self._port = port
        self._worker_name = worker_name
        # FEAT-005: optional Manager().dict() proxy — when present, the
        # readiness probe reports 503 for draining workers and the
        # /supervisor/status endpoint returns a per-worker snapshot.
        self._shared_state = shared_state
        self._server: Optional[asyncio.AbstractServer] = None
        self.logger = logging.getLogger("QW.HealthServer")

    # -- public API -----------------------------------------------------------

    async def start(self) -> None:
        """Start listening for HTTP health-check requests."""
        self._server = await asyncio.start_server(
            self._handle_request,
            host=self._host,
            port=self._port,
        )
        addr = self._server.sockets[0].getsockname()
        self.logger.info(
            f"Health server listening on {addr[0]}:{addr[1]}"
        )

    async def stop(self) -> None:
        """Gracefully shut down the health server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self.logger.info("Health server stopped")

    # -- internals ------------------------------------------------------------

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(
                reader.readline(), timeout=5.0
            )
            # Drain remaining headers (we don't need them)
            while True:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=5.0
                )
                if line in (b"\r\n", b"\n", b""):
                    break

            path = self._parse_path(request_line)
            status, body = self._route(path)

            writer.write(_http_response(status, body))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:
            self.logger.error(f"Health request error: {exc}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _parse_path(request_line: bytes) -> str:
        """Extract path from ``GET /path HTTP/1.1\\r\\n``."""
        try:
            parts = request_line.decode("utf-8", errors="replace").split()
            return parts[1] if len(parts) >= 2 else "/"
        except (IndexError, UnicodeDecodeError):
            return "/"

    def _route(self, path: str) -> tuple[str, str]:
        """Dispatch *path* to the appropriate handler."""
        if path in ("/health", "/health/ready"):
            return self._readiness()
        elif path == "/health/live":
            return self._liveness()
        elif path == "/supervisor/status":
            return self._supervisor_status()
        else:
            body = json_encoder({"error": "not found"})
            return HTTP_404, body

    def _readiness(self) -> tuple[str, str]:
        """Return 200 when the worker is ready to accept work, 503 otherwise.

        Order of checks:

        1. FEAT-005 draining guard — if this worker has been marked
           ``"draining"`` by the supervisor, return 503 immediately
           regardless of queue state so K8s/ALB stops routing.
        2. Queue capacity — 503 when the queue is at ceiling (base +
           grow_margin), otherwise 200.
        """
        # FEAT-005: draining takes precedence over queue-capacity
        # readiness — a draining worker must be pulled out of the load
        # balancer immediately even if its queue still has room.
        if self._shared_state is not None:
            try:
                worker_state = dict(
                    self._shared_state.get(self._worker_name, {})
                )
                if worker_state.get("status") == "draining":
                    body = json_encoder({
                        "status": "draining",
                        "worker": self._worker_name,
                        "draining_since": worker_state.get("draining_since"),
                    })
                    return HTTP_503, body
            except Exception as exc:  # pragma: no cover — defensive
                self.logger.debug(
                    "Draining status read failed: %s", exc
                )

        snap = self._queue.snapshot()
        # At ceiling when size >= ceiling (no more room to grow)
        at_ceiling = snap["size"] >= snap["ceiling"]

        body = json_encoder({
            "status": "full" if at_ceiling else "ok",
            "queue": {
                "size": snap["size"],
                "max_size": snap["max_size"],
                "base_size": snap["base_size"],
                "grow_margin": snap["grow_margin"],
                "grow_events": snap["grow_events"],
                "discard_events": snap["discard_events"],
                "full": snap["full"],
            },
            "worker": self._worker_name,
        })

        return (HTTP_503 if at_ceiling else HTTP_200), body

    def _liveness(self) -> tuple[str, str]:
        """Always 200 — the process is alive."""
        body = json_encoder({
            "status": "alive",
            "worker": self._worker_name,
        })
        return HTTP_200, body

    def _supervisor_status(self) -> tuple[str, str]:
        """Return a per-worker supervisor snapshot for Grafana dashboards.

        Response body shape::

            {
              "workers": {
                "<worker_name>": {
                  "pid": int,
                  "status": "healthy" | "draining",
                  "heartbeat_age_s": float | None,
                  "draining_since": str | None,      # ISO 8601 UTC ("Z"),
                  "draining_since_ts": float | None, # raw epoch seconds
                  "task_ledger_depth": int,
                  "queue_size": int
                }, ...
              }
            }

        ``draining_since`` is formatted per the spec example
        (``"2026-04-23T14:30:00Z"``). ``draining_since_ts`` preserves the
        raw epoch-seconds float so downstream tooling can do arithmetic
        without re-parsing the ISO string.

        Returns 503 when the HealthServer has no shared state reference —
        typical of older deployments or the ``shared_state=None`` path.
        """
        if self._shared_state is None:
            body = json_encoder({
                "error": "shared state not available",
                "workers": {},
            })
            return HTTP_503, body

        now = time.time()
        workers: dict[str, dict] = {}
        try:
            # Manager().dict() items() materializes a list of tuples.
            for name, raw_state in list(self._shared_state.items()):
                try:
                    state = dict(raw_state)
                except Exception:  # pragma: no cover — defensive
                    continue
                heartbeat = state.get("heartbeat", 0.0) or 0.0
                heartbeat_age = (
                    round(now - heartbeat, 1) if heartbeat > 0 else None
                )
                draining_since_ts = state.get("draining_since")
                draining_since_iso = _format_iso_utc(draining_since_ts)
                workers[name] = {
                    "pid": state.get("pid"),
                    "status": state.get("status", "unknown"),
                    "heartbeat_age_s": heartbeat_age,
                    "draining_since": draining_since_iso,
                    "draining_since_ts": draining_since_ts,
                    "task_ledger_depth": len(state.get("task_ledger", [])),
                    "queue_size": len(state.get("queue", [])),
                }
        except Exception as exc:  # pragma: no cover — defensive
            self.logger.error("Supervisor status read failed: %s", exc)
            body = json_encoder({"error": str(exc), "workers": {}})
            return HTTP_503, body

        body = json_encoder({"workers": workers})
        return HTTP_200, body
