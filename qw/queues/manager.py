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
from .policy import QueueSizePolicy


class QueueManager:
    """base Class for all Queue Managers in Queue Worker."""

    def __init__(
        self,
        worker_name: str,
        state_tracker=None,
        policy: QueueSizePolicy | None = None,
    ) -> None:
        self.logger = logging.getLogger('QW.Queue')
        self.worker_name = worker_name
        self._policy: QueueSizePolicy = policy or QueueSizePolicy.from_config(
            base_size=WORKER_QUEUE_SIZE
        )
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=self._policy.current_maxsize
        )
        self.consumers: list = []
        self.logger.debug(
            f'Started Queue Manager with size: {self._policy.base_size}'
        )
        ### Getting Queue Callback (called when queue object is consumed)
        self._callback: Union[Callable, Awaitable] = self.get_callback(
            WORKER_QUEUE_CALLBACK
        )
        self.logger.notice(
            f'Callback Queue: {self._callback!r}'
        )
        # Optional StateTracker for task lifecycle observability
        self._state = state_tracker
        # Hysteresis flag — True when we are currently above the warn threshold
        self._warn_active: bool = False
        # Reference to the shrink monitor task (populated by fire_consumers)
        self._shrink_task: asyncio.Task | None = None

    # ---------------- read-only accessors ----------------

    @property
    def max_size(self) -> int:
        """Current effective maximum queue size (may grow beyond base_size)."""
        return self.queue._maxsize

    @property
    def base_size(self) -> int:
        """Baseline queue capacity (never changes at runtime)."""
        return self._policy.base_size

    @property
    def grow_margin(self) -> int:
        """Maximum number of extra slots allowed above base_size."""
        return self._policy.grow_margin

    def snapshot(self) -> dict:
        """Return a read-only summary of the current queue state.

        Returns:
            dict with keys: size, max_size, base_size, grow_margin,
            ceiling, grow_events, discard_events, full.
        """
        sz = self.queue.qsize()
        return {
            "size": sz,
            "max_size": self.max_size,
            "base_size": self._policy.base_size,
            "grow_margin": self._policy.grow_margin,
            "ceiling": self._policy.ceiling(),
            "grow_events": self._policy.grow_events,
            "discard_events": self._policy.discard_events,
            "full": self.queue.full(),
        }

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

    # ---------------- internal: resize ----------------

    def _apply_maxsize(self, new_max: int) -> None:
        """Mutate queue._maxsize and wake one blocked putter if any.

        Args:
            new_max: The new maximum size to apply to the underlying queue.
        """
        self.queue._maxsize = new_max
        # Release one blocked putter (no-op if none queued)
        if self.queue._putters:
            try:
                self.queue._wakeup_next(self.queue._putters)
            except Exception:  # defensive: never break the event loop
                self.logger.debug("wakeup_next swallowed", exc_info=True)

    async def fire_consumers(self) -> None:
        """Fire up the Task consumers and the shrink monitor."""
        for _ in range(self._policy.base_size - 1):
            task = asyncio.create_task(self.queue_handler())
            self.consumers.append(task)
        self._shrink_task = asyncio.create_task(self._shrink_monitor())
        self.consumers.append(self._shrink_task)

    async def empty_queue(self) -> None:
        """Processing and shutting down the Queue."""
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        await self.queue.join()
        # also: cancel the idle consumers (including shrink monitor):
        for c in self.consumers:
            try:
                c.cancel()
            except asyncio.CancelledError:
                pass

    # ---------------- rewritten put() ----------------

    async def put(self, task: QueueWrapper, id: str) -> bool:
        """Add a Task into the Queue with dynamic sizing support.

        Handles warn-on-pressure, grow-on-full, and discard-at-ceiling
        according to the configured QueueSizePolicy.

        Args:
            task (QueueWrapper): an instance of QueueWrapper
            id (str): task identifier for logging

        Returns:
            True if the task was successfully queued.

        Raises:
            asyncio.queues.QueueFull: if the queue is at ceiling capacity.
        """
        size = self.queue.qsize()

        # Warn transition (hysteresis): only log on under→over transition
        if self._policy.should_warn(size) and not self._warn_active:
            self._warn_active = True
            self.logger.warning(
                f"Queue near limit: size={size}/{self.max_size} "
                f"task_id={task.id}"
            )
        elif not self._policy.should_warn(size) and self._warn_active:
            self._warn_active = False

        try:
            self.queue.put_nowait(task)
        except asyncio.queues.QueueFull:
            if self._policy.can_grow():
                self._policy.register_grow()
                self._apply_maxsize(self._policy.current_maxsize)
                self.queue.put_nowait(task)
                self.logger.warning(
                    f"Queue grew: max_size={self.max_size} "
                    f"base={self.base_size} margin_used="
                    f"{self.max_size - self.base_size} "
                    f"grow_events={self._policy.grow_events} "
                    f"task_id={task.id}"
                )
            else:
                self._policy.register_discard()
                self.logger.error(
                    f"Queue at ceiling: max_size={self.max_size} "
                    f"discard_events={self._policy.discard_events} "
                    f"task_id={task.id}"
                )
                raise

        # State tracking (unchanged behaviour)
        if self._state:
            fn_name = self._state._get_function_name(task)
            self._state.task_queued(str(task.id), fn_name)

        # Cooldown reset on sustained pressure
        if self.queue.qsize() / max(1, self.max_size) > \
                self._policy.low_watermark_ratio:
            self._policy.note_high_usage(time.monotonic())

        await asyncio.sleep(0.1)
        self.logger.info(
            f'Task {task!s} with id {id} was queued at {int(time.time())}'
        )
        return True

    # ---------------- shrink monitor ----------------

    async def _shrink_monitor(self) -> None:
        """Background coroutine that shrinks the queue toward base_size."""
        interval = max(1.0, self._policy.shrink_cooldown / 5)
        while True:
            try:
                await asyncio.sleep(interval)
                size = self.queue.qsize()
                now = time.monotonic()
                if self._policy.can_shrink(size, now) and \
                        size < self._policy.current_maxsize:
                    self._policy.register_shrink()
                    self._apply_maxsize(self._policy.current_maxsize)
                    self.logger.info(
                        f"Queue shrunk: max_size={self.max_size}"
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("shrink_monitor error")

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
            if self._state:
                self._state.task_executing(str(task.id), source="queue")
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
                if self._state:
                    result_str = "error" if isinstance(result, BaseException) else "success"
                    self._state.task_completed(str(task.id), result_str, source="queue")
