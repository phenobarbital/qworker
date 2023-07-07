import asyncio
from qw.discovery import get_server_discovery
from qw.conf import (
    WORKER_DISCOVERY_PORT
)


async def start_server(loop):
    transport, discovery_server = await get_server_discovery(event_loop=loop)
    print(':: Starting Discovery Server ::')
    return transport, discovery_server

if __name__ == '__main__':
    try:
        loop = asyncio.get_event_loop()
        transport, server = loop.run_until_complete(start_server(loop))
        loop.run_forever()
    except KeyboardInterrupt:
        transport.close()
