import time
import random
import asyncio
import redis
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
    WORKER_REDIS,
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
            params: dict = {
                "encoding": 'utf-8',
                "decode_responses": True,
                "max_connections": 10
            }
            self._redis = redis.from_url(
                url=WORKER_REDIS, **params
            )
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
            workers = {}
            # Get all the server info from the redis list
            for server_info_json in self._redis.lrange(
                'QW_SERVER_LIST', 0, -1
            ):
                # Deserialize the JSON string to a dictionary and update the workers
                workers.update(json_decoder(server_info_json))
            try:
                items = random.shuffle(list(workers.items()))
                workers = dict(items)
            except TypeError:
                workers = self.workers
            data = json_encoder(workers).encode('utf-8')
            self.transport.sendto(data, addr)
        elif data == expected_message:
            # send information Protocol:
            data = expected_message.encode('utf-8')
            self.transport.sendto(data, addr)
        else:
            # register a worker
            self.logger.debug(
                f'Worker: {data!s}'
            )
            try:
                srv, addr = zip(*json_decoder(data).items())
                address = tuple(addr[0])
                try:
                    server = srv[0]
                except KeyError:
                    server = srv
                if server in self.workers:
                    # unregister worker:
                    self.logger.info(
                        '::: Remove Worker :::'
                    )
                    self.remove_worker(server, address)
                    return
                self.logger.info(
                    '::: Registering a Worker :::'
                )
                self.register_worker(server, address)
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
        # Convert the server and address to a JSON string
        server_info = json_encoder({server: addr})
        # Push the server info to the redis list
        self._redis.lpush('QW_SERVER_LIST', server_info)

    def remove_worker(self, server: str, addr: tuple):
        del self.workers[server]
        server_info = json_encoder({server: addr})
        # Remove all occurrences of this server from the redis list
        self._redis.lrem('QW_SERVER_LIST', 1, server_info)

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
