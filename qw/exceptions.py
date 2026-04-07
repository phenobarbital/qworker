# Copyright (C) 2018-present Jesus Lara
#
"""QueueWorker Exceptions."""
from typing import Optional

__all__ = (
    'QWException',
    'ConfigError',
    'ParserError',
    'DiscardedTask',
    'ProcessNotFound',
)


class QWException(Exception):
    """Base class for other exceptions."""

    status: int = 400

    def __init__(self, message: str, status: int = 400, **kwargs):
        super().__init__(message)
        self.stacktrace = None
        if 'stacktrace' in kwargs:
            self.stacktrace = kwargs['stacktrace']
        self.message = message
        self.status = int(status)

    def __str__(self) -> str:
        return f"{self.message}"

    def get(self) -> str:
        return self.message


class ConfigError(QWException):

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or "QW Configuration Error.", status=500)


class ParserError(QWException):

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or "JSON Parser Error", status=410)


class DiscardedTask(QWException):

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or "Task was Discarded", status=408)


class ProcessNotFound(QWException):

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or "Task Not Found", status=404)
