import subprocess
import sys
from navconfig import config
from qw.conf import (
    WORKER_DEFAULT_PORT,
    DASK_SCHEDULER
)

def start_qw():
    return subprocess.Popen(
        ['qw', f'--port={WORKER_DEFAULT_PORT}', '--debug'],
    )

def start_dask_worker():
    return subprocess.Popen(
        ['dask', 'worker', DASK_SCHEDULER, '--nworkers', '1', '--nthreads', '2'],
    )

def start_gunicorn():
    return subprocess.Popen([
        'gunicorn',
        'nav:navigator',
        '-c',
        'gunicorn_config.py',
        '-k',
        'aiohttp.GunicornUVLoopWebWorker',
        '--log-level',
        'debug',
        '--preload'
    ])

def main():
    # Start services
    qw_process = start_qw()
    dask_worker_process = start_dask_worker()
    # gunicorn_process = start_gunicorn()
    try:
        # Wait for processes to complete
        qw_process.wait()
        dask_worker_process.wait()
        # gunicorn_process.wait()
    except KeyboardInterrupt:
        # Terminate processes on interrupt
        qw_process.terminate()
        dask_worker_process.terminate()
        # gunicorn_process.terminate()
        sys.exit(0)

if __name__ == '__main__':
    main()
