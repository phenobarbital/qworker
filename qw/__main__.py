"""Queue Worker server entry point."""
# import os
# import warnings
import asyncio
import argparse

# os.environ['PYTHONASYNCIODEBUG'] = '1'
# warnings.resetwarnings()

from .conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    WORKER_DEFAULT_QTY,
    WORKER_QUEUE_SIZE,
    WORKER_DISCOVERY_PORT

)
from .process import SpawnProcess
from .utils import cPrint
from .utils.events import enable_uvloop


def main():
    """Main Worker Function."""
    enable_uvloop()
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter
    )
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
        '--debug', action="store_true",
        default=False,
        help="Start workers in Debug Mode"
    )
    args = parser.parse_args()
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
        # log the unexpected error
        print(f"Unexpected error: {ex}")
        if process:
            process.terminate()
    finally:
        cPrint('Shutdown all workers ...', level='WARN')
        # print stack for all tasks
        loop.close()  # close the event loop


if __name__ == '__main__':
    main()
