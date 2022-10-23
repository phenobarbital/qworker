"""TaskWrapper.

Wrapping a DI-task to be executed by Worker.
"""
import logging
import multiprocessing as mp
from dataintegration.task import Task
from dataintegration.exceptions import (
    TaskNotFound,
    TaskError,
    TaskFailed
)
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
        self.id = task_id

    def task_id(self):
        return f'{self.id!s}'

    def __repr__(self):
        return f'Task(task={self.task}, program={self.program}, debug={self._debug})'

    async def create(self):
        try:
            self._task = Task(
                task=self.task,
                program=self.program,
                task_id=self.id,
                loop=self.loop,
                worker=mp.current_process(),
                new_args=self.new_args,
                debug=self._debug,
                **self.kwargs
            )
        except TaskNotFound:
            raise
        except TaskError:
            raise
        except Exception as err:
            logging.exception(err, stack_info=True)
            raise

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
            except Exception:
                stats = None
            result = {
                "result": result,
                "stats": stats
            }
            return result
        except Exception as err:
            print(err)
            result = err
            return result
        finally:
            await self.close()

    async def run(self):
        """ Running the Task in the loop."""
        result = None
        print(f':: Starting Task {self.program}.{self.task}')
        async with self._task as task:
            try:
                status = await task.start()
                if not status:
                    raise TaskError(
                        f'Error on Task: {self.program}.{self.task}'
                    )
            except Exception as err:
                logging.error(err)
                raise TaskFailed(err) from err
            print(f'Executing Task {self.program}.{self.task}')
            try:
                result = await task.run()
            except Exception as err:
                logging.exception(err, stack_info=True)
                raise
        return result

    async def close(self):
        try:
            await self._task.close()
            del self._task
        except Exception as err:
            logging.error(err)

    def __str__(self):
        return f"{self.program}.{self.task}"
