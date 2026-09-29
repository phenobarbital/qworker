# TASK-032: Container Configuration Models

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Foundation task for FEAT-006. Creates the Pydantic data models that every other
task in this feature depends on: `ContainerConfig`, `ContainerResources`,
`ContainerTaskMapping`, and `TaskResult`. Also provides serialization helpers
for cloudpickle and JSON.

Implements Spec Section 2 (Data Models) and Section 3 (Module 1).

---

## Scope

- Create `qw/backends/__init__.py` (empty package init)
- Implement `qw/backends/models.py` with:
  - `ContainerResources` Pydantic model (cpu_limit, memory_limit, cpu_request, memory_request)
  - `ContainerConfig` Pydantic model (backend, image, env, volumes, resources, fire_and_forget, timeout, namespace)
  - `ContainerTaskMapping` Pydantic model (task_pattern, config)
  - `TaskResult` Pydantic model (task_id, success, result, error, execution_time, backend)
  - `serialize_for_container(task, config) -> bytes` helper: cloudpickle for Python, JSON fallback
  - `deserialize_from_container(data: bytes, format: str) -> Any` helper
- Write unit tests

**NOT in scope**: Backend abstraction (TASK-033), wrapper changes (TASK-038), config vars (TASK-040)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/__init__.py` | CREATE | Package init, exports models |
| `qw/backends/models.py` | CREATE | Pydantic models + serialization helpers |
| `tests/test_container_models.py` | CREATE | Unit tests |

---

## Implementation Notes

### Pattern to Follow
```python
# Follow the Pydantic pattern used throughout the project.
# See spec Section 2 (Data Models) for exact field definitions.
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any
import uuid

class ContainerResources(BaseModel):
    cpu_limit: Optional[str] = Field(None, description="CPU limit, e.g. '500m', '2'")
    memory_limit: Optional[str] = Field(None, description="Memory limit, e.g. '512Mi', '2Gi'")
    cpu_request: Optional[str] = Field(None, description="CPU request (K8s only)")
    memory_request: Optional[str] = Field(None, description="Memory request (K8s only)")
```

### Key Constraints
- `backend` field must be `Literal["docker", "k8s"]`
- `image` is required (no default)
- `fire_and_forget` defaults to `False`
- `TaskResult.result` is `Any` — must handle non-serializable results gracefully
- Serialization: cloudpickle for Python containers, JSON for others
- All models must be importable: `from qw.backends.models import ContainerConfig`

### References in Codebase
- `qw/wrappers/base.py` — QueueWrapper (will consume ContainerConfig in TASK-038)
- `qw/client.py:525` — cloudpickle.dumps() usage pattern for serialization

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/wrappers/base.py — QueueWrapper (line 10)
class QueueWrapper:
    def __init__(self, coro=None, *args, **kwargs):
        self._id: uuid.UUID = kwargs.pop('id', uuid.uuid4())
        self.args = args
        self.kwargs = kwargs
        self.retries = 0
        self.coro = coro

# qw/client.py:525 — serialization pattern
serialized_task = cloudpickle.dumps(func)
encoded_task = base64.b64encode(serialized_task).decode('utf-8')
```

### Does NOT Exist
- `qw/backends/` directory — must be created
- `qw/models.py` — there is no project-level models file
- No existing Pydantic models in `qw/` — this is the first
- No existing `BaseModel` import anywhere in `qw/` package

---

## Acceptance Criteria

- [ ] `from qw.backends.models import ContainerConfig, ContainerResources, ContainerTaskMapping, TaskResult`
- [ ] `ContainerConfig(backend="docker", image="python:3.12")` creates valid instance
- [ ] `ContainerConfig(backend="invalid", image="x")` raises ValidationError
- [ ] Cloudpickle roundtrip: `deserialize(serialize(task, config))` recovers data
- [ ] JSON serialization works for JSON-serializable task data
- [ ] All tests pass: `pytest tests/test_container_models.py -v`

---

## Test Specification

```python
# tests/test_container_models.py
import pytest
from qw.backends.models import (
    ContainerConfig, ContainerResources,
    ContainerTaskMapping, TaskResult,
)


class TestContainerConfig:
    def test_valid_docker_config(self):
        cfg = ContainerConfig(backend="docker", image="python:3.12-slim")
        assert cfg.backend == "docker"
        assert cfg.fire_and_forget is False

    def test_valid_k8s_config(self):
        cfg = ContainerConfig(
            backend="k8s",
            image="registry.example.com/worker:latest",
            namespace="prod",
        )
        assert cfg.namespace == "prod"

    def test_invalid_backend_rejected(self):
        with pytest.raises(Exception):
            ContainerConfig(backend="invalid", image="x")

    def test_defaults(self):
        cfg = ContainerConfig(backend="docker", image="x")
        assert cfg.env == {}
        assert cfg.volumes == {}
        assert cfg.resources is None
        assert cfg.timeout is None

    def test_with_resources(self):
        res = ContainerResources(cpu_limit="500m", memory_limit="512Mi")
        cfg = ContainerConfig(backend="docker", image="x", resources=res)
        assert cfg.resources.cpu_limit == "500m"


class TestContainerTaskMapping:
    def test_mapping(self):
        cfg = ContainerConfig(backend="docker", image="x")
        m = ContainerTaskMapping(task_pattern="etl_*", config=cfg)
        assert m.task_pattern == "etl_*"


class TestTaskResult:
    def test_success_result(self):
        r = TaskResult(
            task_id="550e8400-e29b-41d4-a716-446655440000",
            success=True,
            result={"data": 42},
            error=None,
            execution_time=1.5,
            backend="docker",
        )
        assert r.success is True
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md` for full context
2. **Check dependencies** — this task has none
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Create** `qw/backends/__init__.py` and `qw/backends/models.py`
5. **Implement** all models per scope
6. **Run tests**: `pytest tests/test_container_models.py -v`
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-032-container-config-models.md`
9. **Update index** → `"done"`

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (sdd-worker)
**Date**: 2026-05-25
**Notes**: All 20 unit tests pass. deserialize_from_container accepts raw bytes for cloudpickle (no double-decode).
**Deviations from spec**: none
