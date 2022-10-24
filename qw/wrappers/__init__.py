"""
QueueWorker Wrappers.

Wrappers are classes to enclosed Functions or Jobs.
Collection of wrappers for different kind of Objects to be launched by workers.
"""


from .func import FuncWrapper
from .base import QueueWrapper
from .di_task import TaskWrapper


__all__ = ('QueueWrapper', 'FuncWrapper', 'TaskWrapper', )
