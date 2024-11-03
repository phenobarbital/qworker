import asyncio
import time
from typing import Union
from collections.abc import Awaitable, Callable
import importlib
from navconfig.logging import logging
try:
    from flowtask.exceptions import (
        NotFound,
        DataNotFound,
        FileNotFound,
        TaskFailed,
        TaskNotFound,
        NotSupported
    )
except ImportError:
    logging.warning(
        "Unable to Load FlowTask, we can't handle Flowtask in Queue Manager."
    )
from qw.exceptions import QWException
from ..conf import (
    WORKER_QUEUE_SIZE,
    WORKER_RETRY_INTERVAL,
    WORKER_RETRY_COUNT,
    WORKER_QUEUE_CALLBACK
)
from ..executor import TaskExecutor
from ..wrappers.base import QueueWrapper


class QueueManager:
    """base Class for all Queue Managers in Queue Worker.
    """

    def __init__(self, worker_name: str):
        self.logger = logging.getLogger('QW.Queue')
        self.worker_name = worker_name
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=WORKER_QUEUE_SIZE
        )
        self.consumers: list = []
        self.logger.debug(
            f'Started Queue Manager with size: {WORKER_QUEUE_SIZE}'
        )
        ### Getting Queue Callback (called when queue object is consumed)
        self._callback: Union[Callable, Awaitable] = self.get_callback(
            WORKER_QUEUE_CALLBACK
        )
        self.logger.notice(
            f'Callback Queue: {self._callback!r}'
        )

    async def task_callback(self, task, **kwargs):
        self.logger.info(
            f'Task Consumed: {task!r} with ID {task.id}'
        )

    def get_callback(self, done_callback: str) -> Union[Callable, Awaitable]:
        if not done_callback:
            ## returns a simple logger:
            return self.task_callback
        try:
            parts = done_callback.split(".")
            bkname = parts.pop()
            classpath = ".".join(parts)
            module = importlib.import_module(classpath, package=bkname)
            return getattr(module, bkname)
        except ImportError as ex:
            raise RuntimeError(
                f"Error loading Queue Callback {done_callback}: {ex}"
            ) from ex

    def size(self):
        return self.queue.qsize()

    def empty(self):
        return self.queue.empty()

    def full(self):
        return self.queue.full()

    async def fire_consumers(self):
        """Fire up the Task consumers."""
        for _ in range(WORKER_QUEUE_SIZE - 1):
            task = asyncio.create_task(
                self.queue_handler()
            )
            self.consumers.append(task)

    async def empty_queue(self):
        """Processing and shutting down the Queue."""
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        await self.queue.join()
        # also: cancel the idle consumers:
        for c in self.consumers:
            try:
                c.cancel()
            except asyncio.CancelledError:
                pass

    async def put(self, task: QueueWrapper, id: str):
        """put.

            Add a Task into the Queue.
        Args:
            task (QueueWrapper): an instance of QueueWrapper
        """
        try:
            self.queue.put_nowait(task)
            await asyncio.sleep(.1)
            self.logger.info(
                f'Task {task!s} with id {id} was queued at {int(time.time())}'
            )
            # TODO: Add broadcast event for queued task.
            return True
        except asyncio.queues.QueueFull:
            self.logger.error(
                f"Worker Queue is Full, discarding Task {task!r}"
            )
            raise

    async def get(self) -> QueueWrapper:
        """get.

            Get a Task from Queue.
        Returns:
            task (QueueWrapper): an instance of QueueWrapper
        """
        task = await self.queue.get()
        self.logger.info(
            f'Getting Task {task!s} at {int(time.time())}'
        )
        return task

    async def queue_handler(self):
        """Method for handling the tasks received by the connection handler."""
        while True:
            result = None
            task = await self.queue.get()
            self.logger.info(
                f"Task started {task} on {self.worker_name}"
            )
            ### Process Task:
            try:
                executor = TaskExecutor(task)
                result = await executor.run()
                if isinstance(result, asyncio.TimeoutError):
                    raise asyncio.TimeoutError(
                        f"Task {task} with id {task.id} was cancelled."
                    )
                elif type(result) in (
                    NotFound,
                    DataNotFound,
                    FileNotFound,
                    TaskFailed,
                    TaskNotFound,
                    NotSupported
                ):
                    raise result
                elif isinstance(result, BaseException):
                    ## TODO: checking retry info from Task.
                    if task.retry() is True:  # task was marked to retry
                        if task.retries < WORKER_RETRY_COUNT - 1:
                            task.add_retries()
                            self.logger.warning(
                                f"Task {task} failed. Retrying. Retry count: {task.retries}"
                            )
                            # Wait some seconds before retrying.
                            await asyncio.sleep(WORKER_RETRY_INTERVAL)
                            await self.queue.put(task)
                            await asyncio.sleep(0.1)
                        else:
                            cnt = WORKER_RETRY_COUNT
                            self.logger.warning(
                                f"{task} Failed after {cnt} times. Discarding task."
                            )
                            raise result
                    else:
                        raise result
                self.logger.debug(
                    f'Consumed Task: {task} at {int(time.time())}'
                )
            except RuntimeError as exc:
                result = exc
                raise QWException(
                    f"Error: {exc}"
                ) from exc
            except Exception as exc:
                self.logger.error(
                    f"Task failed with error: {exc}"
                )
                raise
            finally:
                ### Task Completed
                self.queue.task_done()
                await self._callback(
                    task, result=result
                )
