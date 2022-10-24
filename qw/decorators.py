from typing import Any
from collections.abc import Callable
from functools import wraps
from qw.client import QClient


def dispatch(func: Callable) -> Any:
    qw = QClient() ## calling Queue Worker
    print('FUNCTION is ', qw)
    @wraps(func)
    async def _wrap(*args, **kwargs):
        result = await qw.queue(func, *args, **kwargs)
        return result
    return _wrap
