"""HTTP Health Check Server for Kubernetes readiness/liveness probes.

Runs a lightweight HTTP server using raw asyncio (no extra dependencies)
that exposes queue state so that an ALB/K8s can stop routing traffic
to workers whose queue is full.

Endpoints:
    GET /health/ready  - 200 if queue has capacity, 503 if full.
    GET /health/live   - 200 always (process is alive).
    GET /health        - Alias for /health/ready.
"""
import asyncio
from typing import Optional

from navconfig.logging import logging

from .queues import QueueManager
from datamodel.parsers.json import json_encoder


HTTP_200 = "200 OK"
HTTP_404 = "404 Not Found"
HTTP_503 = "503 Service Unavailable"


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
    ):
        self._queue = queue
        self._host = host
        self._port = port
        self._worker_name = worker_name
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
        else:
            body = json_encoder({"error": "not found"})
            return HTTP_404, body

    def _readiness(self) -> tuple[str, str]:
        """Return 200 when the queue can accept work, 503 otherwise.

        Returns 503 only when the queue is at ceiling (base + grow_margin),
        so a queue that is 'full' at base_size still returns 200 when grow
        margin is available.
        """
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
