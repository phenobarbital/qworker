# TASK-035: Docker Backend

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-033
**Assigned-to**: unassigned

---

## Context

Implements `DockerBackend` — executes tasks inside Docker containers via the
Docker SDK for Python. Supports local daemon and remote Docker hosts.

Implements Spec Section 3 (Module 4).

---

## Scope

- Implement `qw/backends/docker.py` with `DockerBackend(BaseExecutionBackend)`:
  - `__init__` — connects to Docker daemon (local socket or DOCKER_HOST)
  - `dispatch(task)` — serializes task data, creates container with correct image/env/volumes,
    starts container, returns tracking UUID
  - `poll(task_id)` — `docker inspect` equivalent, returns status string
  - `get_result(task_id)` — reads container stdout or output file, deserializes result
  - `cancel(task_id)` — stops the container
  - `cleanup(task_id)` — removes the container
  - `health_check()` — pings Docker daemon, returns connection status + active container count
- Handle optional import: if `docker` package not installed, raise ImportError with helpful message
- Register "docker" in BackendRegistry
- Write unit tests (mock Docker SDK)

**NOT in scope**: K8s backend (TASK-036), image building, Docker Compose

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/backends/docker.py` | CREATE | DockerBackend implementation |
| `qw/backends/__init__.py` | MODIFY | Conditional export of DockerBackend |
| `tests/test_docker_backend.py` | CREATE | Unit tests with mocked Docker SDK |

---

## Implementation Notes

### Pattern to Follow
```python
import uuid
from navconfig.logging import logging

try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

from .base import BaseExecutionBackend
from .models import TaskResult, ContainerConfig

class DockerBackend(BaseExecutionBackend):
    def __init__(self, docker_host: str | None = None):
        if not HAS_DOCKER:
            raise ImportError(
                "Docker SDK required: uv pip install docker"
            )
        self.logger = logging.getLogger('QW.Backend.Docker')
        self._client = docker.DockerClient(base_url=docker_host) if docker_host else docker.from_env()
        self._tasks: dict[uuid.UUID, str] = {}  # task_id -> container_id
```

### Key Constraints
- Task data passed to container via: env var `TASK_DATA` (base64-encoded cloudpickle/JSON)
  + volume mount for large payloads
- Container `restart_policy` must be `{"Name": "no"}` (Qworker manages retries)
- Poll via `container.status` (created, running, exited)
- Result retrieval: read container logs (stdout) or mounted output file
- Must handle: ImageNotFound, ContainerError, APIError gracefully
- `docker` package is optional — handle ImportError

### References in Codebase
```python
# qw/conf.py — config pattern (will be extended in TASK-040)
DOCKER_HOST = config.get('DOCKER_HOST', fallback=None)

# qw/client.py:525 — cloudpickle serialization pattern
serialized_task = cloudpickle.dumps(func)
encoded_task = base64.b64encode(serialized_task).decode('utf-8')
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/backends/base.py (TASK-033)
class BaseExecutionBackend(ABC):
    async def dispatch(self, task: QueueWrapper) -> uuid.UUID: ...
    async def poll(self, task_id: uuid.UUID) -> str: ...
    async def get_result(self, task_id: uuid.UUID) -> TaskResult: ...
    async def cancel(self, task_id: uuid.UUID) -> bool: ...
    async def cleanup(self, task_id: uuid.UUID) -> None: ...
    async def health_check(self) -> dict: ...

# qw/backends/models.py (TASK-032)
class ContainerConfig(BaseModel):
    backend: Literal["docker", "k8s"]
    image: str
    env: dict[str, str]
    volumes: dict[str, str]
    resources: Optional[ContainerResources] = None
    fire_and_forget: bool = False
    timeout: Optional[int] = None
```

### Does NOT Exist
- No existing Docker integration in `qw/` (only health.py for K8s probes)
- No `docker` package in current dependencies — it's an optional extra
- No `qw/containers/` module — everything goes in `qw/backends/`

---

## Acceptance Criteria

- [ ] `from qw.backends.docker import DockerBackend` works when docker SDK installed
- [ ] ImportError with helpful message when docker SDK not installed
- [ ] `dispatch()` creates a container with correct image, env, volumes
- [ ] `poll()` returns correct status (pending/running/completed/failed)
- [ ] `get_result()` deserializes container output
- [ ] `cancel()` stops running container
- [ ] `cleanup()` removes container
- [ ] `health_check()` reports Docker daemon status
- [ ] All tests pass: `pytest tests/test_docker_backend.py -v`

---

## Test Specification

```python
import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch

class TestDockerBackend:
    @pytest.fixture
    def mock_docker_client(self):
        with patch("qw.backends.docker.docker") as mock_docker:
            client = MagicMock()
            mock_docker.from_env.return_value = client
            yield client

    def test_import_error_without_sdk(self):
        # Test that ImportError is raised with helpful message
        pass

    @pytest.mark.asyncio
    async def test_dispatch_creates_container(self, mock_docker_client):
        pass

    @pytest.mark.asyncio
    async def test_poll_running_container(self, mock_docker_client):
        pass

    @pytest.mark.asyncio
    async def test_get_result_from_exited_container(self, mock_docker_client):
        pass

    @pytest.mark.asyncio
    async def test_cleanup_removes_container(self, mock_docker_client):
        pass

    @pytest.mark.asyncio
    async def test_health_check_ping(self, mock_docker_client):
        mock_docker_client.ping.return_value = True
        pass
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/launch-docker-k8s.spec.md`
2. **Check dependencies** — verify TASK-033 is completed
3. **Implement** `qw/backends/docker.py`
4. **Run tests**: `pytest tests/test_docker_backend.py -v`
5. **Verify** acceptance criteria
6. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

*(Agent fills this in when done)*
