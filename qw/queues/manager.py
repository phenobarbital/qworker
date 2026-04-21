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
    WORKER_QUEUE_CALLBACK,
    WORKER_CONSUMER_MONITOR_INTERVAL
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
        self._respawn_events: int = 0
        self._monitor_task: asyncio.Task | None = None
        self._monitor_interval: float = float(WORKER_CONSUMER_MONITOR_INTERVAL)
        # Emit a CRITICAL log every N respawns to flag potential crash-loops
        self._respawn_alert_threshold: int = 5

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
            ceiling, grow_events, discard_events, full,
            consumer_alive, consumer_total, respawn_events.
            consumer_alive / consumer_total count only queue_handler tasks
            (the _shrink_task infrastructure task is excluded).
        """
        sz = self.queue.qsize()
        # Exclude _shrink_task so counts reflect real consumer workers only
        queue_handlers = [
            t for t in self.consumers if t is not self._shrink_task
        ]
        alive = len([t for t in queue_handlers if not t.done()])
        return {
            "size": sz,
            "max_size": self.max_size,
            "base_size": self._policy.base_size,
            "grow_margin": self._policy.grow_margin,
            "ceiling": self._policy.ceiling(),
            "grow_events": self._policy.grow_events,
            "discard_events": self._policy.discard_events,
            "full": self.queue.full(),
            "consumer_alive": alive,
            "consumer_total": len(queue_handlers),
            "respawn_events": self._respawn_events,
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
        self._monitor_task = asyncio.create_task(self._consumer_monitor())
        # Not appending to consumers list since it monitors the list itself

    async def empty_queue(self) -> None:
        """Processing and shutting down the Queue."""
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        await self.queue.join()
        # Cancel all consumers (including shrink monitor).
        # task.cancel() never raises CancelledError — it returns True/False.
        for c in self.consumers:
            c.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()

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

    async def _consumer_monitor(self) -> None:
        """Defense-in-depth: respawn any dead consumer tasks.

        Runs on a background loop every ``_monitor_interval`` seconds.
        Detects tasks in ``self.consumers`` (queue_handler and _shrink_task)
        that have exited, logs the cause, and replaces them with fresh tasks.

        Emits a CRITICAL log every ``_respawn_alert_threshold`` respawns to
        surface persistent crash-loop conditions to operators.

        Note: ``_monitor_task`` itself is never added to ``self.consumers``,
        so this loop never attempts to respawn itself.
        """
        while True:
            try:
                await asyncio.sleep(self._monitor_interval)
                alive = []
                for t in self.consumers:
                    if t is self._shrink_task:
                        if t.done():
                            exc = t.exception() if not t.cancelled() else None
                            self.logger.warning(
                                "_shrink_monitor died: %s — respawning", exc
                            )
                            new_t = asyncio.create_task(self._shrink_monitor())
                            self._shrink_task = new_t
                            alive.append(new_t)
                            self._respawn_events += 1
                        else:
                            alive.append(t)
                        continue
                    if t.done():
                        exc = t.exception() if not t.cancelled() else None
                        self.logger.warning(
                            "Consumer died: %s — respawning", exc
                        )
                        new_t = asyncio.create_task(self.queue_handler())
                        alive.append(new_t)
                        self._respawn_events += 1
                    else:
                        alive.append(t)
                self.consumers = alive
                # Alert on sustained crash-loop activity
                if (
                    self._respawn_events > 0
                    and self._respawn_events % self._respawn_alert_threshold == 0
                ):
                    self.logger.critical(
                        "Consumer respawn count reached %d — possible crash loop"
                        " on worker %s. Investigate immediately.",
                        self._respawn_events,
                        self.worker_name,
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("_consumer_monitor error")

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
                    result = asyncio.TimeoutError(
                        "Task %s with id %s was cancelled." % (task, task.id)
                    )
                elif type(result) in (
                    NotFound,
                    DataNotFound,
                    FileNotFound,
                    TaskFailed,
                    TaskNotFound,
                    NotSupported
                ):
                    # Log flowtask errors explicitly — consumer loop continues.
                    self.logger.error(
                        "Task %s failed with %s: %s",
                        task, type(result).__name__, result
                    )
                elif isinstance(result, BaseException):
                    ## TODO: checking retry info from Task.
                    if task.retry() is True:  # task was marked to retry
                        if task.retries < WORKER_RETRY_COUNT - 1:
                            task.add_retries()
                            self.logger.warning(
                                "Task %s failed. Retrying. Retry count: %d",
                                task, task.retries
                            )
                            # Wait some seconds before retrying.
                            await asyncio.sleep(WORKER_RETRY_INTERVAL)
                            await self.queue.put(task)
                            await asyncio.sleep(0.1)
                        else:
                            self.logger.error(
                                "Task %s permanently failed after %d retries"
                                " — discarding.",
                                task, WORKER_RETRY_COUNT
                            )
                    else:
                        self.logger.error(
                            "Task %s failed (no retry configured): %s",
                            task, result
                        )
                self.logger.debug(
                    "Consumed Task: %s at %d", task, int(time.time())
                )
            except Exception as exc:
                self.logger.error("Task %s failed: %s", task, exc)
                result = exc
            finally:
                ### Task Completed
                self.queue.task_done()
                try:
                    await self._callback(
                        task, result=result
                    )
                except Exception as cb_err:
                    self.logger.error("Callback error for %s: %s", task, cb_err)
                if self._state:
                    result_str = "error" if isinstance(result, BaseException) else "success"
                    self._state.task_completed(str(task.id), result_str, source="queue")
