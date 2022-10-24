"""
Abstract Wrapper Base.

Any other wrapper extends this.
"""
import uuid
from abc import ABC


class QueueWrapper(ABC):
    _queued: bool = True
    _debug: bool = False

    def __init__(self, *args, **kwargs):
        if 'queued' in kwargs:
            self._queued = kwargs['queued']
            del kwargs['queued']
        self._id = uuid.uuid4()
        self.args = args
        self.kwargs = kwargs

    @property
    def queued(self):
        return self._queued

    @queued.setter
    def queued(self, value):
        self._queued = value

    @property
    def debug(self):
        return self._debug

    @debug.setter
    def debug(self, debug: bool = False):
        self._debug = debug

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        self._id = value

    def set_loop(self, event_loop):
        self.loop = event_loop
