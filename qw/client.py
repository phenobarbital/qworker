"""QueueWorker Client."""
import asyncio
import itertools
import random
import warnings
from typing import Any
from collections.abc import Callable
from collections import defaultdict
from functools import partial
import aioredis
import cloudpickle
import jsonpickle
import orjson
import uvloop
# from dataintegration.utils.parserqs import is_parseable
from navconfig.logging import logging
from qw.discovery import get_client_discovery
from qw.utils import make_signature
from qw.exceptions import (
    ParserError,
    QWException
)
from .conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    WORKER_REDIS,
    USE_DISCOVERY,
    WORKER_SECRET_KEY,
    expected_message
)
from .process import QW_WORKER_LIST
from .wrappers import FuncWrapper, TaskWrapper


asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
uvloop.install()


MAX_RETRY_COUNT = 5
WAIT_TIME = 0.1  # seconds


# TODO: select a non-busy Worker
def random_worker(workers):
    """Picks next worker uniformly at random."""
    while True:
        try:
            yield random.choice(workers)
        except (ValueError, TypeError) as ex:
            raise Exception(
                "Cannot launch Work on Empty Worker List"
            ) from ex

def round_robin_worker(workers):
    """Pick next worker based on Round Robin."""
    while True:
        try:
            return next(workers)
        except (ValueError, TypeError) as ex:
            raise Exception(
                f"Bad Worker list: {ex}"
            ) from ex
        except StopIteration:
            break

