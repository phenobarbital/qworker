import asyncio
import uuid
import multiprocessing as mp
import resource as res
import subprocess
from collections.abc import Callable
import socket
from redis import asyncio as aioredis
from navconfig.logging import logging
from qw.exceptions import ConfigError
from .utils.json import json_encoder
from .conf import (
    NOFILES,
    WORKER_REDIS,
    QW_WORKER_LIST,
    WORKER_DISCOVERY_PORT,
    WORKER_USE_NAKED_IP,
    QW_MAX_WORKERS
)

from .server import start_server

JOB_LIST = []

def raise_nofile(value: int = 4096) -> tuple[str, int]:
    """raise_nofile.

    sets nofile soft limit to at least 4096.
    """
    soft, hard = res.getrlimit(res.RLIMIT_NOFILE)
    if soft < value:
        soft = value

    if hard < soft:
        hard = soft
    try:
        ## print(f'Setting soft & hard ulimit -n {soft} {hard}')
        res.setrlimit(res.RLIMIT_NOFILE, (soft, hard))
    except (ValueError, AttributeError) as err:
        logging.exception(err)
        try:
            ulimit = 'ulimit -{type} {value};'
            subprocess.Popen(ulimit.format(type='n', value=hard), shell=True)
        except Exception as e:  # pylint: disable=W0703
            print('Failed to set ulimit, giving up')
            logging.exception(e, stack_info=False)
    return 'nofile', (soft, hard)


def is_port_available(host, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.close()
        return True
    except OSError:
        return False

class SpawnProcess(object):
    def __init__(self, args):
        try:
            self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        except RuntimeError:
            raise
        self.host: str = args.host
        self.hostname = socket.gethostname()
        self.address = socket.gethostbyname(self.hostname)
        self.id = str(uuid.uuid4())
        self.port: int = args.port
        self.worker: str = f"{args.wkname}-{args.port}"
        self.debug: bool = args.debug
        self.redis: Callable = None
        # increase the ulimit of server
        raise_nofile(value=NOFILES)
        ## Logger:
        self.logger = logging.getLogger(
            name='QW.WorkerProcess'
        )
        if args.workers > QW_MAX_WORKERS:
            raise ConfigError(
                f"Max Number of Workers exceeded: {args.workers}, exiting."
            )
        if not is_port_available(args.host, args.port):
            raise RuntimeError(
                "QW Error: Port is already in use"
            )
        for i in range(args.workers):
            try:
                p = mp.Process(
                    target=start_server,
                    name=f'{self.worker}_{i}',
                    args=(i, args.host, args.port, args.debug, )
                )
                JOB_LIST.append(p)
                p.start()
            except (OSError, IOError) as ex:
                self.logger.error(
                    f"Error Dispatching Worker: {ex}"
                )
                raise

    async def start_redis(self):
        # starting redis:
        try:
            self.redis = aioredis.ConnectionPool.from_url(
                WORKER_REDIS,
                decode_responses=True,
                encoding='utf-8'
            )
        except Exception as ex:
            self.logger.exception(ex)
            raise

    async def stop_redis(self):
        try:
            conn = aioredis.Redis(connection_pool=self.redis)
            await conn.delete(
                QW_WORKER_LIST
            )
            await self.redis.disconnect(inuse_connections=True)
        except Exception as err:  # pylint: disable=W0703
            self.logger.exception(err)

    async def register_worker(self):
        try:
            if WORKER_USE_NAKED_IP is True:
                address = (self.address, self.port)
            else:
                address = (self.hostname, self.port)
            worker = json_encoder({
                self.id: address
            })
            conn = aioredis.Redis(connection_pool=self.redis)
            await conn.lpush(
                QW_WORKER_LIST,
                worker
            )
        except aioredis.ConnectionError as err:
            self.logger.error(
                f"Redis connection error: {err}"
            )
            raise
        # self-registration into Discovery Service:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv_addr = ('', WORKER_DISCOVERY_PORT)
        try:
            sock.sendto(worker.encode(), srv_addr)
        except socket.timeout as ex:
            logging.error(
                f"Unable to register Worker on this Network: {ex}"
            )

    async def remove_worker(self):
        if WORKER_USE_NAKED_IP is True:
            address = (self.address, self.port)
        else:
            address = (self.hostname, self.port)
        worker = json_encoder({
            self.id: address
        })
        conn = aioredis.Redis(connection_pool=self.redis)
        await conn.lrem(
            QW_WORKER_LIST,
            1,
            worker
        )
        # adding remove capability on Worker List:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv_addr = ('', WORKER_DISCOVERY_PORT)
        try:
            sock.sendto(worker.encode(), srv_addr)
        except socket.timeout as ex:
            logging.error(
                f"Unable to register Worker on this Network: {ex}"
            )

    def start(self):
        try:
            # start a redis connection
            self.loop.run_until_complete(
                self.start_redis()
            )
        except aioredis.ConnectionError as err:
            self.logger.error(
                f"Redis connection error: {err}"
            )
            raise
        except Exception as err:
            self.logger.error(
                f"Unexpected error when starting Redis: {err}"
            )
            raise
        try:
            # register worker in the worker list
            self.loop.run_until_complete(
                self.register_worker()
            )
        except asyncio.TimeoutError as err:
            self.logger.error(
                f"Timeout error when registering worker: {err}"
            )
            raise
        except Exception as err:
            self.logger.error(
                f"Unexpected error when registering worker: {err}"
            )
            raise

    def terminate(self):
        for j in JOB_LIST:
            try:
                j.terminate()
            except (OSError, AssertionError) as ex:
                self.logger.error(ex)
            try:
                j.join()
            except TypeError as ex:
                self.logger.error(ex)
        try:
            self.loop.run_until_complete(
                self.remove_worker()
            )
            self.loop.run_until_complete(
                self.stop_redis()
            )
        except asyncio.TimeoutError as ex:
            self.logger.warning(
                f"Timeout error: {ex}"
            )
        except asyncio.CancelledError as exc:
            self.logger.warning(str(exc))
        except Exception as err:  # pylint: disable=W0703
            self.logger.exception(err)
            raise
