import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from navconfig.logging import logging
from ..wrappers import QueueWrapper, FuncWrapper, TaskWrapper
from ..conf import WORKER_CONCURRENCY_NUMBER

class TaskExecutor:
    def __init__(self, task, *args, **kwargs):
        self.logger = logging.getLogger('QW.Executor')
        self.task = task
        self.semaphore = asyncio.Semaphore(WORKER_CONCURRENCY_NUMBER)

    async def run_task(self):
        result = None
        self.logger.info(
            f"Running Task: {self.task!s}"
        )
        try:
            await self.task.create()
            result = await self.task.run()
        except Exception as err:  # pylint: disable=W0703
            result = err
        finally:
            await self.task.close()
        return result

    async def run(self):
        result = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        try:
            if isinstance(self.task, (FuncWrapper, QueueWrapper)):
                self.task.set_loop(loop)
                result = await self.task()
            elif isinstance(self.task, TaskWrapper):
                self.task.set_loop(loop)
                async with self.semaphore:
                    result = await self.run_task()
            elif (
                inspect.isawaitable(self.task) or asyncio.iscoroutinefunction(self.task)
            ):
                result = await self.task()
            else:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    future = loop.run_in_executor(executor, self.task)
                result = await future
        except Exception as err:  # pylint: disable=W0703
            result = err
        finally:
            return result
