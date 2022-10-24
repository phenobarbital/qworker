import asyncio
from typing import Any
from itertools import cycle
import socket
from qw.exceptions import QWException
from .conf import (
    WORKER_DISCOVERY_HOST,
    WORKER_DISCOVERY_PORT,
    WORKER_DEFAULT_PORT,
    expected_message
)


DEFAULT_HOST = WORKER_DISCOVERY_HOST
if not DEFAULT_HOST:
    DEFAULT_HOST = socket.gethostbyname(socket.gethostname())


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self._loop = asyncio.get_event_loop()
        self._loop.set_debug(True)
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: Any, addr: str):
        data = data.decode('utf-8')
        if data == expected_message:
            # print(f'Received {data!r} from {addr!r}')
            # send information Protocol:
            data = expected_message.encode('utf-8')
            self.transport.sendto(data, addr)
        else:
            self.transport.close()

    def error_received(self, exc):
        print('Error received:', exc)

    def connection_lost(self, exc):
        print("Socket closed, stop the event loop")
        self._loop.stop()

async def get_server_discovery(event_loop: asyncio.AbstractEventLoop) -> Any:
    """Get Server Discovery.
    """
    print(':: Starting Discovery Server ::')
    # proto = DiscoveryProtocol('prot1')
    return await event_loop.create_datagram_endpoint(
        DiscoveryProtocol,
        local_addr=('0.0.0.0', WORKER_DISCOVERY_PORT),
        allow_broadcast=True
    )


def get_client_discovery() -> list:
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.settimeout(2)
    srv_addr = ('', WORKER_DISCOVERY_PORT)
    try:
        detected = []
        while True:
            sock.sendto(expected_message.encode(), srv_addr)
            # Receive response
            data, server = sock.recvfrom(4096)
            if data.decode('utf-8') == expected_message:
                srv = server[0]
                print('Server IP: ' + str(srv) )
                # TODO: detect which port is used by this server:
                detected.append((srv, WORKER_DEFAULT_PORT))
                break
    except socket.timeout as ex:
        raise QWException(
            "Unable to discover Workers on this Network."
        ) from ex
    finally:
        sock.close()
        if detected:
            return cycle(detected) # pylint: disable=W0150
