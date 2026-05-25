"""Execution backend package for QWorker.

Provides pluggable task execution backends: local (in-process), Docker, and Kubernetes.
"""
from .models import (
    ContainerResources,
    ContainerConfig,
    ContainerTaskMapping,
    TaskResult,
    serialize_for_container,
    deserialize_from_container,
)
from .base import BaseExecutionBackend, BackendRegistry
from .local import LocalBackend

# DockerBackend is conditionally imported (optional dependency)
try:
    from .docker import DockerBackend
    _HAS_DOCKER_BACKEND = True
except ImportError:
    DockerBackend = None  # type: ignore[assignment,misc]
    _HAS_DOCKER_BACKEND = False

__all__ = [
    "ContainerResources",
    "ContainerConfig",
    "ContainerTaskMapping",
    "TaskResult",
    "serialize_for_container",
    "deserialize_from_container",
    "BaseExecutionBackend",
    "BackendRegistry",
    "LocalBackend",
    "DockerBackend",
]
