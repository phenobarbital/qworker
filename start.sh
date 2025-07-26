#!/bin/sh

REDIS_HOST="$REDIS_HOST"
REDIS_PORT="6379"
QW_WORKER_LIST="QW_WORKER_LIST"
DASK_SCHEDULER_IP="$DASK_SCHEDULER_IP"

redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ltrim "$QW_WORKER_LIST" 1 0

HOSTNAME=$(hostname)
# Start QW in the background:
ulimit -Sn 65535 && qw --port=8888 --notify_port=8989 --wkname=$HOSTNAME --debug > qw.log 2>&1 &

# Start the Dask worker in the background
dask-worker $DASK_SCHEDULER_IP --nprocs 1 --nthreads 2 > dask-worker.log 2>&1 &

# Start the Gunicorn server
# ulimit -Sn 32766 && gunicorn nav:navigator -c gunicorn_config.py -k aiohttp.GunicornUVLoopWebWorker --log-level debug --preload > gunicorn.log 2>&1
