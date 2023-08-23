"""QueueWorker Client."""
import asyncio
import itertools
import random
import uuid
import warnings
import socket
import base64
from typing import Any, Union
from collections.abc import Callable, Awaitable
from collections import defaultdict
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from redis import asyncio as aioredis
import pickle
import cloudpickle
import jsonpickle
import orjson
from navconfig.logging import logging
from qw.discovery import get_client_discovery
from qw.utils import make_signature
from qw.exceptions import (
    ParserError,
    QWException,
    DiscardedTask
)
from .conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    WORKER_REDIS,
    REDIS_WORKER_STREAM,
    REDIS_WORKER_GROUP,
    USE_DISCOVERY,
    WORKER_SECRET_KEY,
    MAX_WORKERS,
    expected_message
)
from .process import QW_WORKER_LIST
from .wrappers import FuncWrapper, TaskWrapper


MAX_RETRY_COUNT = 5
WAIT_TIME = 0.1  # seconds


def round_robin_worker(workers):
    """Pick next worker based on Round Robin."""
    while True:
        try:
            return next(workers)
        except (ValueError, TypeError) as ex:
            raise QWException(
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
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            raise
        ## logger:
        self.logger = logging.getLogger('QW.Client')
        ## check if we use network discovery or redis list:
        if worker_list:
            self._workers = worker_list
            self._worker_list = itertools.cycle(worker_list)
        elif USE_DISCOVERY is True:
            # get worker list from discovery:
            self._workers, self._worker_list = get_client_discovery()
        else:
            # getting the list from redis
            self._workers, self._worker_list = self.get_workers()
        if not self._worker_list:
            self.logger.warning(
                'EMPTY WORKER LIST: Trying to connect to a default Worker'
            )
            # try to connect with the default worker
            self._workers = [(WORKER_DEFAULT_HOST, WORKER_DEFAULT_PORT)]
            self._worker_list = itertools.cycle(self._workers)
        self._num_retries = defaultdict(int)
        self._worker = None
        self.timeout = timeout

    def discover_workers(self):
        if USE_DISCOVERY is True:
            self._workers, self._worker_list = get_client_discovery()
        else:
            self._workers, self._worker_list = self.get_workers()

    def get_workers(self):
        async def get_workers_list():
            try:
                # starting redis:
                redis = aioredis.ConnectionPool.from_url(
                    WORKER_REDIS,
                    decode_responses=True,
                    encoding='utf-8'
                )
                conn = aioredis.Redis(connection_pool=redis)
                workers = []
                if (lrange := await conn.lrange(QW_WORKER_LIST, 0, MAX_WORKERS)):
                    w = [orjson.loads(el) for el in lrange]
                    workers = [tuple(list(v.values())[0]) for v in w]
                return workers, itertools.cycle(workers)
            except aioredis.ConnectionError as err:
                self.logger.error(
                    f"Redis connection error: {err}"
                )
                raise
            finally:
                await redis.disconnect(inuse_connections=True)

        def get_workers():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(get_workers_list())
            finally:
                loop.close()

        executor = ThreadPoolExecutor()
        future = executor.submit(get_workers)
        return future.result()

    def get_servers(self) -> list:
        return self._workers

    def register_pickle_module(self, module: Any):
        cloudpickle.register_pickle_by_value(module)

    async def validate_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ) -> tuple:
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
            try:
                response = cloudpickle.loads(response)
                if isinstance(response, BaseException):
                    raise response
            except (TypeError, ValueError, pickle.UnpicklingError) as ex:
                raise ConnectionRefusedError(
                    f"Connection refused by QW Server: {ex}"
                )

    async def get_connection(self):
        retries = {}
        reader = None
        writer = None
        if not self._worker_list:
            # try discover again a worker list:
            self.discover_workers()
        while True:
            worker = round_robin_worker(self._worker_list)
            self.logger.debug(f':: WORKER SELECTED: {worker!r}')
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
            except DiscardedTask as exc:
                self.logger.warning(
                    f'Task was discarded, {exc!s}, retrying'
                )
                raise
            except asyncio.TimeoutError:
                # removing this worker from the self workers
                # TODO: removing bad worker:
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
                        f'Max number of retries reached for {worker!r}, error: {err!s}'
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
        self.logger.debug('Closing Socket')
        writer.close()

    async def get_worker_connection(self):
        try:
            reader, writer = await self.get_connection()
            return reader, writer
        except DiscardedTask:
            await asyncio.sleep(WAIT_TIME)
            ### ask again after wait for new connection:
            reader, writer = await self.get_connection()
            return reader, writer
        except ConnectionError as ex:
            raise ConnectionError(
                f"Unable to Connect to Queue Worker: {ex}"
            ) from ex
        except Exception as err:
            self.logger.error(err)
            raise

    async def sendto_worker(
        self,
        func: Union[bytes, Callable, Awaitable],
        writer: asyncio.StreamWriter
    ):
        serialized_task = None
        try:
            serialized_task = cloudpickle.dumps(func)
            writer.write(serialized_task)
            # sending data to worker:
            if writer.can_write_eof():
                writer.write_eof()
            await writer.drain()
        except Exception as err:
            self.logger.error(
                f'Error Serializing Task {func!r}: {err!s}'
            )
            raise
        return serialized_task

    async def get_result(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        result = None
        try:
            while True:
                result = await reader.read(-1)
                if reader.at_eof():
                    break
        except Exception as ex:
            raise QWException(
                f"Error getting results from Worker: {ex}"
            ) from ex
        finally:
            await self.close(writer)
            return result

    def get_wrapped_function(
        self,
        fn: Any,
        host: str,
        *args,
        use_wrapper: bool = False,
        queued: bool = False,
        **kwargs
    ):
        if isinstance(fn, (TaskWrapper, FuncWrapper)):
            # already wrapped
            func = fn
            func.queued = queued
        elif use_wrapper is True:
            # wrap function into a Wrapper:
            func = FuncWrapper(
                host,
                fn,
                *args,
                **kwargs
            )
            func.queued = queued
        else:
            # sent function *as is* using partial
            func = partial(fn, *args, **kwargs)
        return func

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
        reader, writer = await self.get_worker_connection()

        host, *_ = writer.get_extra_info('sockname')
        # wrapping the function into Task Wrapper
        self.logger.debug(
            f'Sending Object {fn!s} to Worker {host}'
        )
        func = self.get_wrapped_function(
            fn,
            host,
            *args,
            use_wrapper=use_wrapper,
            queued=False,
            **kwargs
        )
        ## send data to worker:
        await self.sendto_worker(func, writer)
        # Then, got the result:
        serialized_result = await self.get_result(reader, writer)
        try:
            task_result = cloudpickle.loads(serialized_result)
            self.logger.debug(
                f'Data Received: {task_result!r}'
            )
            try:
                if isinstance(task_result, str):
                    result = jsonpickle.decode(task_result)
                else:
                    result = task_result
            except (TypeError, ValueError) as e:
                logging.error(e)
                result = task_result
            except Exception as e:
                logging.error(e)
            task_result = result
        except (ValueError, TypeError) as ex:
            raise ParserError(
                f"Error Parsing serialized results: {ex}"
            ) from ex
        except EOFError as err:
            self.logger.exception(f'No data was received from Server: {err!s}')
            task_result = None
        except Exception as err:  # pylint: disable=W0703
            self.logger.exception(f'Error receiving data from Worker Server: {err!s}')
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
                except (TypeError, ValueError):
                    res.append(el)
            return res
        elif isinstance(task_result, dict):
            # check if result is and error:
            if 'exception' in task_result:
                ex = task_result['exception']
                if isinstance(ex, BaseException):
                    msg = task_result['error']
                    error = str(ex)
                    self.logger.error(
                        error
                    )
                    raise ex(
                        f"{error}: {msg}"
                    )
            return task_result
        else:
            return task_result

    async def queue(self, fn: Any, *args, use_wrapper: bool = True, **kwargs):
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
        reader, writer = await self.get_worker_connection()
        self.logger.debug(
            f'Sending function {fn!s} to Worker'
        )
        host, *_ = writer.get_extra_info('sockname')
        # serializing
        func = self.get_wrapped_function(
            fn,
            host,
            *args,
            use_wrapper=use_wrapper,
            queued=True,
            **kwargs
        )
        try:
            ## send data to worker:
            await self.sendto_worker(func, writer=writer)
            # asks server if task was queued:
            try:
                while True:
                    serialized_result = await reader.read(-1)
                    if reader.at_eof():
                        break
            except Exception as err:
                raise QWException(
                    str(err)
                ) from err
            finally:
                await self.close(writer)
            received = cloudpickle.loads(serialized_result)
            # we dont need the result, return true
            if isinstance(received, (QWException, asyncio.QueueFull)):
                raise received.__class__(str(received))
            serialized_result = {
                "status": "Queued",
                "task": f"{func!r}",
                "message": received
            }
            return serialized_result
        except Exception as err:  # pylint: disable=W0703
            self.logger.exception(
                f'Error Serializing Task: {err!s}'
            )
            raise

    async def publish(self, fn: Any, *args, use_wrapper: bool = True, **kwargs):
        """Publish a function into a Pub/Sub Channel.

        Send & Forget functionality to send a task to Queue Worker using Pub/Sub.

        Args:
            fn: Any Function, object or callable to be send to Worker.
            args: any non-keyword arguments
            kwargs: keyword arguments.

        Returns:
            None.

        Raises:
            ConfigError: bad instructions to Worker Client.
            ConnectionError: unable to connect to Worker.
            Exception: Any Unhandled error.
        """
        try:
            worker_stream = kwargs.pop('stream', REDIS_WORKER_STREAM)
        except (ValueError, KeyError):
            worker_stream = REDIS_WORKER_STREAM
        self.logger.info(
            f'Sending function {fn!s} to Pub/Sub Channel {worker_stream}'
        )
        host = socket.gethostbyname(socket.gethostname())
        # serializing
        func = self.get_wrapped_function(
            fn,
            host,
            *args,
            use_wrapper=use_wrapper,
            queued=True,
            **kwargs
        )
        if use_wrapper is True:
            uid = func.id
        else:
            uid = uuid.uuid1(
                node=random.getrandbits(48) | 0x010000000000
            )
        serialized_task = cloudpickle.dumps(func)
        encoded_task = base64.b64encode(serialized_task).decode('utf-8')
        message = {
            "uid": str(uid),
            "task": encoded_task
        }
        # check if published
        # Add the data to the stream
        try:
            async with aioredis.from_url(
                    WORKER_REDIS,
                    decode_responses=True,
                    encoding='utf-8'
            ) as conn:
                self.logger.debug(
                    f"Redis Server:  {conn}"
                )
                result = await conn.xadd(worker_stream, message, nomkstream=False)
                serialized_result = {
                    "status": "Queued",
                    "task": f"{func!r}",
                    "message": result
                }
                self.logger.info(
                    f"Task {fn!r} was published to {REDIS_WORKER_GROUP}-{worker_stream}"
                )
                return serialized_result
        finally:
            try:
                await conn.close()
            except Exception as exc:
                logging.warning(
                    f"Failed to disconnect Redis: {exc}"
                )

    async def health(self):
        task = 'health'
        serialized_task = task.encode('utf-8')
        # getting writer, reader
        reader, writer = await self.get_worker_connection()
        # send data to worker:
        await self.sendto_worker(serialized_task, writer=writer)
        # asks server if task was queued:
        try:
            while True:
                serialized_result = await reader.read(-1)
                if reader.at_eof():
                    break
        except Exception as err:
            raise QWException(
                str(err)
            ) from err
        finally:
            await self.close(writer)
        task_result = None
        try:
            task_result = cloudpickle.loads(serialized_result)
        except EOFError as err:
            logging.exception(
                f'No data was received from Server: {err!s}'
            )
        except Exception as err:
            # logging.exception(f'Error receiving data from Worker Server: {err!s}')
            task_result = orjson.loads(serialized_result)
        return task_result
