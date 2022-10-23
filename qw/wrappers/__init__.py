"""
QueueWorker Wrappers.

Wrappers are classes to enclosed Functions or Jobs.
Collection of wrappers for different kind of Objects to be launched by workers.
"""


# from .func import FuncWrapper
# from .di import TaskWrapper
from .base import QueueWrapper

__all__ = ('QueueWrapper', )
