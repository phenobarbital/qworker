"""Functional Wrapper."""
import asyncio
from .base import QueueWrapper

class FuncWrapper(QueueWrapper):
    """Wraps function and it's arguments."""
    def __init__(self, host, func, *args, **kwargs):
        super(FuncWrapper, self).__init__(*args, **kwargs)
        self.host = host
        self.func, self.args, self.kwargs = func, args, kwargs

    async def __call__(self):
        print(f'Calling Function {self.func.__name__}')
        if asyncio.iscoroutinefunction(self.func):
            return await self.func(*self.args, **self.kwargs)
        else:
            return self.func(*self.args, **self.kwargs)

    def __repr__(self) -> str:
        return '<%s> from %s' % (self.func.__name__, self.host) # pylint: disable=C0209

    def __str__(self):
        return '<%s> from %s' % (self.func.__name__, self.host) # pylint: disable=C0209
