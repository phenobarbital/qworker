"""
QueueWorker Wrappers.

Wrappers are classes to enclosed Functions or Jobs.
Collection of wrappers for different kind of Objects to be launched by workers.
"""
import logging as _logging

from .func import FuncWrapper
from .base import QueueWrapper
from .named import NamedHandlerWrapper

_logger = _logging.getLogger(__name__)

try:
    from .di_task import TaskWrapper
except Exception as e:
    _logger.warning("TaskWrapper not available (flowtask not installed): %s", e)
    TaskWrapper = None


__all__ = (
    'QueueWrapper',
    'FuncWrapper',
    'NamedHandlerWrapper',
    'TaskWrapper',
)
