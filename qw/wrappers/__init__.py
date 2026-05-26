"""
QueueWorker Wrappers.

Wrappers are classes to enclosed Functions or Jobs.
Collection of wrappers for different kind of Objects to be launched by workers.
"""


from .func import FuncWrapper
from .base import QueueWrapper
from .named import NamedHandlerWrapper
try:
    from .di_task import TaskWrapper
except Exception as e:
    print(
        f"Error importing TaskWrapper: {e}"
    )
    TaskWrapper = None


__all__ = (
    'QueueWrapper',
    'FuncWrapper',
    'NamedHandlerWrapper',
    'TaskWrapper',
)
