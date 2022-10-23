import asyncio
import hashlib
import time
from navconfig.logging import logging

class QueueServer(asyncio.Protocol):
    """Connection Protocol for QueueWorker Server."""
    connections = {}

    def __init__(self):
        self.logger = logging.getLogger('qworker.protocol')
        self.transport = None
        self.loop = asyncio.get_event_loop()
        self._ready = asyncio.Event()
        print('Starting Protocol ... ')

    def setup(self, peer):
        self.ip, self.port = peer[0], peer[1]

    @property
    def connection_id(self):
        if not hasattr(self, '_connection_id'):
            self._connection_id = hashlib.md5('{}{}{}'.format(self.ip, self.port, time.time()).encode('utf-8')).hexdigest()
        print('ID: ', self._connection_id)
        return self._connection_id

    def connection_lost(self, exc):
        print('Connection Lost')
        if exc:
            self.logger.exception(exc)
        self.logger.debug('Server closed the connection')
        del QueueServer.connections[self.connection_id]
        super().connection_lost(exc=exc)

    def connection_made(self, transport):
        print('Connection Made')
        self.transport = transport
        self.peername = transport.get_extra_info('peername')
        self.setup(self.peername)
        print('Connection Accepted')

    def data_received(self, data):
        print('Data Received')
        print('Data received: {!r}',len(data))
        self.logger.debug('received {!r}'.format(data))
        self.transport.write(data)
        self.logger.debug('sent {!r}'.format(data))

    def eof_received(self):
        self.logger.debug('received EOF')
        if self.transport.can_write_eof():
            self.transport.write_eof()
