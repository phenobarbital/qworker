"""Functional Wrapper."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from .base import QueueWrapper

class FuncWrapper(QueueWrapper):
    """Wraps function and it's arguments."""
    def __init__(self, host, func, *args, **kwargs):
        super(FuncWrapper, self).__init__(*args, **kwargs)
        self.host = host
        self._retry = None
        self.func, self.args, self.kwargs = func, args, kwargs

    async def __call__(self):
        print(f'Calling Function {self.func.__name__}')
        if asyncio.iscoroutinefunction(self.func):
            return await self.func(*self.args, **self.kwargs)
        else:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            fn = partial(self.func, *self.args, **self.kwargs)
            with ThreadPoolExecutor(max_workers=2) as executor:
                future = loop.run_in_executor(executor, fn)
            return await future

    def __repr__(self) -> str:
        return '<%s> from %s' % (self.func.__name__, self.host)  # pylint: disable=C0209

    def __str__(self):
        return '<%s> from %s' % (self.func.__name__, self.host)  # pylint: disable=C0209

    def retry(self):
        try:
            return self._retry
        except Exception:
            return True
