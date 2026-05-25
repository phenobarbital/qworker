"""Container Configuration Models for QWorker task execution.

Provides Pydantic data models for configuring container-based task execution
via Docker or Kubernetes, along with serialization helpers.
"""
import base64
import json
import uuid
from typing import Any, Literal, Optional

import cloudpickle
from pydantic import BaseModel, Field


class ContainerResources(BaseModel):
    """Resource limits and requests for container execution.

    Args:
        cpu_limit: CPU limit, e.g. '500m', '2'.
        memory_limit: Memory limit, e.g. '512Mi', '2Gi'.
        cpu_request: CPU request (Kubernetes only).
        memory_request: Memory request (Kubernetes only).
    """

    cpu_limit: Optional[str] = Field(None, description="CPU limit, e.g. '500m', '2'")
    memory_limit: Optional[str] = Field(None, description="Memory limit, e.g. '512Mi', '2Gi'")
    cpu_request: Optional[str] = Field(None, description="CPU request (K8s only)")
    memory_request: Optional[str] = Field(None, description="Memory request (K8s only)")


class ContainerConfig(BaseModel):
    """Task-level container execution configuration.

    Args:
        backend: Execution backend — 'docker' or 'k8s'.
        image: Container image (must exist in registry).
        env: Environment variables to pass to container.
        volumes: Host:container volume mount mappings.
        resources: Optional CPU and memory constraints.
        fire_and_forget: Skip result tracking if True.
        timeout: Override WORKER_TASK_TIMEOUT for this task (minutes).
        namespace: Kubernetes namespace override.
    """

    backend: Literal["docker", "k8s"] = Field(..., description="Execution backend")
    image: str = Field(..., description="Container image (must exist in registry)")
    env: dict[str, str] = Field(
        default_factory=dict, description="Environment variables"
    )
    volumes: dict[str, str] = Field(
        default_factory=dict, description="Host:container volume mounts"
    )
    resources: Optional[ContainerResources] = None
    fire_and_forget: bool = Field(
        default=False, description="Skip result tracking"
    )
    timeout: Optional[int] = Field(
        None, description="Override WORKER_TASK_TIMEOUT for this task (minutes)"
    )
    namespace: Optional[str] = Field(None, description="K8s namespace override")


class ContainerTaskMapping(BaseModel):
    """Worker-side config mapping task names to container targets.

    Args:
        task_pattern: Task name or glob pattern to match.
        config: Container configuration to apply to matched tasks.
    """

    task_pattern: str = Field(..., description="Task name or glob pattern")
    config: ContainerConfig


class TaskResult(BaseModel):
    """Result from a backend execution.

    Args:
        task_id: UUID of the executed task.
        success: True if execution completed without error.
        result: The task return value (Any).
        error: Error message if success is False, else None.
        execution_time: Wall-clock seconds from dispatch to completion.
        backend: Name of the backend that ran the task.
    """

    task_id: uuid.UUID
    success: bool
    result: Any
    error: Optional[str]
    execution_time: float
    backend: str

    model_config = {"arbitrary_types_allowed": True}


def serialize_for_container(task: Any, config: ContainerConfig) -> bytes:
    """Serialize a task and its config for transmission to a container.

    Attempts cloudpickle serialization first (for Python containers).
    Falls back to JSON for non-Python or non-picklable payloads.

    Args:
        task: The task object (QueueWrapper or callable) to serialize.
        config: ContainerConfig for the task (controls backend selection).

    Returns:
        Base64-encoded bytes containing the serialized payload.
    """
    try:
        payload = {
            "format": "cloudpickle",
            "data": base64.b64encode(cloudpickle.dumps(task)).decode("utf-8"),
            "config": config.model_dump(),
        }
        return base64.b64encode(json.dumps(payload).encode("utf-8"))
    except Exception:
        # JSON fallback for non-picklable content
        payload = {
            "format": "json",
            "data": None,
            "config": config.model_dump(),
        }
        return base64.b64encode(json.dumps(payload).encode("utf-8"))


def deserialize_from_container(data: bytes, fmt: str) -> Any:
    """Deserialize container output data.

    Args:
        data: Raw bytes from container output (stdout or file).
        fmt: Format hint — 'cloudpickle' or 'json'.

    Returns:
        Deserialized result object.

    Raises:
        ValueError: When the format is unknown or data cannot be decoded.
    """
    if fmt == "cloudpickle":
        return cloudpickle.loads(data)
    elif fmt == "json":
        return json.loads(data)
    else:
        raise ValueError(f"Unknown deserialization format: {fmt!r}")
