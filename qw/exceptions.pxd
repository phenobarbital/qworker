# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""Queue Worker Exceptions."""
cdef class QWException(Exception):
    """Base class for other exceptions"""
    pass

#### Exceptions:
cdef class ConfigError(QWException):
    pass

cdef class ParserError(QWException):
    pass

cdef class DiscardedTask(QWException):
    """Usable to patch Discarded tasks."""
    pass

cdef class ProcessNotFound(QWException):
    """Process was not found."""
    pass
