import asyncio
from typing import Any
from itertools import cycle
import random
import socket
import struct
from navconfig.logging import logging
from qw.exceptions import QWException
from qw.utils import cPrint
from qw.utils.json import json_encoder, json_decoder
from .conf import (
    WORKER_DISCOVERY_HOST,
    WORKER_DISCOVERY_PORT,
    WORKER_DEFAULT_PORT,
    expected_message
)

MULTICAST_ADDRESS = "239.255.255.250"

DEFAULT_HOST = WORKER_DISCOVERY_HOST
if not DEFAULT_HOST:
    DEFAULT_HOST = socket.gethostbyname(socket.gethostname())


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Basic Discovery Protocol for Workers."""

    workers: dict = {}

    def __init__(self):
        self._loop = asyncio.get_event_loop()
        self._loop.set_debug(True)
        self.transport = None
        super().__init__()

    def connection_made(self, transport):
        self.transport = transport
        # Allow receiving multicast broadcasts
        sock = self.transport.get_extra_info('socket')
        group = socket.inet_aton(MULTICAST_ADDRESS)
        mreq = struct.pack('4sL', group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def datagram_received(self, data: Any, addr: str):
        data = data.decode('utf-8')
        print(f'Received {data!r} from {addr!r}')
        logging.debug("%s:%s > %s", *(addr + (data,)))
        if data == 'list_workers':
            data = json_encoder(self.workers).encode('utf-8')
            self.transport.sendto(data, addr)
        elif data == expected_message:
            # send information Protocol:
            data = expected_message.encode('utf-8')
            self.transport.sendto(data, addr)
        else:
            # register a worker
            try:
                server, addr = zip(*json_decoder(data).items())
                self.workers[server[0]] = tuple(addr[0])
            except Exception as ex:
                logging.warning(ex)


    def error_received(self, exc):
        print('Error received:', exc)

    def connection_lost(self, exc):
        print("Socket closed, stop the event loop", exc)

    def register_worker(self, server: str, addr: tuple):
        self.workers[server] = addr

    def remove_worker(self, server: str):
        del self.workers[server]

async def get_server_discovery(event_loop: asyncio.AbstractEventLoop) -> Any:
    """Get Server Discovery.
    """
    return await event_loop.create_datagram_endpoint(
        DiscoveryProtocol,
        local_addr=('0.0.0.0', WORKER_DISCOVERY_PORT),
        family=socket.AF_INET,
        allow_broadcast=True
    )

def get_client_discovery() -> tuple:
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.settimeout(2)
    srv_addr = ('', WORKER_DISCOVERY_PORT)
    try:
        server_list = []
        while True:
            sock.sendto(expected_message.encode(), srv_addr)
            # Receive response
            data, server = sock.recvfrom(4096)
            if data.decode('utf-8') == expected_message:
                srv, port = server
                cPrint(f':: Discovery Server: {srv}' )
                sock.sendto('list_workers'.encode(), (srv, port))
                # ask for a list of servers:
                # TODO: detect which port is used by this server:
                ls, _ = sock.recvfrom(4096)
                if ls:
                    server_list = [tuple(v) for v in json_decoder(ls).values()]
                else:
                    server_list.append((srv, WORKER_DEFAULT_PORT))
                break
    except socket.timeout as ex:
        raise QWException(
            "Unable to discover Workers on this Network."
        ) from ex
    finally:
        sock.close()
        random.shuffle(server_list)
        return server_list, cycle(server_list) # pylint: disable=W0150
