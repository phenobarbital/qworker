import asyncio
import time
from qw.client import QClient, TaskWrapper
from qw.utils import cPrint

qw = QClient()

print('SERVER : ', qw.get_servers())


async def very_long_task(seconds: int):
    if seconds == 17:
        raise ValueError('BAD BOYS')
    print(f'This Function Sleep for {seconds} sec.')
    await asyncio.sleep(seconds)


async def queue_task():
    await qw.publish(very_long_task, 10)
    task = TaskWrapper(
        program='troc',
        task='organizations',
        debug=True,
        ignore_results=True
    )
    res = await asyncio.gather(
        *[
            qw.publish(task)
        ]
    )
    print(f'Task Queued: {res!s}')
    await qw.publish(very_long_task, 15)

if __name__ == '__main__':
    start_time = time.time()
    loop = asyncio.get_event_loop()
    top = loop.run_until_complete(
        queue_task()
    )
    end_time = time.time() - start_time
    cPrint(f'Task took {end_time} seconds to run', level='DEBUG')
