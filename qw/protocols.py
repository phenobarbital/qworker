import time
import asyncio
from typing import Any
import hashlib
import socket
import struct
from json import JSONDecodeError
from navconfig.logging import logging
from qw.exceptions import QWException
from qw.utils.json import json_encoder, json_decoder
from .conf import (
    WORKER_DISCOVERY_HOST,
    WORKER_DEFAULT_MULTICAST,
    expected_message
)

MULTICAST_ADDRESS = WORKER_DEFAULT_MULTICAST

DEFAULT_HOST = WORKER_DISCOVERY_HOST
if not DEFAULT_HOST:
    DEFAULT_HOST = socket.gethostbyname(socket.gethostname())


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Basic Discovery Protocol for Workers."""

    workers: dict = {}

    def __init__(self):
        try:
            self._loop = asyncio.get_event_loop()
            self._loop.set_debug(True)
        except RuntimeError as ex:
            raise QWException(
                f"Error getting event loop: {ex}"
            )
        self.logger = logging.getLogger('QW.Discovery')
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
            except JSONDecodeError as ex:
                self.logger.error(
                    f"JSON decode error: {ex}"
                )
            except Exception as ex:
                self.logger.error(ex)

    def error_received(self, exc):
        print('Error received:', exc)

    def connection_lost(self, exc):
        print("Socket closed, stop the event loop", exc)

    def register_worker(self, server: str, addr: tuple):
        self.workers[server] = addr

    def remove_worker(self, server: str):
        del self.workers[server]

class QueueProtocol(asyncio.Protocol):
    """Connection Protocol for QueueWorker Server."""

    def __init__(self, queue: asyncio.Queue, name: str):
        self.queue = queue
        self.logger = logging.getLogger(
            f'QW.QueueServer-{name}'
        )
        self.transport = None
        self.loop = asyncio.get_event_loop()
        self._ready = asyncio.Event()
        self._name = name
        print(
            f'Starting Protocol for {name}'
        )

    def setup(self, peer):
        self.ip, self.port = peer[0], peer[1]

    @property
    def connection_id(self):
        if not hasattr(self, '_connection_id'):
            self._connection_id = hashlib.md5(
                '{}{}{}'.format(self.ip, self.port, time.time()).encode('utf-8')
            ).hexdigest()
        print('ID: ', self._connection_id)
        return self._connection_id

    def connection_lost(self, exc):
        if exc:
            self.logger.exception(exc)
        self.logger.debug(
            'Server closed the Connection'
        )
        super().connection_lost(exc=exc)

    def connection_made(self, transport):
        print('Connection Made')
        self.transport = transport
        self.peername = transport.get_extra_info('peername')
        self.setup(self.peername)
        print('Connection Accepted')

    def data_received(self, data):
        pass

    # def eof_received(self):
    #     self.logger.debug('received EOF')
    #     if self.transport.can_write_eof():
    #         self.transport.write_eof()
