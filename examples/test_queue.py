import asyncio
import time
from random import randint
from qw.client import QClient
from qw.utils import cPrint

qw = QClient()

print('SERVER : ', qw.get_servers())


async def very_long_task(seconds: int):
    if seconds == 17:
        raise ValueError('BAD BOYS')
    print(f'This Function Sleep for {seconds} sec.')
    await asyncio.sleep(seconds)

async def queue_tasks():
    """Returns top n words in documents specified by URLs."""
    x = [randint(10, 20) for p in range(0, 100)]
    print(f'Pushed {len(x)} tasks to worker queue.')
    result = await asyncio.gather(
        *[qw.queue(very_long_task, n) for n in x]
    )
    print(result)

if __name__ == '__main__':
    start_time = time.time()
    loop = asyncio.get_event_loop()
    top = loop.run_until_complete(
        queue_tasks()
    )
    end_time = time.time() - start_time
    print(top)
    cPrint(f'Task took {end_time} seconds to run', level='DEBUG')
