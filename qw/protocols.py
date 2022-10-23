import sys
import asyncio
import hashlib
import logging
import time
from time import sleep

logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s: %(message)s',
    stream=sys.stderr,
)
log = logging.getLogger('main')


class QueueServer(asyncio.Protocol):
    """Connection Protocol for QueueWorker Server."""
    connections = {}

    def __init__(self):
        self.transport = None
        self.loop = asyncio.get_event_loop()
        self._ready = asyncio.Event()
        print('Starting Protocol ... ')

    def setup(self, peer):
        self.ip, self.port = peer[0], peer[1]

    @property
    def connection_id(self):
        if not hasattr(self, '_connection_id'):
            self._connection_id = hashlib.md5('{}{}{}'.format(self.ip, self.port, time()).encode('utf-8')).hexdigest()
        print('ID: ', self._connection_id)
        return self._connection_id

    def connection_lost(self, exception):
        print('Connection Lost')
        if exception:
            self.log.exception(exception)
        logging.debug('The server closed the connection')
        del QueueServer.connections[self.connection_id]
        super().connection_lost(error)

    def connection_made(self, transport):
        print('Connection Made')
        self.transport = transport
        self.peername = transport.get_extra_info('peername')
        self.setup(self.peername)
        # self.log = logging.getLogger(
        #     'QueueServer_{}_{}'.format(*self.peername)
        # )
        # QueueServer.connections[self.connection_id] = self
        print('Connection Accepted')

    def data_received(self, data):
        print('Data Received')
        print('Data received: {!r}',len(data))
        self.log.debug('received {!r}'.format(data))
        self.transport.write(data)
        self.log.debug('sent {!r}'.format(data))

    def eof_received(self):
        self.log.debug('received EOF')
        if self.transport.can_write_eof():
            self.transport.write_eof()
