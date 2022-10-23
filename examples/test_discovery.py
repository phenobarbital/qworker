import asyncio
from qw.discovery import get_server_discovery


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(get_server_discovery(loop)) # Server starts listening
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
