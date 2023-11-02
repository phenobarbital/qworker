"""TaskWrapper.

Wrapping a Flowtask-task to be executed by Worker.
"""
import asyncio
import multiprocessing as mp
from navconfig.logging import logging
try:
    from flowtask.tasks.task import Task
    from flowtask.exceptions import (
        TaskException,
        TaskNotFound,
        TaskError,
        FileNotFound,
        EmptyFile,
        DataNotFound,
        NotFound,
        TaskFailed
    )
except ImportError:
    logging.warning(
        "Unable to Load FlowTask Task Component, we can't send Tasks to any Worker."
    )
from ..exceptions import QWException
from .base import QueueWrapper

class TaskWrapper(QueueWrapper):
    """Wraps a DI Task and arguments"""
    def __init__(self, program, task, *args, task_id: str = None, **kwargs):
        super(TaskWrapper, self).__init__(*args, **kwargs)
        try:
            self.new_args = kwargs['new_args']
            del kwargs['new_args']
        except KeyError:
            self.new_args = []
        try:
            self.host = kwargs['host']
            del kwargs['host']
        except KeyError:
            self.host = 'localhost'
        try:
            self._debug = kwargs['debug']
            del kwargs['debug']
        except KeyError:
            self._debug = False
        self.program = program
        self.task = task
        self._task = None
        self.args, self.kwargs = args, kwargs
        if task_id is not None:
            self.id = task_id
        else:
            self.id = self._id

    def task_id(self):
        return f'{self.id!s}'

    def task_obj(self):
        return self._task

    def __repr__(self):
        return f'Task(task={self.task}, program={self.program}, debug={self._debug})'

    async def create(self):
        try:
            loop = self.loop
        except AttributeError:
            loop = asyncio.get_running_loop()
        try:
            self._task = Task(
                task=self.task,
                program=self.program,
                task_id=self.id,
                loop=loop,
                worker=mp.current_process(),
                new_args=self.new_args,
                debug=self._debug,
                **self.kwargs
            )
        except TaskNotFound as ex:
            raise TaskNotFound(
                f"Task Not Found: {ex}"
            )
        except TaskError:
            raise
        except (FileNotFound, EmptyFile) as exc:
            logging.warning(exc)
            raise
        except (
            DataNotFound,
            NotFound
        ) as exc:
            logging.warning(exc)
            raise
        except Exception as exc:
            logging.exception(exc, stack_info=True)
            raise QWException(
                f"{exc}"
            ) from exc

    def __await__(self):
        return self.__call__().__await__()

    async def __call__(self, *args, **kwargs):
        print(f'Calling Task {self.program}.{self.task}')
        result = None
        try:
            # first: we create the task
            await self.create()
            result = await self.run()
            try:
                stats = self._task.stats.stats
            except Exception:  # pylint: disable=W0703
                stats = None
            result = {
                "result": result,
                "stats": stats
            }
            return result
        except Exception as err:  # pylint: disable=W0703
            raise TaskFailed(
                f"{err}"
            )
        finally:
            await self.close()

    def retry(self):
        try:
            return self._task.retry()
        except Exception:
            return True

    async def run(self):
        """ Running the Task in the loop."""
        result = None
        async with self._task as task:
            try:
                status = await task.start()
                if not status:
                    raise TaskError(
                        f'Error starting Task: {self.program}.{self.task}'
                    )
            except Exception as err:
                logging.error(str(err), exc_info=True)
                if isinstance(err, TaskException):
                    raise
                raise TaskFailed(
                    f"{err}"
                ) from err
            logging.info(
                f'Executing Task {self.program}.{self.task} with id {self.id}'
            )
            try:
                result = await task.run()
            except Exception as err:
                logging.exception(err, stack_info=False)
                if isinstance(err, TaskException):
                    raise
                else:
                    raise TaskFailed(
                        f"{err}"
                    ) from err
        return result

    async def close(self):
        try:
            logging.info(
                f'Closing Task {self.program}.{self.task} with id {self.id}'
            )
            if self._task:
                await self._task.close()
                self._task = None
        except Exception as err:  # pylint: disable=W0703
            logging.error(err)

    def __str__(self):
        return f"{self.program}.{self.task}"
