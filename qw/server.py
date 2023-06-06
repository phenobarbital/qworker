"""QueueWorker Server Implementation"""
import os
import time
import socket
import uuid
import asyncio
import inspect
from typing import Any
from collections.abc import Callable
import multiprocessing as mp
import cloudpickle
from navconfig.logging import logging
from qw.exceptions import (
    QWException,
    ParserError,
    DiscardedTask
)
from qw.utils import make_signature
from .conf import (
    WORKER_DEFAULT_HOST,
    WORKER_DEFAULT_PORT,
    WORKER_DEFAULT_QTY,
    expected_message,
    WORKER_SECRET_KEY,
    WORKER_QUEUE_CALLBACK
)
from .utils.json import json_encoder
from .utils.versions import get_versions
from .utils import cPrint
from .queues import QueueManager
from .wrappers import (
    QueueWrapper
)
from .executor import TaskExecutor

DEFAULT_HOST = WORKER_DEFAULT_HOST
if not DEFAULT_HOST:
    DEFAULT_HOST = socket.gethostbyname(socket.gethostname())


# Initialize a semaphore with Worker Limit
semaphore = asyncio.Semaphore(WORKER_DEFAULT_QTY)


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
        self._id = worker_id
        if name:
            self._name = name
        else:
            self._name = mp.current_process().name
        self._loop = event_loop if event_loop else asyncio.new_event_loop()
        self._server: Callable = None
        self._pid = os.getpid()
        self._protocol = protocol
        # logging:
        self.logger = logging.getLogger(
            f'QW.Server:{self._name}.{self._id}'
        )

    @property
    def name(self):
        return self._name

    async def start(self):
        """Starts Queue Manager."""
        self.queue = QueueManager(worker_name=self._name)
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
        # Getting Tasks Callback (run when task is consumed from Queue)

        # Serve requests until Ctrl+C is pressed
        try:
            await self.queue.fire_consumers()
            async with self._server:
                await self._server.serve_forever()
        except (RuntimeError, KeyboardInterrupt) as err:
            self.logger.exception(err, stack_info=True)

    async def shutdown(self):
        if self.debug is True:
            cPrint(
                f'Shutting down worker {self.name!s}'
            )
        try:
            # forcing close the queue
            await self.queue.empty_queue()
        except KeyboardInterrupt:
            pass
        try:
            self._server.close()
            await self._server.wait_closed()
        except RuntimeError as err:
            self.logger.exception(
                err, stack_info=True
            )
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
        if not status:
            status = {
                "pong": "Empty data",
                "worker": {
                    "name": self.name,
                    "serving": addrs
                }
            }
        result = json_encoder(status)
        await self.closing_writer(writer, result.encode('utf-8'))

    async def worker_health(self, writer: asyncio.StreamWriter):
        addrs = ', '.join(str(sock.getsockname()) for sock in self._server.sockets)
        status = {
            "queue": {
                "size": self.queue.size(),
                "full": self.queue.full(),
                "empty": self.queue.empty(),
                "consumers": len(self.queue.consumers)
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
                "size": self.queue.size(),
                "full": self.queue.full(),
                "empty": self.queue.empty(),
                "consumers": len(self.queue.consumers)
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
            self.logger.debug(
                f'TASK RECEIVED: {task} at {int(time.time())}'
            )
            return task
        except (EOFError, RuntimeError) as ex:
            ### Empty Task:
            ex = ParserError(
                f"Error Decoding Serialized Task: {ex}"
            )
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
                await self.queue.put(
                    task, id=task.id
                )
                return f'Task {task!s} with id {uid} was queued.'.encode('utf-8')
            except asyncio.QueueFull:
                return await self.discard_task(
                    f"Worker {self.name!s} Queue is Full, discarding Task {task!r}"
                )
        else:
            try:
                # executed and send result to client
                executor = TaskExecutor(task)
                return await executor.run()
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
        addr = writer.get_extra_info(
            "peername"
        )
        # first time: check signature authentication of payload:
        if not await self.signature_validation(reader, writer):
            return False
        self.logger.info(
            f"Received Data from {addr!r} to worker {self.name!s} pid: {self._pid}"
        )
        # after: deserialize Task:
        serialized_task = await self._read_task(reader)
        task = None
        result = None
        task = await self.deserialize_task(
            serialized_task, writer
        )
        task_uuid = uuid.uuid4()
        if not task:
            return False
        elif isinstance(task, QueueWrapper):
            if not (result := await self.handle_queue_wrapper(task, task_uuid, writer)):
                return False
        elif callable(task):
            executor = TaskExecutor(task)
            result = await executor.run()
        else:
            # put work in Queue:
            try:
                await self.queue.put(task, id=task_uuid)
                result = f'Task {task!s} was Queued.'.encode('utf-8')
            except asyncio.QueueFull:
                return await self.discard_task(
                    message=f'Task {task!s} was discarded, queue full',
                    writer=writer
                )
        if result is None:
            # Not always a Task returns Value, sometimes returns None.
            result = [
                {
                    "task": task,
                    "uuid": task_uuid,
                    "worker": self.name
                }
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
                cPrint(
                    f"Closing client socket, pid: {self._pid}", level='DEBUG'
                )
            writer.close()
        except Exception as e:
            self.logger.error(
                f"Error while closing writer: {str(e)}"
            )


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
