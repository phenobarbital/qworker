"""StateTracker — Shared state management for QWorker observability.

Wraps a multiprocessing.Manager DictProxy and provides safe, atomic
updates for task lifecycle events (queued, executing, completed).

CRITICAL: Manager proxies do NOT detect nested mutations.
  WRONG: shared_state[key]["queue"].append(item)  # change is LOST
  RIGHT: d = dict(shared_state[key]); d["queue"] = [...]; shared_state[key] = d
"""
import time


class StateTracker:
    """Thin wrapper around multiprocessing.Manager shared dict.

    Provides atomic task lifecycle state updates that are safely
    propagated across process boundaries via the Manager proxy.

    Args:
        shared_state: A multiprocessing.Manager().dict() proxy object.
        worker_name: Human-readable name of this worker process.
        pid: PID of this worker process.
    """

    MAX_COMPLETED = 10

    def __init__(self, shared_state: dict, worker_name: str, pid: int):
        self._state = shared_state
        self._worker_name = worker_name
        self._pid = pid
        # Initialize this worker's entry in the shared dict (full assignment for proxy)
        self._state[worker_name] = {
            "pid": pid,
            "queue": [],
            "tcp_executing": [],
            "redis_executing": [],
            "broker_executing": [],
            "completed": []
        }

    # ------------------------------------------------------------------
    # Helper: function name extraction
    # ------------------------------------------------------------------

    def _get_function_name(self, task) -> str:
        """Extract a human-readable function name from a task wrapper.

        Checks isinstance in order: FuncWrapper first (has .func attribute),
        then falls back to repr(), then '<unknown>'.
        """
        # Lazy imports to avoid circular dependencies; these are only used
        # for isinstance checks and the import is cheap after first load.
        try:
            from qw.wrappers.func import FuncWrapper
            if isinstance(task, FuncWrapper):
                try:
                    return task.func.__name__
                except AttributeError:
                    return repr(task)
        except ImportError:
            pass

        try:
            from qw.wrappers.di_task import TaskWrapper
            if isinstance(task, TaskWrapper):
                return repr(task)
        except Exception:
            pass

        # Generic fallback — covers plain QueueWrapper and any other type
        try:
            name = repr(task)
            if name:
                return name
        except Exception:
            pass

        return "<unknown>"

    # ------------------------------------------------------------------
    # Source-list mapping
    # ------------------------------------------------------------------

    _SOURCE_KEYS = {
        "queue": "queue",
        "tcp": "tcp_executing",
        "redis": "redis_executing",
        "broker": "broker_executing",
    }

    def _list_key_for_source(self, source: str) -> str:
        return self._SOURCE_KEYS.get(source, "tcp_executing")

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    def task_queued(self, task_id: str, function_name: str) -> None:
        """Record a task being added to the asyncio queue (source='queue').

        Args:
            task_id: String UUID of the task.
            function_name: Human-readable function name.
        """
        entry = {
            "task_id": task_id,
            "function_name": function_name,
            "enqueued_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "duration": None,
            "retries": 0,
            "status": "queued",
            "result": "",
            "source": "queue",
        }
        # Atomic full-value replacement (Manager proxy requirement)
        d = dict(self._state[self._worker_name])
        d["queue"] = list(d["queue"]) + [entry]
        self._state[self._worker_name] = d

    def task_executing(self, task_id: str, source: str) -> None:
        """Transition a task to executing state.

        For 'queue' source: finds the task in queue list and updates it
        in-place (status → executing, started_at set).
        For other sources (tcp, redis, broker): creates a new entry
        directly in the appropriate executing list (tasks are consumed
        immediately, never passed through the asyncio queue first).

        Args:
            task_id: String UUID of the task.
            source: One of "queue", "tcp", "redis", "broker".
        """
        list_key = self._list_key_for_source(source)
        d = dict(self._state[self._worker_name])
        now = time.time()

        if source == "queue":
            # Find in queue list and update status in-place
            new_queue = []
            for item in list(d["queue"]):
                item = dict(item)
                if item["task_id"] == task_id:
                    item["status"] = "executing"
                    item["started_at"] = now
                new_queue.append(item)
            d["queue"] = new_queue
        else:
            # Create a new entry in the appropriate executing list
            entry = {
                "task_id": task_id,
                "function_name": "",
                "enqueued_at": now,
                "started_at": now,
                "completed_at": None,
                "duration": None,
                "retries": 0,
                "status": "executing",
                "result": "",
                "source": source,
            }
            d[list_key] = list(d[list_key]) + [entry]

        self._state[self._worker_name] = d

    def task_completed(self, task_id: str, result: str, source: str) -> None:
        """Move a task to the completed ring buffer (max MAX_COMPLETED).

        Removes the task from its active list, sets completion timestamps,
        and prepends to the completed list. Oldest entries are evicted when
        the list exceeds MAX_COMPLETED.

        Args:
            task_id: String UUID of the task.
            result: "success" or "error" (or an error message string).
            source: One of "queue", "tcp", "redis", "broker".
        """
        list_key = self._list_key_for_source(source)
        d = dict(self._state[self._worker_name])
        now = time.time()

        # Find and remove the task from its source list
        found_entry = None
        remaining = []
        for item in list(d[list_key]):
            item = dict(item)
            if item["task_id"] == task_id and found_entry is None:
                found_entry = item
            else:
                remaining.append(item)
        d[list_key] = remaining

        # Build completed entry (use found or create minimal)
        if found_entry is not None:
            completed_entry = dict(found_entry)
        else:
            completed_entry = {
                "task_id": task_id,
                "function_name": "",
                "enqueued_at": now,
                "started_at": now,
                "source": source,
            }

        completed_entry["completed_at"] = now
        started = completed_entry.get("started_at") or now
        completed_entry["duration"] = now - started
        completed_entry["status"] = "completed"
        completed_entry["result"] = result

        # Append to completed and cap at MAX_COMPLETED (evict oldest)
        completed_list = list(d["completed"]) + [completed_entry]
        if len(completed_list) > self.MAX_COMPLETED:
            completed_list = completed_list[-self.MAX_COMPLETED:]
        d["completed"] = completed_list

        self._state[self._worker_name] = d

    def get_state(self) -> dict:
        """Return this worker's current state as a plain dict copy."""
        raw = self._state.get(self._worker_name, {})
        # Convert to plain dict (from Manager proxy mapping)
        return dict(raw)

    def get_all_states(self) -> dict:
        """Return state for ALL worker processes as a plain dict.

        Returns:
            dict: Mapping of worker_name → state dict for every worker
            registered in the shared multiprocessing Manager dict.
        """
        return {name: dict(state) for name, state in self._state.items()}
