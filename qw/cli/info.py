"""qw info — CLI command for querying QWorker task state.

Connects to a running qworker via TCP 'info' command, receives JSON response,
and renders either a rich table or raw JSON output.

Usage:
    qw info --host 127.0.0.1 --port 8888
    qw info --host 127.0.0.1 --port 8888 --json
    qw info --host 127.0.0.1 --port 8888 --watch 5
"""
import asyncio
import json
import os
import sys
import time
import argparse
from datetime import datetime
from typing import Optional

# Rich is a required dependency — fail clearly if not installed
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
except ImportError as exc:
    raise ImportError(
        "The 'rich' package is required for 'qw info'. "
        "Install it with: pip install rich"
    ) from exc

from qw.conf import WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT

console = Console()


# ---------------------------------------------------------------------------
# TCP fetch
# ---------------------------------------------------------------------------

async def _fetch_info_async(host: str, port: int) -> dict:
    """Connect to a QWorker TCP port and fetch the 'info' response.

    Sends the raw 'info' command (same pattern as 'health').
    Localhost connections are served without auth per the spec.

    Returns:
        dict: Parsed JSON response from the worker.

    Raises:
        ConnectionRefusedError: If the worker is not reachable.
        ValueError: If the response cannot be parsed as JSON.
    """
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except (ConnectionRefusedError, OSError) as exc:
        raise ConnectionRefusedError(
            f"Cannot connect to qworker at {host}:{port} — {exc}"
        ) from exc

    try:
        # Send the 'info' command as a newline-terminated bytes string.
        # The server reads via reader.readline(), so we must end with \n.
        writer.write(b'info\n')
        await writer.drain()

        # Read the response
        raw = b''
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            raw += chunk
            if reader.at_eof():
                break
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    if not raw:
        raise ValueError("Empty response from worker")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON response from worker: {exc}") from exc


def fetch_info(host: str, port: int) -> dict:
    """Synchronous wrapper around _fetch_info_async."""
    return asyncio.run(_fetch_info_async(host, port))


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------

def _fmt_ts(ts: Optional[float]) -> str:
    """Format a Unix timestamp float as a human-readable datetime string."""
    if ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return str(ts)


def _fmt_duration(secs: Optional[float]) -> str:
    """Format a duration in seconds as a human-readable string."""
    if secs is None:
        return "—"
    try:
        secs = float(secs)
        if secs < 60:
            return f"{secs:.2f}s"
        minutes = int(secs // 60)
        remaining = secs % 60
        return f"{minutes}m {remaining:.0f}s"
    except Exception:
        return str(secs)


# ---------------------------------------------------------------------------
# Rich table renderer
# ---------------------------------------------------------------------------

def render_text(data: dict) -> None:
    """Render the worker state as rich tables grouped by source."""
    worker = data.get("worker", {})
    state = data.get("state", {})

    # Worker header
    name = worker.get("name", "unknown")
    pid = worker.get("pid", "?")
    address = worker.get("address", ("?", "?"))
    serving = worker.get("serving", "?")
    is_dead = state.get("dead", False)

    status_text = "[bold red]DEAD[/bold red]" if is_dead else "[bold green]ALIVE[/bold green]"
    console.print(
        Panel(
            f"[bold cyan]{name}[/bold cyan]  pid=[yellow]{pid}[/yellow]  "
            f"addr=[white]{address}[/white]  status={status_text}",
            title="[bold]QWorker State[/bold]",
            expand=False
        )
    )

    # Task sections
    sections = [
        ("Asyncio Queue", state.get("queue", []), "queue"),
        ("Direct TCP", state.get("tcp_executing", []), "tcp"),
        ("Redis Streams", state.get("redis_executing", []), "redis"),
        ("Broker (RabbitMQ)", state.get("broker_executing", []), "broker"),
    ]

    for section_title, tasks, _source in sections:
        _render_task_table(section_title, tasks)

    # Completed tasks
    completed = state.get("completed", [])
    _render_completed_table(completed)


def _render_task_table(title: str, tasks: list) -> None:
    """Render an active task table."""
    table = Table(
        title=f"[bold]{title}[/bold]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        expand=False
    )
    table.add_column("Task ID", style="dim", max_width=16)
    table.add_column("Function", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Enqueued", justify="right")
    table.add_column("Started", justify="right")
    table.add_column("Retries", justify="right")

    if not tasks:
        table.add_row("[dim]—[/dim]", "[dim]empty[/dim]", "", "", "", "")
    else:
        for task in tasks:
            tid = str(task.get("task_id", ""))[:12] + "…" if len(str(task.get("task_id", ""))) > 12 else str(task.get("task_id", ""))
            fn = task.get("function_name", "")
            status = task.get("status", "")
            status_style = {
                "queued": "[yellow]queued[/yellow]",
                "executing": "[green]executing[/green]",
            }.get(status, status)
            table.add_row(
                tid,
                fn or "[dim]unknown[/dim]",
                status_style,
                _fmt_ts(task.get("enqueued_at")),
                _fmt_ts(task.get("started_at")),
                str(task.get("retries", 0))
            )

    console.print(table)


def _render_completed_table(completed: list) -> None:
    """Render the completed tasks ring buffer."""
    table = Table(
        title="[bold]Completed Tasks (last 10)[/bold]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        expand=False
    )
    table.add_column("Task ID", style="dim", max_width=16)
    table.add_column("Function", style="cyan")
    table.add_column("Source", justify="center")
    table.add_column("Result", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Completed", justify="right")

    if not completed:
        table.add_row("[dim]—[/dim]", "[dim]none yet[/dim]", "", "", "", "")
    else:
        for task in reversed(completed):  # Most recent first
            tid = str(task.get("task_id", ""))[:12] + "…" if len(str(task.get("task_id", ""))) > 12 else str(task.get("task_id", ""))
            result = task.get("result", "")
            result_style = {
                "success": "[green]✓ success[/green]",
                "error": "[red]✗ error[/red]",
            }.get(result, result)
            table.add_row(
                tid,
                task.get("function_name", "") or "[dim]unknown[/dim]",
                task.get("source", ""),
                result_style,
                _fmt_duration(task.get("duration")),
                _fmt_ts(task.get("completed_at"))
            )

    console.print(table)


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------

def render_json(data: dict) -> None:
    """Output the worker state as formatted JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def execute_info(args: argparse.Namespace) -> None:
    """Main orchestration for `qw info` subcommand.

    Fetches worker state and renders it. If --watch N, loops every N seconds.
    """
    host = getattr(args, 'host', WORKER_DEFAULT_HOST)
    port = getattr(args, 'port', WORKER_DEFAULT_PORT)
    json_output = getattr(args, 'json_output', False)
    watch = getattr(args, 'watch', None)

    def _once() -> None:
        try:
            data = fetch_info(host, port)
        except ConnectionRefusedError as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            sys.exit(1)
        except ValueError as exc:
            console.print(f"[bold red]Parse error:[/bold red] {exc}")
            sys.exit(1)

        if json_output:
            render_json(data)
        else:
            render_text(data)

    if watch is None:
        _once()
        return

    # --watch N: poll every N seconds with clear-screen refresh
    try:
        while True:
            if not json_output:
                os.system('clear')
            _once()
            if not json_output:
                console.print(
                    f"[dim]Refreshing every {watch}s — press Ctrl+C to stop[/dim]"
                )
            time.sleep(watch)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
