"""Queue Worker server entry point."""
import asyncio
import argparse

from qw.conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    NOTIFY_DEFAULT_PORT,
    WORKER_DEFAULT_QTY,
    WORKER_QUEUE_SIZE,
    WORKER_DISCOVERY_PORT,
    WORKER_HEALTH_PORT,
)
from .process import SpawnProcess
from .utils import cPrint
from .utils.events import enable_uvloop


def _add_start_args(parser: argparse.ArgumentParser) -> None:
    """Register all arguments for the `start` (default) subcommand."""
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
    parser.add_argument(
        '--health-port', dest='health_port', type=int,
        default=WORKER_HEALTH_PORT,
        help='HTTP port for health check endpoint (K8s probes)'
    )


def run_start(args: argparse.Namespace) -> None:
    """Start the QWorker server processes."""
    enable_uvloop()
    process = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cPrint('::: Starting Workers ::: ')
        process = SpawnProcess(args)
        process.start()
        loop.run_forever()
    except KeyboardInterrupt:
        process.terminate()
    except Exception as ex:
        print(f"Unexpected error: {ex}")
        if process:
            process.terminate()
    finally:
        cPrint('Shutdown all workers ...', level='WARN')
        loop.close()


def run_info(args: argparse.Namespace) -> None:
    """Fetch and display real-time task-state from a running QWorker."""
    from .cli.info import execute_info
    execute_info(args)


def main():
    """Main Worker Function."""
    root_parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='QWorker — distributed async task worker'
    )
    subparsers = root_parser.add_subparsers(dest='command')

    # --- start subcommand (default) ---
    start_parser = subparsers.add_parser(
        'start',
        help='Start the QWorker server (default when no subcommand is given)',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    _add_start_args(start_parser)

    # --- info subcommand ---
    info_parser = subparsers.add_parser(
        'info',
        help='Show real-time task state of a running QWorker',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    info_parser.add_argument(
        '--host', dest='host', type=str,
        default='127.0.0.1',
        help='QWorker host to connect to (default: 127.0.0.1)'
    )
    info_parser.add_argument(
        '--port', dest='port', type=int,
        default=WORKER_DEFAULT_PORT,
        help='QWorker TCP port (default: %(default)s)'
    )
    info_parser.add_argument(
        '--watch', dest='watch', type=int,
        default=0,
        metavar='SECONDS',
        help='Refresh every N seconds (0 = run once)'
    )
    info_parser.add_argument(
        '--json', dest='json_output', action='store_true',
        default=False,
        help='Output raw JSON instead of a rich table'
    )

    # Parse; if no subcommand given, fall back to "start"
    args, remaining = root_parser.parse_known_args()
    if args.command is None:
        # Backward-compat: treat unknown args as start-subcommand args
        start_parser.set_defaults(command='start')
        args = start_parser.parse_args(remaining)
        args.command = 'start'

    if args.command == 'info':
        run_info(args)
    else:
        run_start(args)


if __name__ == '__main__':
    main()
