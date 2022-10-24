"""
Checking Health of Worker.
"""
import asyncio
import warnings
import time
import random
import cloudpickle
from navconfig.logging import logging
# from qw.wrappers import FuncWrapper, TaskWrapper
import orjson

WAIT_TIME = 0.1  # seconds

async def test_client():
    worker = ("nav-api.dev.local", 8888)
    print('WORKER IS: ', worker)
    try:
        task = asyncio.open_connection(
            *worker
        )
    except Exception as e:
        print(e)
    try:
        reader, writer = await asyncio.wait_for(
            task, timeout=10
        )
    except asyncio.TimeoutError:
        # removing this worker from the self workers
        warnings.warn(f"Timeout, skipping {worker!r}")
        await asyncio.sleep(WAIT_TIME)
    except ConnectionRefusedError:
        warnings.warn(f"Can't connect to {worker!r}. Retrying...")
        await asyncio.sleep(WAIT_TIME)
    except Exception as err:
        warnings.warn(f'Unexpected Error on Queue Client: {err!s}')
        raise

    tasks = ['health', 'keepalive']
    t = random.choice(tasks)
    print(f'SELECTED: {t}')
    if t == 'health':
        task = 'health'
    else:
        task = None
    print(f'produced: {task}')
    serialized_task = cloudpickle.dumps(task)
    writer.write(serialized_task)
    if writer.can_write_eof():
        writer.write_eof()
    await writer.drain()
    # getting data:
    try:
        while True:
            task_result = await reader.read(-1)
            if reader.at_eof():
                break
    except Exception as err:
        logging.error(err)
        raise
    try:
        task_result = cloudpickle.loads(task_result)
    except EOFError as err:
        logging.exception(f'No data was received from Server: {err!s}')
        task_result = None
    except Exception as err:
        # logging.exception(f'Error receiving data from Worker Server: {err!s}')
        task_result = orjson.loads(task_result)

    logging.debug(f'Data Received: {task_result}')
    print('Close the socket')
    writer.close()


if __name__ == '__main__':
    try:
        loop = asyncio.get_event_loop()
        # print('Starting Client:')
        for _ in range(10):
            loop.run_until_complete(test_client())
            time.sleep(0.1)
    except KeyboardInterrupt:
        print('Request Finish: ')
    finally:
        print('Closing All Connections ...')
        loop.close()
