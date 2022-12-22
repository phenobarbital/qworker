"""Client Example of Queue Worker."""
import asyncio
import time
from qw.client import QClient, TaskWrapper
from qw.utils import cPrint

workers = [("nav-api.dev.local", 9999)]

qw = QClient()


async def queue_task(program, task, **args):
    task = TaskWrapper(
        program=program,
        task=task,
        **args
    )
    res = await asyncio.gather(
        *[
            qw.queue(task)
        ]
    )
    print(f'Task Completed: {res!s}')
    return res

async def run_task(program, task, **args):
    task = TaskWrapper(
        program=program,
        task=task,
        **args
    )
    res = await asyncio.gather(
        *[
            qw.run(task)
        ]
    )
    print(f'Task Completed: {res!s}')
    return res

if __name__ == '__main__':
    start_time = time.time()
    loop = asyncio.get_event_loop()
    # cPrint('Queue Task ::: ')
    # result = loop.run_until_complete(
    #     queue_task(
    #         program='test',
    #         task='daily_postpaid',
    #         params={
    #         }
    #     )
    # )
    # print(result)
    cPrint('Running Task ::: ')
    result = loop.run_until_complete(
        run_task(
            program='epson',
            task='retailers',
            debug=True,
            ignore_results=True
        )
    )
    print(result)
    end_time = time.time() - start_time
    print(f'Task Completed: took {end_time} seconds to run')
