import asyncio
import multiprocessing as mp
import resource as res
import subprocess
from collections.abc import Callable
import aioredis
from navconfig.logging import logging
from qw.discovery import get_server_discovery
from .utils.json import json_encoder
from .conf import NOFILES, WORKER_REDIS

from .server import start_server

QW_WORKER_LIST = 'QW_WORKER_LIST'
JOB_LIST = []

def raise_nofile(value: int = 4096) -> tuple[str, int]:
    """raise_nofile.

    sets nofile soft limit to at least 4096.
    """
    soft, hard = res.getrlimit(res.RLIMIT_NOFILE)
    # print(f'getting hard ulimit -n {soft} {hard}')
    if soft < value:
        soft = value

    if hard < soft:
        hard = soft
    try:
        print(f'setting soft & hard ulimit -n {soft} {hard}')
        res.setrlimit(res.RLIMIT_NOFILE, (soft, hard))
    except (ValueError, AttributeError) as err:
        logging.exception(err)
        try:
            ulimit = 'ulimit -{type} {value};'
            subprocess.Popen(ulimit.format(type='n', value=hard), shell=True)
        except Exception as e: # pylint: disable=W0703
            print('failed to set ulimit, giving up')
            logging.exception(e, stack_info=False)
    return 'nofile', (soft, hard)


class spawn_process(object):
    def __init__(self, args, event_loop):
        self.loop: asyncio.AbstractEventLoop = event_loop
        self.host: str = args.host
        self.port: int = args.port
        self.worker: str = args.wkname
        self.debug: bool = args.debug
        self.redis: Callable = None
        # increase the ulimit of server
        raise_nofile(value=NOFILES)
        for i in range(args.workers):
            p = mp.Process(
                target=start_server,
                name=f'{self.worker}_{i}',
                args=(i, args.host, args.port, args.debug, )
            )
            JOB_LIST.append(p)
            p.start()


    async def start_redis(self):
        # starting redis:
        try:
            self.redis = aioredis.ConnectionPool.from_url(
                    WORKER_REDIS,
                    decode_responses=True,
                    encoding='utf-8'
            )
        except Exception as e:
            raise Exception(e) from e

    async def stop_redis(self):
        try:
            await self.redis.disconnect(inuse_connections = True)
        except Exception as err: # pylint: disable=W0703
            logging.exception(err)

    async def register_worker(self):
        worker = {
            self.worker: (self.host, self.port)
        }
        conn = aioredis.Redis(connection_pool=self.redis)
        await conn.lpush(
            QW_WORKER_LIST,
            json_encoder(worker)
        )

    async def remove_worker(self):
        worker = {
            self.worker: (self.host, self.port)
        }
        conn = aioredis.Redis(connection_pool=self.redis)
        await conn.lrem(
            QW_WORKER_LIST,
            1,
            json_encoder(worker)
        )

    def start(self):
        try:
            # start a redis connection
            self.loop.run_until_complete(
                self.start_redis()
            )
            # register worker in the worker list
            self.loop.run_until_complete(
                self.register_worker()
            )
            self.loop.run_until_complete(
                get_server_discovery(event_loop=self.loop)
            )
        except Exception as err: # pylint: disable=W0703
            logging.error(err)

    def terminate(self):
        try:
            self.loop.run_until_complete(
                self.remove_worker()
            )
            self.loop.run_until_complete(
                self.stop_redis()
            )
        except Exception as err: # pylint: disable=W0703
            logging.exception(err)
        for j in JOB_LIST:
            j.terminate()
            j.join()
