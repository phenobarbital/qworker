import asyncio
from qw.queues import QueueManager
from qw.wrappers import QueueWrapper

async def very_long_task(seconds: int):
    print(f'This Function Sleep for {seconds} sec.')
    await asyncio.sleep(seconds)

async def task_callback(task, **kwargs):
    print("=== Task callback ====")
    print('RUNNING TASK >>> {task}')

async def main():
    queue_manager = QueueManager()

    # create and fire consumers (assuming a callback is defined)
    await queue_manager.fire_consumers(done_callback=task_callback)

    # enqueue 100 tasks:
    for i in range(100):
        task = QueueWrapper(very_long_task, i)
        _ = asyncio.create_task(queue_manager.put(task, id=task.id))

    # Wait for all tasks to complete:
    while not queue_manager.empty():
        await asyncio.sleep(1)

    # Stop consumers:
    await queue_manager.empty_queue()

asyncio.run(main())
