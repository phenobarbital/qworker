import asyncio
from typing import Any
from itertools import cycle
import random
import socket
from qw.exceptions import QWException
from qw.utils import cPrint
from qw.utils.json import json_decoder
from .conf import (
    WORKER_DISCOVERY_PORT,
    WORKER_DEFAULT_PORT,
    expected_message
)
from .protocols import DiscoveryProtocol


async def get_server_discovery(event_loop: asyncio.AbstractEventLoop) -> Any:
    """Get Server Discovery.
    """
    try:
        return await event_loop.create_datagram_endpoint(
            DiscoveryProtocol,
            local_addr=('0.0.0.0', WORKER_DISCOVERY_PORT),
            family=socket.AF_INET,
            allow_broadcast=True
        )
    except OSError:
        return False


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
                cPrint(f':: Discovery Server: {srv}')
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
        return server_list, cycle(server_list)  # pylint: disable=W0150
