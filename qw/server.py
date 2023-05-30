"""QueueWorker Server Implementation"""
import os
import socket
import uuid
import asyncio
import inspect
from typing import Any
from collections.abc import Callable
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
import resource
import psutil
import cloudpickle
from navconfig.logging import logging
from qw.exceptions import (
    QWException,
    ParserError,
    DiscardedTask
)
from qw.utils import make_signature
# from .protocols import QueueProtocol
from .conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    WORKER_DEFAULT_QTY,
    WORKER_QUEUE_SIZE,
    expected_message,
    WORKER_SECRET_KEY,
    RESOURCE_THRESHOLD,
    CHECK_RESOURCE_USAGE
)
from .utils.json import json_encoder
from .utils.versions import get_versions
from .utils import cPrint
from .wrappers import (
    QueueWrapper,
    FuncWrapper,
    TaskWrapper
)


DEFAULT_HOST = WORKER_DEFAULT_HOST
if not DEFAULT_HOST:
    DEFAULT_HOST = socket.gethostbyname(socket.gethostname())

class QWorker:
    """Queue Task Worker server.

    Attributes:
        host: Hostname of the server.
        port: Port number of the server.
        loop: Event loop to run in.
        task_executor: Executor that will run tasks from clients.
    """
    def __init__(
            self,
            host: str = DEFAULT_HOST,
            port: int = WORKER_DEFAULT_PORT,
            worker_id: int = None,
            name: str = '',
            event_loop: asyncio.AbstractEventLoop = None,
            debug: bool = False,
            protocol: Any = None
    ):
        self.host = host
        self.port = port
        self.debug = debug
        self.queue = None
        self.consumers = []
        self.executor = None
        self._id = worker_id
        if name:
            self._name = name
        else:
            self._name = mp.current_process().name
        self._executor = ThreadPoolExecutor(
            max_workers=WORKER_DEFAULT_QTY
        )
        self._loop = event_loop if event_loop else asyncio.new_event_loop()
        self._server: Callable = None
        self._pid = os.getpid()
        self._protocol = protocol
        # logging:
        self.logger = logging.getLogger(
            f'QW:Server:{self._name}:{self._id}'
        )

    @property
    def name(self):
        return self._name

    async def start(self):
        """Starts Queue Manager."""
        self.queue = asyncio.Queue(maxsize=WORKER_QUEUE_SIZE)
        self.executor = ProcessPoolExecutor(
            max_workers=WORKER_DEFAULT_QTY
        )
        try:
            if self._protocol:
                self._server = await self._loop.create_server(
                    self._protocol,
                    host=self.host,
                    port=self.port,
                    family=socket.AF_INET,
                    reuse_port=True
                )
            else:
                self._server = await asyncio.start_server(
                    self.connection_handler,
                    host=self.host,
                    port=self.port,
                    family=socket.AF_INET,
                    reuse_port=True,
                    # loop=self._loop
                )
            self.server_address = (
                socket.gethostbyname(socket.gethostname()), self.port
            )
            sock = self._server.sockets[0].getsockname()
            self.logger.info(
                f'Serving {self._name}:{self._id} on {sock}, pid: {self._pid}'
            )
        except Exception as err:
            raise QWException(
                f"Error: {err}"
            ) from err
        # Serve requests until Ctrl+C is pressed
        try:
            await self.fire_consumers()
            async with self._server:
                await self._server.serve_forever()
        except (RuntimeError, KeyboardInterrupt) as err:
            self.logger.exception(err, stack_info=True)

    async def fire_consumers(self):
        """Fire up the Task consumers."""
        self.consumers = [
            asyncio.create_task(
                self.task_handler(self.queue)) for _ in range(WORKER_QUEUE_SIZE - 1)
        ]

    def get_resource_usage(self):
        if CHECK_RESOURCE_USAGE is True:
            soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
            processes = psutil.process_iter()
            used = 0
            try:
                for ps in processes:
                    try:
                        num_fds = len(ps.open_files())
                        used += num_fds
                    except (
                        psutil.NoSuchProcess,
                        psutil.AccessDenied,
                        psutil.ZombieProcess
                    ):
                        pass
                return (used / soft) * 100
            except (ValueError, RuntimeError):
                pass
        else:
            return True

    async def empty_queue(self, q: asyncio.Queue):
        """Processing and shutting down the Queue."""
        while not q.empty():
            q.get_nowait()
            q.task_done()
        await q.join()

    async def shutdown(self):
        if self.debug is True:
            cPrint(
                f'Shutting down worker {self.name!s}'
            )
        try:
            # forcing close the queue
            await self.empty_queue(self.queue)
        except KeyboardInterrupt:
            pass
        # also: cancel the idle consumers:
        for c in self.consumers:
            try:
                c.cancel()
            except asyncio.CancelledError:
                pass
        try:
            self._server.close()
            await self._server.wait_closed()
        except RuntimeError as err:
            self.logger.exception(err, stack_info=True)
        except Exception as exc:
            raise QWException(
                f"Error closing Worker: {exc}"
            )
        finally:
            self._loop.stop()
        if self.debug is True:
            cPrint(
                '::: QueueWorker Server Closed ::: ',
                level='INFO'
            )

    def run_process(self, fn):
        """Unpickles task, runs it and pickles result."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        fn.set_loop(loop)
        try:
            result = loop.run_until_complete(
                self.run_function(fn, loop)
            )
            return result
        except Exception as err:
            raise QWException(
                f"Error: {err}"
            ) from err
        finally:
            loop.close()

    async def run_function(self, fn, event_loop: asyncio.AbstractEventLoop):
        result = None
        self.logger.debug(
            f'Running Task {fn!s} in worker {self.name!s}'
        )
        try:
            asyncio.set_event_loop(event_loop)
            if isinstance(fn, FuncWrapper):
                result = await fn()
            elif inspect.isawaitable(fn) or asyncio.iscoroutinefunction(fn):
                result = await fn()
            else:
                result = fn()
        except Exception as err:  # pylint: disable=W0703
            result = err
        return result

    async def run_task(self, task: TaskWrapper):
        result = None
        try:
            await task.create()
            result = await task.run()
        except Exception as err:  # pylint: disable=W0703
            result = err
        finally:
            await task.close()
        self.logger.debug(
            f"Running Task: {task!s}"
        )
        return result

    async def task_handler(self, q: asyncio.Queue):
        """Method for handling the tasks received by the connection handler."""
        while True:
            task = await q.get()
            if self.debug:
                cPrint(
                    f'Running Queued Task {task!s}', level='DEBUG'
                )
            # processing the task received
            if isinstance(task, TaskWrapper):
                # Running a FlowTask Task
                task.set_loop(self._loop)
                task.debug = True
                fn = partial(self.run_process, task)
                result = await self._loop.run_in_executor(
                    self._executor, fn
                )
                # result = await self.run_task(task)
                self.logger.debug(
                    f'{task!s} Result: {result!r}'
                )
            elif isinstance(task, FuncWrapper):
                # running a FuncWrapper
                result = None
                try:
                    result = await self.run_function(task, self._loop)
                except Exception as err:  # pylint: disable=W0703
                    result = err
                self.logger.debug(f'{task!s} Result: {result!r}')
            else:
                # TODO: try to Execute the object deserialized
                pass
            q.task_done()
            self.logger.debug(
                f'consumed: {task}'
            )
            self.logger.debug(
                f'QUEUE Size after Work: {self.queue.qsize()}'
            )

    def check_signature(self, payload: bytes) -> bool:
        signature = make_signature(expected_message, WORKER_SECRET_KEY)
        if signature == payload:
            return True
        else:
            return False

    async def response_keepalive(
        self,
        writer: asyncio.StreamWriter,
        status: dict = None
    ) -> None:
        addrs = ', '.join(str(sock.getsockname()) for sock in self._server.sockets)
        prct_used = self.get_resource_usage()
        if not status:
            status = {
                "pong": "Empty data",
                "worker": {
                    "name": self.name,
                    "serving": addrs,
                    "resource": f"{prct_used:.2f}%"
                }
            }
        result = json_encoder(status)
        await self.closing_writer(writer, result.encode('utf-8'))

    async def worker_health(self, writer: asyncio.StreamWriter):
        addrs = ', '.join(str(sock.getsockname()) for sock in self._server.sockets)
        status = {
            "queue": {
                "size": self.queue.qsize(),
                "full": self.queue.full(),
                "empty": self.queue.empty(),
                "consumers": len(self.consumers)
            },
            "worker": {
                "name": self.name,
                "address": self.server_address,
                "serving": addrs
            }
        }
        await self.response_keepalive(status=status, writer=writer)

    async def worker_check_state(self, writer: asyncio.StreamWriter):
        ## TODO: add last executed task
        addrs = ', '.join(str(sock.getsockname()) for sock in self._server.sockets)
        status = {
            "versions": get_versions(),
            "worker": {
                "name": self.name,
                "address": self.server_address,
                "serving": addrs
            },
            "queue": {
                "size": self.queue.qsize(),
                "full": self.queue.full(),
                "empty": self.queue.empty(),
                "consumers": len(self.consumers)
            },
        }
        await self.response_keepalive(
            status=status,
            writer=writer
        )

    async def discard_task(self, message: str, writer: asyncio.StreamWriter):
        exc = DiscardedTask(
            message
        )
        result = cloudpickle.dumps(exc)
        await self.closing_writer(
            writer,
            result
        )
        return False

    async def signature_validation(
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter
    ):
        prefix = None
        prct_used = self.get_resource_usage()
        self.logger.debug(f'Current NFILE percent: {prct_used}')
        if prct_used >= int(RESOURCE_THRESHOLD):
            ## Avoid accepting task for lack of resource
            self.logger.error(
                f'Discarted Task due Resource usage: {prct_used}'
            )
            exc = DiscardedTask(
                f'Too many Open Files: {prct_used:.2f}% usage.'
            )
            result = cloudpickle.dumps(exc)
            await self.closing_writer(
                writer,
                result
            )
            return False
        try:
            prefix = await reader.readline()
            if not prefix:
                # if no content on payload:
                await self.response_keepalive(writer=writer)
                return False
        except asyncio.IncompleteReadError as exc:
            self.logger.error(exc)
            return False
        except (ConnectionResetError, ConnectionAbortedError, EOFError) as exc:
            self.logger.error(exc)
            raise
        except asyncio.CancelledError:
            return False
        ###
        if prefix == b'health':
            ### sending a heartbeat
            await self.worker_health(
                writer=writer
            )
            return False
        elif prefix == b'check_state':
            await self.worker_check_state(
                writer=writer
            )
            return False
        else:
            try:
                msglen = int(prefix)
            except ValueError:
                raise
            payload = await reader.readexactly(msglen)
            if self.check_signature(payload) is False:
                ### close transport inmediately:
                exc = ConnectionRefusedError(
                    'Connection unsecured, Closing now.'
                )
                self.logger.error(
                    'Closing unsecured connection'
                )
                result = cloudpickle.dumps(exc)
                await self.closing_writer(
                    writer,
                    result
                )
                return False
            else:
                # passing a "continue" signal:
                writer.write('CONTINUE'.encode('utf-8'))
                await writer.drain()
                return True

    async def _read_task(self, reader: asyncio.StreamReader):
        serialized_task = b''
        while True:
            serialized_task += await reader.read(-1)
            if reader.at_eof():
                break
        return serialized_task

    async def deserialize_task(self, serialized_task, writer: asyncio.StreamWriter):
        try:
            task = cloudpickle.loads(serialized_task)
            self.logger.debug(f'TASK RECEIVED: {task}')
            return task
        except RuntimeError as ex:
            ex = ParserError(f"Error Decoding Serialized Task: {ex}")
            result = cloudpickle.dumps(ex)
            await self.closing_writer(writer, result)
            return False

    async def handle_queue_wrapper(
        self,
        task: QueueWrapper,
        uid: uuid.UUID,
        writer: asyncio.StreamWriter
    ):
        """Handle QueueWrapper Tasks.
        """
        # Set Debug level of task:
        task.debug = self.debug
        if task.queued is True:
            try:
                task.id = uid
                await self.queue.put(task)
                self.logger.debug(
                    f'Current QUEUE Size: {self.queue.qsize()}'
                )
                return f'Task {task!s} with id {uid} was queued.'.encode('utf-8')
            except asyncio.QueueFull:
                return await self.discard_task(
                    f"Worker {self.name!s} Queue is Full, discarding Task {task!r}"
                )
        else:
            try:
                # executed and send result to client
                task.id = uid
                fn = partial(self.run_process, task)
                return await self._loop.run_in_executor(self._executor, fn)
            except Exception as err:  # pylint: disable=W0703
                try:
                    result = cloudpickle.dumps(err)
                except Exception as ex:  # pylint: disable=W0703
                    result = cloudpickle.dumps(
                        QWException(
                            f'Error on Deal with Exception: {ex!s}'
                        )
                    )
                await self.closing_writer(writer, result)
                return False

    async def connection_handler(
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter
    ):
        """ Handler for Function/Task Execution.
        receives the client request and run/queue the function.
        Args:
            reader: asyncio StreamReader, client information
            writer: asyncio StreamWriter, infor to send to client.
        Returns:
            Task Result.
        """
        # # TODO: task can select which executor to use, else use default:
        addr = writer.get_extra_info("peername")
        # first time: check signature authentication of payload:
        if not await self.signature_validation(reader, writer):
            return False
        self.logger.debug(
            f"Received Data from {addr!r} to worker {self.name!s} pid: {self._pid}"
        )
        # after: deserialize Task:
        serialized_task = await self._read_task(reader)
        task = None
        result = None
        task_uuid = uuid.uuid4()
        task = await self.deserialize_task(serialized_task, writer)
        if not task:
            return False
        elif isinstance(task, QueueWrapper):
            if not (result := await self.handle_queue_wrapper(task, task_uuid, writer)):
                return False
        elif callable(task):
            result = await self.run_function(
                task, self._loop
            )
        else:
            # put work in Queue:
            try:
                await self.queue.put(task)
                await asyncio.sleep(.1)
                self.logger.debug(
                    f'Current QUEUE Size: {self.queue.qsize()}'
                )
                result = f'Task {task!s}:{task_uuid} was Queued.'.encode('utf-8')
            except asyncio.QueueFull:
                self.logger.error(
                    f"Worker Queue is Full, discarding Task {task!r}"
                )
                return await self.discard_task(
                    message=f'Task {task!s} was discarded, queue full',
                    writer=writer
                )
        if result is None:
            # Not always a Task returns Value, sometimes returns None.
            result = [
                {"uuid": task_uuid, "worker": self.name}
            ]
        try:
            if isinstance(result, BaseException):
                try:
                    msg = result.message
                except Exception:
                    msg = str(result)
                result = {
                    "exception": result.__class__,
                    "error": msg
                }
            elif inspect.isgeneratorfunction(result) or isinstance(result, list):
                try:
                    result = json_encoder(list(result))
                except (ValueError, TypeError):
                    result = f"{result!r}"  # cannot pickle a generator object
            result = cloudpickle.dumps(result)
        except Exception as err:  # pylint: disable=W0703
            error = {
                "exception": err.__class__,
                "error": str(err)
            }
            result = cloudpickle.dumps(error)
            self.logger.error(
                f'Error dumping result: {err!s}'
            )
        await self.closing_writer(writer, result)

    async def closing_writer(self, writer: asyncio.StreamWriter, result):
        """Sending results and closing the streamer."""
        try:
            writer.write(result)
            await writer.drain()
            if writer.can_write_eof():
                writer.write_eof()
            if self.debug is True:
                cPrint(f"Closing client socket, pid: {self._pid}", level='DEBUG')
            writer.close()
        except Exception as e:
            self.logger.error(f"Error while closing writer: {str(e)}")


### Start Server ###
def start_server(num_worker, host, port, debug: bool):
    """thread worker function"""
    loop = None
    worker = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except RuntimeError as ex:
        raise QWException(
            f"Unable to set an event loop: {ex}"
        ) from ex
    try:
        worker = QWorker(
            host=host,
            port=port,
            event_loop=loop,
            debug=debug,
            worker_id=num_worker
        )
        loop.run_until_complete(
            worker.start()
        )
    except (OSError, RuntimeError) as ex:
        raise QWException(
            f"Unable to Spawn a new Worker: {ex}"
        )
    except KeyboardInterrupt:
        if loop and worker:
            worker.logger.info(
                f'Shutting down Worker {worker.name if worker else "unknown"}'
            )
            loop.run_until_complete(
                worker.shutdown()
            )
    finally:
        if loop:
            loop.close()  # Close the event loop
