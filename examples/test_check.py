"""
Checking Health of Worker.
"""
import asyncio
import time
from qw.client import QClient

WAIT_TIME = 0.1  # seconds

async def test_client():
    qw = QClient()
    print('SERVERS : ', qw.get_servers())


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
