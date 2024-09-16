from navconfig import config

def get_worker_list(workers: list):
    """Convert a list of workers in a tuple of worker:port for Scheduler."""
    wl = []
    for worker in workers:
        w, p = worker.split(':')
        wl.append((w, p))
    return wl

### Worker Configuration
QW_MAX_WORKERS = config.getint('QW_MAX_WORKERS', fallback=10)
MAX_WORKERS = config.getint('MAX_WORKERS', fallback=10)
WORKER_DEFAULT_HOST = config.get('WORKER_DEFAULT_HOST', fallback='0.0.0.0')
WORKER_DEFAULT_PORT = config.getint('WORKER_DEFAULT_PORT', fallback=8888)
WORKER_DEFAULT_QTY = config.getint('WORKER_DEFAULT_QTY', fallback=4)
WORKER_QUEUE_SIZE = config.getint('WORKER_QUEUE_SIZE', fallback=4)
RESOURCE_THRESHOLD = config.getint('RESOURCE_THRESHOLD', fallback=90)
CHECK_RESOURCE_USAGE = config.getboolean('CHECK_RESOURCE_USAGE', fallback=True)
WORKER_RETRY_INTERVAL = config.getint('WORKER_RETRY_INTERVAL', fallback=10)
WORKER_RETRY_COUNT = config.getint('WORKER_RETRY_COUNT', fallback=2)
WORKER_CONCURRENCY_NUMBER = config.getint('WORKER_CONCURRENCY_NUMBER', fallback=8)
WORKER_TASK_TIMEOUT = config.getint('WORKER_TASK_TIMEOUT', fallback=30)

## Queue Consumed Callback
WORKER_QUEUE_CALLBACK = config.get(
    'WORKER_QUEUE_CALLBACK', fallback=None
)

## ID for saving worker list on Redis
QW_WORKER_LIST = 'QW_WORKER_LIST'

## Network Discovery:
USE_DISCOVERY = config.getboolean('USE_DISCOVERY', fallback=False)
WORKER_DISCOVERY_HOST = config.get('WORKER_DISCOVERY_HOST')
WORKER_DISCOVERY_PORT = config.getint('WORKER_DISCOVERY_PORT', fallback=8434)
WORKER_USE_NAKED_IP = config.getboolean('WORKER_USE_NAKED_IP', fallback=False)
WORKER_DISCOVERY_BROADCAST = config.get('WORKER_DISCOVERY_BROADCAST', '255.255.255.255')
WORKER_DEFAULT_MULTICAST = config.get(
    'WORKER_DEFAULT_MULTICAST', fallback="239.255.255.250"
)
## Word used by Discovery
expected_message = config.get('WORKER_DISCOVERY_MESSAGE')
WORKER_SECRET_KEY = config.get('WORKER_SECRET_KEY')


### Redis Transport
REDIS_HOST = config.get('REDIS_HOST', fallback='localhost')
REDIS_PORT = config.getint('REDIS_PORT', fallback=6379)
REDIS_WORKER_DB = config.getint('REDIS_WORKER_DB', fallback=4)
WORKER_USE_STREAMS = config.getboolean('WORKER_USE_STREAMS', fallback=True)
REDIS_WORKER_GROUP = config.get('REDIS_WORKER_CHANNEL', fallback='QWorkerGroup')
REDIS_WORKER_STREAM = config.get('REDIS_WORKER_STREAM', fallback='QWorkerStream')

WORKER_REDIS = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_WORKER_DB}"

WORKERS = [e.strip() for e in list(config.get(
    'WORKER_LIST', fallback='127.0.0.1:8181').split(","))]
WORKER_LIST = get_worker_list(WORKERS)

HIGH_LIST = [e.strip() for e in list(config.get(
    'WORKER_HIGH_LIST', fallback='127.0.0.1:8899').split(","))]
WORKER_HIGH_LIST = get_worker_list(HIGH_LIST)

# upgrade no-files
NOFILES = config.getint('ULIMIT_NOFILES', fallback=65535)

PACKAGE_LIST = config.getlist(
    'PACKAGE_LIST', fallback=('asyncdb', 'qw', 'querysource', 'navconfig')
)

## Telegram:
# Telegram credentials
TELEGRAM_BOT_TOKEN = config.get("TELEGRAM_BOT_TOKEN")

try:
    from settings.settings import (
        WORKER_LIST,
        WORKER_HIGH_LIST,
        WORKER_REDIS,
        WORKER_DEFAULT_MULTICAST,
        WORKER_DEFAULT_HOST,
        WORKER_DEFAULT_PORT,
        PACKAGE_LIST
    )  # pylint: disable=W0611
except ImportError:
    pass
