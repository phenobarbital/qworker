import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
from navconfig.logging import logging
from notify.providers.telegram import Telegram
from notify.models import Chat
from flowtask.conf import (
    EVENT_CHAT_ID,
    ENVIRONMENT
)
from ..wrappers import QueueWrapper, FuncWrapper, TaskWrapper
from ..conf import WORKER_CONCURRENCY_NUMBER, WORKER_TASK_TIMEOUT

class TaskExecutor:
    def __init__(self, task, *args, **kwargs):
        self.logger = logging.getLogger(
            'QW.Executor'
        )
        self.task = task
        self.semaphore = asyncio.Semaphore(WORKER_CONCURRENCY_NUMBER)

    async def run_task(self):
        result = None
        self.logger.info(
            f"Creating Task: {self.task!s}"
        )
        try:
            await self.task.create()
            # task = asyncio.create_task(self.task())
            # task.add_done_callback(self.task_done)
            result = await asyncio.wait_for(
                self.task.run(), timeout=WORKER_TASK_TIMEOUT * 60
            )
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"Task {self.task} with id {self.task.id} was cancelled."
            )
        except Exception as err:  # pylint: disable=W0703
            self.logger.error(
                f"An Error occurred while running Task {self.task}.{self.task.id}: {err}"
            )
            result = err
        finally:
            await self.task.close()
            return result

    def get_notify(self):
        # TODO: implement other notify connectors:
        # defining the Default chat object:
        recipient = Chat(
            **{"chat_id": EVENT_CHAT_ID, "chat_name": "Navigator"}
        )
        # send notifications to Telegram bot
        tm = Telegram()
        return [tm, recipient]

    async def task_pending(self, task, *args, **kwargs):
        worker = mp.current_process()
        message = f"Task {self.task!s} got timeout and was cancelled at {worker.name!s}"
        self.logger.error(
            message
        )
        try:
            ntf, recipients = self.get_notify()
            message = f'⚠️ ::{ENVIRONMENT} -  {message!s}.'
            args = {
                "recipient": [recipients],
                "message": message,
                "disable_notification": True
            }
            try:
                async with ntf as conn:
                    result = await conn.send(**args)
                return result
            except RuntimeError:
                pass
        except Exception as err:
            self.logger.error(
                f"Error Sending Task Failure notification: {err}"
            )

    async def run(self):
        result = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        try:
            if type(self.task) in (FuncWrapper, QueueWrapper):
                self.logger.notice(
                    f"Running Function: {self.task}"
                )
                self.task.set_loop(loop)
                async with self.semaphore:
                    result = await self.task()
            elif isinstance(self.task, TaskWrapper):
                self.logger.info(
                    f"Running Task: {self.task}"
                )
                self.task.set_loop(loop)
                async with self.semaphore:
                    result = await self.run_task()
            elif (
                inspect.isawaitable(self.task) or asyncio.iscoroutinefunction(self.task)
            ):
                self.logger.notice(
                    f"Running Awaitable Func: {self.task}"
                )
                result = await self.task()
            else:
                self.logger.notice(
                    f"Running Blocking Function: {self.task}"
                )
                with ThreadPoolExecutor(max_workers=2) as executor:
                    future = loop.run_in_executor(executor, self.task)
                result = await future
        except Exception as err:  # pylint: disable=W0703
            result = err
        finally:
            return result
