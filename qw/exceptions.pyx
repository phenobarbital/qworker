# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""QueueWorker Exceptions."""
cdef class QWException(Exception):
    """Base class for other exceptions"""

    status: int = 400

    def __init__(self, str message, int status = 400, **kwargs):
        super().__init__(message)
        self.stacktrace = None
        if 'stacktrace' in kwargs:
            self.stacktrace = kwargs['stacktrace']
        self.message = message
        self.status = int(status)

    def __str__(self):
        return f"{self.message}"

    def get(self):
        return self.message

#### Exceptions:
cdef class ConfigError(QWException):

    def __init__(self, str message = None):
        super().__init__(message or f"QW Configuration Error.", status=500)

cdef class ParserError(QWException):

    def __init__(self, str message = None):
        super().__init__(message or f"JSON Parser Error", status=410)

cdef class DiscardedTask(QWException):
    def __init__(self, str message = None):
        super().__init__(message or f"Task was Discarded", status=408)

cdef class ProcessNotFound(QWException):
    def __init__(self, str message = None):
        super().__init__(message or f"Task Not Found", status=404)
