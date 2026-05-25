# TASK-040: Configuration Extension

**Feature**: launch-docker-k8s
**Spec**: `sdd/specs/launch-docker-k8s.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Adds new configuration variables to `qw/conf.py` for Docker, Kubernetes,
overflow, and container polling settings. Also updates `pyproject.toml` with
optional dependency extras.

Implements Spec Section 3 (Module 9).

---

## Scope

- Modify `qw/conf.py` — add new config vars:
  - `DOCKER_HOST` — Docker daemon URL (default None = local socket)
  - `K8S_NAMESPACE` — Kubernetes namespace (default "default")
  - `K8S_KUBECONFIG` — path to kubeconfig file (default None = auto-detect)
  - `CONTAINER_TASK_MAPPING_FILE` — path to task-to-container mapping YAML (default None)
  - `RESOURCE_OVERFLOW_ENABLED` — enable/disable overflow feature (default False)
  - `RESOURCE_RECOVER_THRESHOLD` — low threshold for recovery (default 75)
  - `CONTAINER_POLL_INTERVAL` — seconds between container status polls (default 5)
  - `CONTAINER_DEFAULT_TIMEOUT` — default container task timeout in minutes (default 30)
- Modify `pyproject.toml` — add optional extras:
  - `[project.optional-dependencies]` section with `docker`, `k8s`, `containers` extras

**NOT in scope**: Using the config vars (other tasks consume them)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `qw/conf.py` | MODIFY | Add new config variables |
| `pyproject.toml` | MODIFY | Add optional extras for docker/k8s/psutil |

---

## Implementation Notes

### Pattern to Follow
```python
# Follow existing conf.py pattern using navconfig:
## Container Execution Backends
DOCKER_HOST = config.get('DOCKER_HOST', fallback=None)
K8S_NAMESPACE = config.get('K8S_NAMESPACE', fallback='default')
K8S_KUBECONFIG = config.get('K8S_KUBECONFIG', fallback=None)
CONTAINER_TASK_MAPPING_FILE = config.get('CONTAINER_TASK_MAPPING_FILE', fallback=None)
RESOURCE_OVERFLOW_ENABLED = config.getboolean('RESOURCE_OVERFLOW_ENABLED', fallback=False)
RESOURCE_RECOVER_THRESHOLD = config.getint('RESOURCE_RECOVER_THRESHOLD', fallback=75)
CONTAINER_POLL_INTERVAL = config.getint('CONTAINER_POLL_INTERVAL', fallback=5)
CONTAINER_DEFAULT_TIMEOUT = config.getint('CONTAINER_DEFAULT_TIMEOUT', fallback=30)
```

### Key Constraints
- Follow exact `navconfig` pattern used by existing vars
- Place new vars after the existing Process Supervisor section
- Group under a clear comment header: `## Container Execution Backends`
- `RESOURCE_OVERFLOW_ENABLED` defaults to False (opt-in feature)
- The existing `RESOURCE_THRESHOLD` (90) and `CHECK_RESOURCE_USAGE` (True) are NOT modified

### pyproject.toml extras
```toml
[project.optional-dependencies]
docker = ["docker>=7.0"]
k8s = ["kubernetes>=29.0"]
containers = ["docker>=7.0", "kubernetes>=29.0", "psutil>=5.9"]
```

### References in Codebase
```python
# qw/conf.py:1 — import pattern
from navconfig import config, ENVIRONMENT, ENV

# qw/conf.py:35-36 — existing resource config (NOT modified)
RESOURCE_THRESHOLD = config.getint('RESOURCE_THRESHOLD', fallback=90)
CHECK_RESOURCE_USAGE = config.getboolean('CHECK_RESOURCE_USAGE', fallback=True)

# qw/conf.py:42-44 — existing health config (for reference)
WORKER_HEALTH_ENABLED = config.getboolean('WORKER_HEALTH_ENABLED', fallback=True)
WORKER_HEALTH_PORT = config.getint('WORKER_HEALTH_PORT', fallback=8080)
```

---

## Codebase Contract

### Verified Imports / Signatures
```python
# qw/conf.py:1
from navconfig import config, ENVIRONMENT, ENV

# navconfig.config methods used in conf.py:
config.get(key, fallback=...)         # returns str
config.getint(key, fallback=...)      # returns int
config.getboolean(key, fallback=...)  # returns bool
config.getlist(key, fallback=...)     # returns list
```

### Does NOT Exist
- No `DOCKER_HOST` in `qw/conf.py` — add it
- No `K8S_NAMESPACE` in `qw/conf.py` — add it
- No `RESOURCE_RECOVER_THRESHOLD` in `qw/conf.py` — add it
- No optional extras in `pyproject.toml` for docker/k8s/psutil

---

## Acceptance Criteria

- [ ] `from qw.conf import DOCKER_HOST, K8S_NAMESPACE, K8S_KUBECONFIG` works
- [ ] `from qw.conf import RESOURCE_OVERFLOW_ENABLED, RESOURCE_RECOVER_THRESHOLD` works
- [ ] `from qw.conf import CONTAINER_POLL_INTERVAL, CONTAINER_DEFAULT_TIMEOUT` works
- [ ] `from qw.conf import CONTAINER_TASK_MAPPING_FILE` works
- [ ] All new vars have correct defaults
- [ ] Existing vars are NOT modified
- [ ] `pyproject.toml` has docker, k8s, containers extras
- [ ] `source .venv/bin/activate && python -c "from qw.conf import DOCKER_HOST"` succeeds

---

## Test Specification

No dedicated test file needed — this is a config-only change. Verification via
import check in acceptance criteria.

---

## Agent Instructions

When you pick up this task:

1. **Read** `qw/conf.py` to understand the pattern
2. **Read** `pyproject.toml` for dependency structure
3. **Add** new config vars to `qw/conf.py`
4. **Add** optional extras to `pyproject.toml`
5. **Verify** imports work: `python -c "from qw.conf import DOCKER_HOST"`
6. **Move** to `sdd/tasks/completed/` and update index

---

## Completion Note

*(Agent fills this in when done)*
