"""Unit tests for qw.backends.models — TASK-032."""
import base64
import uuid

import pytest

from qw.backends.models import (
    ContainerConfig,
    ContainerResources,
    ContainerTaskMapping,
    TaskResult,
    deserialize_from_container,
    serialize_for_container,
)


class TestContainerConfig:
    """Tests for ContainerConfig Pydantic model."""

    def test_valid_docker_config(self):
        """ContainerConfig accepts a valid docker backend config."""
        cfg = ContainerConfig(backend="docker", image="python:3.12-slim")
        assert cfg.backend == "docker"
        assert cfg.fire_and_forget is False

    def test_valid_k8s_config(self):
        """ContainerConfig accepts a valid k8s backend config with namespace."""
        cfg = ContainerConfig(
            backend="k8s",
            image="registry.example.com/worker:latest",
            namespace="prod",
        )
        assert cfg.namespace == "prod"

    def test_invalid_backend_rejected(self):
        """ContainerConfig raises ValidationError for unsupported backends."""
        with pytest.raises(Exception):
            ContainerConfig(backend="invalid", image="x")

    def test_defaults(self):
        """ContainerConfig has correct default values for optional fields."""
        cfg = ContainerConfig(backend="docker", image="x")
        assert cfg.env == {}
        assert cfg.volumes == {}
        assert cfg.resources is None
        assert cfg.timeout is None
        assert cfg.namespace is None
        assert cfg.fire_and_forget is False

    def test_with_resources(self):
        """ContainerConfig stores ContainerResources correctly."""
        res = ContainerResources(cpu_limit="500m", memory_limit="512Mi")
        cfg = ContainerConfig(backend="docker", image="x", resources=res)
        assert cfg.resources.cpu_limit == "500m"
        assert cfg.resources.memory_limit == "512Mi"

    def test_with_env_and_volumes(self):
        """ContainerConfig stores env vars and volume mounts."""
        cfg = ContainerConfig(
            backend="docker",
            image="myimage:latest",
            env={"MY_VAR": "value"},
            volumes={"/host/path": "/container/path"},
        )
        assert cfg.env == {"MY_VAR": "value"}
        assert cfg.volumes == {"/host/path": "/container/path"}

    def test_fire_and_forget_flag(self):
        """ContainerConfig stores fire_and_forget=True when set."""
        cfg = ContainerConfig(backend="docker", image="x", fire_and_forget=True)
        assert cfg.fire_and_forget is True

    def test_timeout_override(self):
        """ContainerConfig stores timeout override in minutes."""
        cfg = ContainerConfig(backend="docker", image="x", timeout=60)
        assert cfg.timeout == 60


class TestContainerResources:
    """Tests for ContainerResources Pydantic model."""

    def test_all_fields_optional(self):
        """ContainerResources can be created with no fields."""
        res = ContainerResources()
        assert res.cpu_limit is None
        assert res.memory_limit is None
        assert res.cpu_request is None
        assert res.memory_request is None

    def test_k8s_request_fields(self):
        """ContainerResources stores K8s request fields."""
        res = ContainerResources(
            cpu_request="100m",
            cpu_limit="500m",
            memory_request="128Mi",
            memory_limit="512Mi",
        )
        assert res.cpu_request == "100m"
        assert res.cpu_limit == "500m"
        assert res.memory_request == "128Mi"
        assert res.memory_limit == "512Mi"


class TestContainerTaskMapping:
    """Tests for ContainerTaskMapping Pydantic model."""

    def test_mapping(self):
        """ContainerTaskMapping stores pattern and config."""
        cfg = ContainerConfig(backend="docker", image="x")
        m = ContainerTaskMapping(task_pattern="etl_*", config=cfg)
        assert m.task_pattern == "etl_*"
        assert m.config.backend == "docker"

    def test_mapping_with_k8s_config(self):
        """ContainerTaskMapping works with K8s backend config."""
        cfg = ContainerConfig(backend="k8s", image="worker:latest", namespace="prod")
        m = ContainerTaskMapping(task_pattern="heavy_task", config=cfg)
        assert m.config.namespace == "prod"


class TestTaskResult:
    """Tests for TaskResult Pydantic model."""

    def test_success_result(self):
        """TaskResult stores a successful result."""
        r = TaskResult(
            task_id="550e8400-e29b-41d4-a716-446655440000",
            success=True,
            result={"data": 42},
            error=None,
            execution_time=1.5,
            backend="docker",
        )
        assert r.success is True
        assert r.result == {"data": 42}
        assert r.error is None
        assert r.backend == "docker"
        assert r.execution_time == 1.5

    def test_failed_result(self):
        """TaskResult stores an error state."""
        r = TaskResult(
            task_id=uuid.uuid4(),
            success=False,
            result=None,
            error="Container exited with code 1",
            execution_time=0.3,
            backend="k8s",
        )
        assert r.success is False
        assert r.error == "Container exited with code 1"

    def test_task_id_as_uuid(self):
        """TaskResult accepts both UUID and string for task_id."""
        uid = uuid.uuid4()
        r = TaskResult(
            task_id=uid,
            success=True,
            result=None,
            error=None,
            execution_time=0.1,
            backend="local",
        )
        assert r.task_id == uid

    def test_any_result_type(self):
        """TaskResult.result can be any type."""
        for value in [42, "string", [1, 2, 3], {"key": "val"}, None, 3.14]:
            r = TaskResult(
                task_id=uuid.uuid4(),
                success=True,
                result=value,
                error=None,
                execution_time=0.1,
                backend="local",
            )
            assert r.result == value


class TestSerializationHelpers:
    """Tests for serialize_for_container and deserialize_from_container."""

    def test_serialize_returns_bytes(self):
        """serialize_for_container returns bytes."""
        cfg = ContainerConfig(backend="docker", image="python:3.12")
        result = serialize_for_container({"task": "data"}, cfg)
        assert isinstance(result, bytes)

    def test_cloudpickle_roundtrip(self):
        """Cloudpickle serialization roundtrip works for simple Python objects."""
        import cloudpickle
        data = {"key": "value", "number": 42}
        raw_bytes = cloudpickle.dumps(data)
        recovered = deserialize_from_container(raw_bytes, "cloudpickle")
        assert recovered == data

    def test_json_deserialization(self):
        """deserialize_from_container handles JSON format."""
        import json
        data = {"result": "success", "value": 100}
        json_bytes = json.dumps(data).encode("utf-8")
        recovered = deserialize_from_container(json_bytes, "json")
        assert recovered == data

    def test_unknown_format_raises(self):
        """deserialize_from_container raises ValueError for unknown format."""
        with pytest.raises(ValueError, match="Unknown deserialization format"):
            deserialize_from_container(b"data", "unknown_format")
