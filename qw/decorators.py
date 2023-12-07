from typing import Any
from collections.abc import Callable
from functools import wraps
from qw.client import QClient


def dispatch(func: Callable) -> Any:
    qw = QClient()  # calling Queue Worker

    @wraps(func)
    async def _wrap(*args, **kwargs):
        result = await qw.queue(func, *args, **kwargs)
        return result
    return _wrap

def run(func: Callable) -> Any:
    qw = QClient()  # calling Queue Worker

    @wraps(func)
    async def _wrap(*args, **kwargs):
        return await qw.run(func, *args, **kwargs)
    return _wrap