class QClient:
    """
    Queue Task Worker Client.

    Attributes:
        _workers: Collection of available workers specified by
                  hostnames and ports.
        _scheduler: Generator that returns host and port of selected worker.
        _retries_per_server: Defaultdict that stores number of retries for
                             each task queue server.
    """
    timeout: int = 5
    redis: Callable = None

    def __init__(self, worker_list: list = None, timeout: int = 5):
        self._loop = asyncio.get_event_loop()
        ## check if we use network discovery or redis list:
        if worker_list:
            self._worker_list = itertools.cycle(worker_list)
        else:
            if USE_DISCOVERY is True:
                # get worker list from discovery:
                self._worker_list = get_client_discovery()
            else:
                # starting redis:
                self.redis = aioredis.ConnectionPool.from_url(
                        WORKER_REDIS,
                        decode_responses=True,
                        encoding='utf-8'
                )
                # getting the list from redis
                self._worker_list = self.get_workers()
        if not self._worker_list:
            logging.warning(
                'EMPTY WORKER LIST: Trying to connect to a default Worker'
            )
            # try to connect with the default worker
            self._worker_list = [(WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT)]
        self._num_retries = defaultdict(int)
        self._workers = round_robin_worker(self._worker_list)
        self.timeout = timeout

    def discover_workers(self):
        if USE_DISCOVERY is True:
            self._worker_list = get_client_discovery()

    def get_workers(self):
        async def get_workers_list():
            conn = aioredis.Redis(connection_pool=self.redis)
            self._worker_list = []
            l = await conn.lrange(QW_WORKER_LIST, 0, 100)
            if l:
                w = [orjson.loads(el) for el in l]
                self._worker_list = [tuple(list(v.values())[0]) for v in w]
            return itertools.cycle(self._worker_list)
        return self._loop.run_until_complete(get_workers_list())

    def get_servers(self) -> list:
        return self._worker_list

    def register_pickle_module(self, module: Any):
        cloudpickle.register_pickle_by_value(module)

    async def validate_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> tuple:
        signature = make_signature(expected_message, WORKER_SECRET_KEY)
        writer.write(b'%d\n' % len(signature))
        writer.write(signature)
        # sending data to worker:
        await writer.drain()
        response = None
        response = await reader.readexactly(8)
        if response == b'CONTINUE':
            return [reader, writer]
        else:
            response = cloudpickle.loads(response)
            if isinstance(response, BaseException):
                raise response


    async def get_connection(self):
        retries = {}
        reader = None
        writer = None
        if not self._worker_list:
            # try discover again a worker list:
            self.discover_workers()
        while True:
            worker = round_robin_worker(self._worker_list)
            logging.debug(f':: WORKER SELECTED: {worker!r}')
            if not worker:
                raise ConnectionAbortedError(
                    "Error: There is no workers to work with."
                )
            try:
                task = asyncio.open_connection(
                    *worker
                )
                reader, writer = await asyncio.wait_for(
                    task, timeout=self.timeout
                )
                retries[worker] = 0
                # check the signature between server and client:
                return await self.validate_connection(
                    reader, writer
                )
            except asyncio.TimeoutError:
                # removing this worker from the self workers
                if len(self._workers) > 0:
                    self._workers.remove(worker)
                warnings.warn(f"Timeout, skipping {worker!r}")
                await asyncio.sleep(WAIT_TIME)
            except ConnectionRefusedError as ex:
                # connection returns timeout, retry with another worker
                self._num_retries[worker] += 1
                if self._num_retries[worker] > MAX_RETRY_COUNT:
                    raise ConnectionError(
                        f'Max number of retries reached: {worker!r}: {ex}'
                    ) from ex
                warnings.warn(
                    f"Can't connect to {worker!r}. Retrying..."
                )
                await asyncio.sleep(WAIT_TIME)
                continue
            except OSError as err:
                self._num_retries[worker] += 1
                if self._num_retries[worker] > MAX_RETRY_COUNT:
                    raise ConnectionError(
                        f'Max number of retries is reached for {worker!r}, error: {err!s}'
                    ) from err
                warnings.warn(
                    f"Can't connect to {worker!r}. Retrying..."
                )
                await asyncio.sleep(WAIT_TIME)
                continue
            except Exception as err:
                warnings.warn(
                    f'Unexpected Error on Queue Client: {err!s}'
                )
                raise

    async def close(self, writer: asyncio.StreamWriter):
        if writer.can_write_eof():
            writer.write_eof()
        await writer.drain()
        try:
            if self.redis:
                await self.redis.disconnect(inuse_connections = True)
        except (AttributeError, ConnectionError) as err:
            logging.error(err)
        logging.debug('Closing Socket')
        writer.close()

    async def run(self, fn: Any, *args, use_wrapper: bool = False, **kwargs):
        """Runs a function in Queue Worker

        Run function in the Queue Worker, returns the result or raises exception.

        Args:
            fn: Any Function, object or callable to be send to Worker.
            args: any non-keyword arguments
            use_wrapper: (bool) wraps function into a Function Wrapper.
            kwargs: keyword arguments.

        Returns:
            Function Result.

        Raises:
            ConfigError: bad instructions to Worker Client.
            ConnectionError: unable to connect to Worker.
            Exception: Any Unhandled error.
        """
        try:
            reader, writer = await self.get_connection()
        except ConnectionError as ex:
            raise ConnectionError(
                f"Unable to Connect to Queue Worker: {ex}"
            ) from ex
        except Exception as err:
            logging.error(err)
            raise

        host, *_ = writer.get_extra_info('sockname')
        # wrapping the function into Task Wrapper
        logging.debug(f'Sending Object {fn!s} to Worker {host}')
        if isinstance(fn, (TaskWrapper, FuncWrapper)):
            # already wrapped
            func = fn
            func.queued = False
        elif use_wrapper is True:
            # wrap function into a Wrapper:
            func = FuncWrapper(
                host,
                fn,
                *args,
                **kwargs
            )
            func.queued = False
        else:
            # sent function *as is*
            func = partial(fn, *args, **kwargs)
        try:
            serialized_task = cloudpickle.dumps(func)
            writer.write(serialized_task)
            # sending data to worker:
            if writer.can_write_eof():
                writer.write_eof()
            await writer.drain()
        except Exception as err:
            logging.error(f'Error Serializing Task: {err!s}')
            raise
        # Then, got the result:
        serialized_result = None
        try:
            while True:
                serialized_result = await reader.read(-1)
                if reader.at_eof():
                    break
        except Exception as ex:
            raise QWException(
                f"Error getting results from Worker: {ex}"
            ) from ex
        finally:
            await self.close(writer)
        try:
            task_result = cloudpickle.loads(serialized_result)
            logging.debug(
                f'Data Received: {task_result!r}'
            )
            try:
                if isinstance(task_result, str):
                    result = jsonpickle.decode(task_result)
                else:
                    result = task_result
            except (TypeError, ValueError):
                result = task_result
            task_result = result
        except (ValueError, TypeError) as ex:
            raise ParserError(
                f"Error Parsing serialized results: {ex}"
            ) from ex
        except EOFError as err:
            logging.exception(f'No data was received from Server: {err!s}')
            task_result = None
        except Exception as err: # pylint: disable=W0703
            logging.exception(f'Error receiving data from Worker Server: {err!s}')
            task_result = None
        if isinstance(task_result, BaseException):
            # raise task_result
            return task_result
        elif isinstance(task_result, list):
            # try to convert into object:
            res = []
            for el in task_result:
                try:
                    a = orjson.loads(el)
                    res.append(a)
                except (TypeError, ValueError) as err:
                    res.append(el)
            return res
        # elif isinstance(task_result, str):
        #     if newval:=is_parseable(task_result):
        #         val = newval(task_result)
        #         if isinstance(val, list):
        #             new_val = []
        #             for el in val:
        #                 if nval:=is_parseable(el):
        #                     new_val.append(nval(el))
        #                 else:
        #                     new_val.append(el)
        #             val = new_val
        #         return val
        #     # try to de-serialize the result:
        #     try:
        #         return orjson.loads(task_result)
        #     except Exception as err:
        #         logging.error(err)
        #         return task_result
        else:
            return task_result

    async def queue(self, fn: Any, *args, **kwargs):
        """Send a function to a Queue Worker and return.

        Send & Forget functionality to send a task to Queue Worker.

        Args:
            fn: Any Function, object or callable to be send to Worker.
            args: any non-keyword arguments
            kwargs: keyword arguments.

        Returns:
            Queue Id

        Raises:
            ConfigError: bad instructions to Worker Client.
            ConnectionError: unable to connect to Worker.
            Exception: Any Unhandled error.
        """
        # TODO: Use Task id to return (later) the result of Task.
        try:
            reader, writer = await self.get_connection()
        except Exception as err:
            logging.error(err)
            raise
        logging.debug(f'Sending function {fn!s} to Worker')
        host, *_ = writer.get_extra_info('sockname')
        if isinstance(fn, (FuncWrapper, TaskWrapper)):
            # Function was wrapped or is already wrapped
            func = fn
        else:
            func = FuncWrapper(
                host,
                fn,
                *args,
                **kwargs
            )
        # serializing
        try:
            # getting data
            serialized_task = cloudpickle.dumps(func)
            writer.write(serialized_task)
            # sending data to worker:
            if writer.can_write_eof():
                writer.write_eof()
            await writer.drain()
            # asks server if task was queued:
            try:
                while True:
                    serialized_result = await reader.read(-1)
                    if reader.at_eof():
                        break
            except Exception as err:
                raise Exception from err
            finally:
                await self.close(writer)
            received = cloudpickle.loads(serialized_result)
            # we dont need the result, return true
            serialized_result = {
                "status": "Queued",
                "task": f"{func!r}",
                "message": received
            }
            return serialized_result
        except Exception as err: # pylint: disable=W0703
            logging.exception(
                f'Error Serializing Task: {err!s}'
            )
