"""Queue Worker server entry point."""
import asyncio
import argparse
import sys

from qw.conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    NOTIFY_DEFAULT_PORT,
    WORKER_DEFAULT_QTY,
    WORKER_QUEUE_SIZE,
    WORKER_DISCOVERY_PORT
)
from .process import SpawnProcess
from .utils import cPrint
from .utils.events import enable_uvloop


# ---------------------------------------------------------------------------
# Server start action
# ---------------------------------------------------------------------------

def _add_start_args(parser: argparse.ArgumentParser) -> None:
    """Add all server-start arguments to a parser (shared by root and start sub)."""
    parser.add_argument(
        '--host', dest='host', type=str,
        default=WORKER_DEFAULT_HOST,
        help='set server host'
    )
    parser.add_argument(
        '--port', dest='port', type=int,
        default=WORKER_DEFAULT_PORT,
        help='set server port'
    )
    parser.add_argument(
        '--notify_host', dest='notify_host', type=str,
        default=WORKER_DEFAULT_HOST,
        help='Set Notify host'
    )
    parser.add_argument(
        '--notify_port', dest='notify_port', type=int,
        default=NOTIFY_DEFAULT_PORT,
        help='Set Notify Port'
    )
    parser.add_argument(
        '--workers', dest='workers', type=int,
        default=WORKER_DEFAULT_QTY,
        help='max number of workers'
    )
    parser.add_argument(
        '--queue', dest='queue', type=int,
        default=WORKER_QUEUE_SIZE,
        help='Size of Queue on Worker'
    )
    parser.add_argument(
        '--wkname', dest='wkname', type=str,
        default='Worker',
        help='Worker Name'
    )
    parser.add_argument(
        '--enable-discovery', dest='enable_discovery',
        type=str.lower,
        choices=["true", "false"],
        default='true',
        help='Start Discovery Service on this Worker'
    )
    parser.add_argument(
        '--discovery', dest='discovery', type=str,
        default=WORKER_DISCOVERY_PORT,
        help='UDP Port for Service discovery'
    )
    parser.add_argument(
        '--enable_notify', action="store_true",
        default=True,
        help="Enable Notify Worker."
    )
    parser.add_argument(
        '--notify_empty', action="store_true",
        default=False,
        help="Notify when Redis Stream is Empty."
    )
    parser.add_argument(
        '--debug', action="store_true",
        default=False,
        help="Start workers in Debug Mode"
    )


def run_start(args: argparse.Namespace) -> None:
    """Start the QWorker server process."""
    process = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cPrint('::: Starting Workers ::: ')
        process = SpawnProcess(args)
        process.start()
        loop.run_forever()
    except KeyboardInterrupt:
        if process:
            process.terminate()
    except Exception as ex:
        print(f"Unexpected error: {ex}")
        if process:
            process.terminate()
    finally:
        cPrint('Shutdown all workers ...', level='WARN')
        try:
            loop.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Info action
# ---------------------------------------------------------------------------

def run_info(args: argparse.Namespace) -> None:
    """Connect to a running qworker and display task state."""
    from .cli.info import execute_info
    execute_info(args)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """Main Worker Function.

    Backward compatible: `qw` with no subcommand (or with server flags like
    --host, --port, --workers) starts the server. `qw info` queries state.
    """
    enable_uvloop()

    # Build main parser with subparsers
    parser = argparse.ArgumentParser(
        prog='qw',
        description='QueueWorker — Asynchronous Task Queue System',
        formatter_class=argparse.RawTextHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command')

    # 'start' subcommand — explicit server start
    start_parser = subparsers.add_parser(
        'start',
        help='Start the QWorker server',
        formatter_class=argparse.RawTextHelpFormatter
    )
    _add_start_args(start_parser)
    start_parser.set_defaults(func=run_start)

    # 'info' subcommand
    info_parser = subparsers.add_parser(
        'info',
        help='Query the state of a running QWorker',
        formatter_class=argparse.RawTextHelpFormatter
    )
    info_parser.add_argument(
        '--host', dest='host', type=str,
        default=WORKER_DEFAULT_HOST,
        help='Worker host to query (default: %(default)s)'
    )
    info_parser.add_argument(
        '--port', dest='port', type=int,
        default=WORKER_DEFAULT_PORT,
        help='Worker port to query (default: %(default)s)'
    )
    info_parser.add_argument(
        '--json', dest='json_output', action='store_true',
        default=False,
        help='Output raw JSON instead of formatted table'
    )
    info_parser.add_argument(
        '--watch', dest='watch', type=int, default=None,
        metavar='N',
        help='Poll every N seconds (continuous monitoring)'
    )
    info_parser.set_defaults(func=run_info)

    # Also add server args to root parser for backward compatibility.
    # When no subcommand is given, old-style `qw --host H --port P` still works.
    _add_start_args(parser)

    args = parser.parse_args()

    if args.command is None:
        # No subcommand → start server (backward compatible behavior)
        run_start(args)
    else:
        args.func(args)


if __name__ == '__main__':
    main()
