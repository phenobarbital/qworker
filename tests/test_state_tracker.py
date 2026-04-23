"""Unit tests for qw/state.py — StateTracker class."""
import time
import pytest
from multiprocessing import Manager
from qw.state import StateTracker


@pytest.fixture
def shared_state():
    """Provides a real multiprocessing.Manager shared dict for testing."""
    manager = Manager()
    state = manager.dict()
    yield state
    manager.shutdown()


@pytest.fixture
def tracker(shared_state):
    """Provides a StateTracker instance with a real shared dict."""
    return StateTracker(shared_state, worker_name="TestWorker_0", pid=12345)


class TestStateTrackerInit:
    def test_init_creates_worker_entry(self, tracker, shared_state):
        """Worker entry exists in shared state after init."""
        assert "TestWorker_0" in shared_state

    def test_init_pid_stored(self, tracker, shared_state):
        """PID is stored in worker entry."""
        state = shared_state["TestWorker_0"]
        assert state["pid"] == 12345

    def test_init_empty_lists(self, tracker, shared_state):
        """All source lists start empty."""
        state = shared_state["TestWorker_0"]
        assert state["queue"] == []
        assert state["tcp_executing"] == []
        assert state["redis_executing"] == []
        assert state["broker_executing"] == []
        assert state["completed"] == []


class TestTaskQueued:
    def test_task_queued_adds_entry(self, tracker, shared_state):
        """task_queued adds entry with correct fields."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 1

    def test_task_queued_task_id(self, tracker, shared_state):
        """task_queued stores task_id correctly."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert state["queue"][0]["task_id"] == "abc-123"

    def test_task_queued_function_name(self, tracker, shared_state):
        """task_queued stores function_name correctly."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert state["queue"][0]["function_name"] == "process_data"

    def test_task_queued_status(self, tracker, shared_state):
        """task_queued sets status to 'queued'."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert state["queue"][0]["status"] == "queued"

    def test_task_queued_enqueued_at(self, tracker, shared_state):
        """task_queued sets enqueued_at timestamp."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert state["queue"][0]["enqueued_at"] is not None
        assert isinstance(state["queue"][0]["enqueued_at"], float)

    def test_task_queued_retries_zero(self, tracker, shared_state):
        """task_queued sets retries to 0."""
        tracker.task_queued("abc-123", "process_data")
        state = shared_state["TestWorker_0"]
        assert state["queue"][0]["retries"] == 0

    def test_task_queued_multiple(self, tracker, shared_state):
        """Multiple tasks can be queued."""
        tracker.task_queued("abc-001", "func_a")
        tracker.task_queued("abc-002", "func_b")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 2


class TestTaskExecuting:
    def test_task_executing_from_queue(self, tracker, shared_state):
        """task_executing transitions queued task to executing."""
        tracker.task_queued("abc-123", "process_data")
        tracker.task_executing("abc-123", source="queue")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 1
        assert state["queue"][0]["status"] == "executing"

    def test_task_executing_sets_started_at(self, tracker, shared_state):
        """task_executing sets started_at timestamp."""
        tracker.task_queued("abc-123", "process_data")
        tracker.task_executing("abc-123", source="queue")
        state = shared_state["TestWorker_0"]
        assert state["queue"][0]["started_at"] is not None

    def test_task_executing_tcp(self, tracker, shared_state):
        """task_executing for TCP source adds to tcp_executing."""
        tracker.task_executing("abc-123", source="tcp")
        state = shared_state["TestWorker_0"]
        assert len(state["tcp_executing"]) == 1
        assert state["tcp_executing"][0]["task_id"] == "abc-123"

    def test_task_executing_redis(self, tracker, shared_state):
        """task_executing for redis source adds to redis_executing."""
        tracker.task_executing("abc-redis", source="redis")
        state = shared_state["TestWorker_0"]
        assert len(state["redis_executing"]) == 1
        assert state["redis_executing"][0]["task_id"] == "abc-redis"

    def test_task_executing_broker(self, tracker, shared_state):
        """task_executing for broker source adds to broker_executing."""
        tracker.task_executing("abc-broker", source="broker")
        state = shared_state["TestWorker_0"]
        assert len(state["broker_executing"]) == 1


class TestTaskCompleted:
    def test_task_completed_moves_to_completed(self, tracker, shared_state):
        """task_completed moves task to completed list."""
        tracker.task_queued("abc-123", "process_data")
        tracker.task_executing("abc-123", source="queue")
        tracker.task_completed("abc-123", result="success", source="queue")
        state = shared_state["TestWorker_0"]
        assert len(state["queue"]) == 0
        assert len(state["completed"]) == 1

    def test_task_completed_result_success(self, tracker, shared_state):
        """Completed task has correct result."""
        tracker.task_queued("abc-123", "process_data")
        tracker.task_executing("abc-123", source="queue")
        tracker.task_completed("abc-123", result="success", source="queue")
        state = shared_state["TestWorker_0"]
        assert state["completed"][0]["result"] == "success"

    def test_task_completed_result_error(self, tracker, shared_state):
        """Error result recorded correctly."""
        tracker.task_executing("abc-err", source="tcp")
        tracker.task_completed("abc-err", result="error", source="tcp")
        state = shared_state["TestWorker_0"]
        assert state["completed"][0]["result"] == "error"

    def test_task_completed_status(self, tracker, shared_state):
        """Completed task has status 'completed'."""
        tracker.task_executing("abc-123", source="tcp")
        tracker.task_completed("abc-123", result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        assert state["completed"][0]["status"] == "completed"

    def test_task_completed_duration(self, tracker, shared_state):
        """Completed task has duration set."""
        tracker.task_executing("abc-123", source="tcp")
        tracker.task_completed("abc-123", result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        assert state["completed"][0]["duration"] is not None
        assert state["completed"][0]["duration"] >= 0

    def test_task_completed_removes_from_tcp_executing(self, tracker, shared_state):
        """Completing a TCP task removes it from tcp_executing."""
        tracker.task_executing("abc-123", source="tcp")
        tracker.task_completed("abc-123", result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        assert len(state["tcp_executing"]) == 0

    def test_task_completed_removes_from_redis_executing(self, tracker, shared_state):
        """Completing a redis task removes it from redis_executing."""
        tracker.task_executing("abc-redis", source="redis")
        tracker.task_completed("abc-redis", result="success", source="redis")
        state = shared_state["TestWorker_0"]
        assert len(state["redis_executing"]) == 0


class TestCompletedRingBuffer:
    def test_completed_ring_buffer_exact_max(self, tracker, shared_state):
        """Completed list never exceeds MAX_COMPLETED entries."""
        for i in range(15):
            tid = f"task-{i}"
            tracker.task_executing(tid, source="tcp")
            tracker.task_completed(tid, result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        assert len(state["completed"]) == 10

    def test_completed_ring_buffer_oldest_evicted(self, tracker, shared_state):
        """Oldest entries are evicted when ring buffer overflows."""
        for i in range(15):
            tid = f"task-{i}"
            tracker.task_executing(tid, source="tcp")
            tracker.task_completed(tid, result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        # The 15 tasks are task-0 through task-14.
        # With max=10, we keep the last 10: task-5 through task-14.
        assert state["completed"][0]["task_id"] == "task-5"

    def test_completed_ring_buffer_under_max(self, tracker, shared_state):
        """When fewer than MAX_COMPLETED tasks complete, all are retained."""
        for i in range(5):
            tid = f"task-{i}"
            tracker.task_executing(tid, source="tcp")
            tracker.task_completed(tid, result="success", source="tcp")
        state = shared_state["TestWorker_0"]
        assert len(state["completed"]) == 5


class TestGetState:
    def test_get_state_returns_dict(self, tracker):
        """get_state returns a plain dict."""
        state = tracker.get_state()
        assert isinstance(state, dict)

    def test_get_state_contains_pid(self, tracker):
        """get_state includes the pid field."""
        state = tracker.get_state()
        assert "pid" in state
        assert state["pid"] == 12345

    def test_get_state_contains_all_keys(self, tracker):
        """get_state contains all expected keys."""
        state = tracker.get_state()
        for key in ("pid", "queue", "tcp_executing", "redis_executing",
                    "broker_executing", "completed"):
            assert key in state


class TestGetFunctionName:
    def test_func_wrapper_name(self, tracker):
        """_get_function_name extracts name from FuncWrapper."""
        from qw.wrappers.func import FuncWrapper

        async def my_function():
            pass

        wrapper = FuncWrapper(host="localhost", func=my_function, queued=True)
        name = tracker._get_function_name(wrapper)
        assert name == "my_function"

    def test_task_wrapper_repr(self, tracker):
        """_get_function_name falls back to repr for TaskWrapper."""
        try:
            from qw.wrappers.di_task import TaskWrapper
        except Exception:
            pytest.skip("TaskWrapper not available in this environment (flowtask not configured)")
        wrapper = TaskWrapper(program="my_prog", task="my_task")
        name = tracker._get_function_name(wrapper)
        assert "my_task" in name or "my_prog" in name

    def test_unknown_fallback(self, tracker):
        """_get_function_name returns a string for unrecognized objects."""
        # We can't test TaskWrapper fallback when flowtask isn't installed,
        # so test with a plain QueueWrapper instead.
        from qw.wrappers.base import QueueWrapper
        wrapper = QueueWrapper(queued=True)
        name = tracker._get_function_name(wrapper)
        assert isinstance(name, str)


# ======================================================================
# FEAT-005 — Heartbeat / Status / Task Ledger tests (TASK-025)
# ======================================================================


class TestHeartbeat:
    def test_initial_heartbeat_is_zero(self, tracker):
        """Before update_heartbeat() is called, heartbeat is 0.0."""
        assert tracker.get_heartbeat() == 0.0

    def test_update_heartbeat_writes_timestamp(self, tracker):
        """update_heartbeat() writes the current time to shared state."""
        before = time.time()
        tracker.update_heartbeat()
        after = time.time()
        hb = tracker.get_heartbeat()
        assert before <= hb <= after

    def test_update_heartbeat_is_idempotent(self, tracker):
        """Multiple updates keep the most recent timestamp."""
        tracker.update_heartbeat()
        first = tracker.get_heartbeat()
        time.sleep(0.01)
        tracker.update_heartbeat()
        second = tracker.get_heartbeat()
        assert second >= first

    def test_heartbeat_visible_in_shared_state(self, tracker, shared_state):
        """Heartbeat is written to shared_state[worker_name]['heartbeat']."""
        tracker.update_heartbeat()
        d = dict(shared_state["TestWorker_0"])
        assert "heartbeat" in d
        assert d["heartbeat"] > 0


class TestStatus:
    def test_initial_status_is_healthy(self, tracker):
        """Default status is 'healthy'."""
        assert tracker.get_status() == "healthy"

    def test_set_status_draining(self, tracker, shared_state):
        """set_status('draining', ts) sets both status and draining_since."""
        now = time.time()
        tracker.set_status("draining", draining_since=now)
        assert tracker.get_status() == "draining"
        d = dict(shared_state["TestWorker_0"])
        assert d["draining_since"] == now

    def test_set_status_back_to_healthy_clears_draining_since(
        self, tracker, shared_state
    ):
        """Clearing to healthy sets draining_since back to None."""
        tracker.set_status("draining", draining_since=time.time())
        tracker.set_status("healthy")
        assert tracker.get_status() == "healthy"
        d = dict(shared_state["TestWorker_0"])
        assert d["draining_since"] is None

    def test_status_initialized_in_shared_state(self, tracker, shared_state):
        """status/draining_since keys exist after init."""
        d = dict(shared_state["TestWorker_0"])
        assert d["status"] == "healthy"
        assert d["draining_since"] is None


class TestTaskLedger:
    def test_initial_ledger_is_empty(self, tracker, shared_state):
        """task_ledger starts as an empty list."""
        d = dict(shared_state["TestWorker_0"])
        assert d["task_ledger"] == []

    def test_ledger_add_single_entry(self, tracker, shared_state):
        """ledger_add appends an entry with task_id/payload/enqueued_at."""
        tracker.ledger_add("id-1", "payload-1")
        d = dict(shared_state["TestWorker_0"])
        assert len(d["task_ledger"]) == 1
        entry = d["task_ledger"][0]
        assert entry["task_id"] == "id-1"
        assert entry["payload"] == "payload-1"
        assert isinstance(entry["enqueued_at"], float)

    def test_ledger_add_multiple_entries(self, tracker, shared_state):
        """Multiple adds accumulate in order."""
        tracker.ledger_add("id-1", "payload-1")
        tracker.ledger_add("id-2", "payload-2")
        d = dict(shared_state["TestWorker_0"])
        assert len(d["task_ledger"]) == 2
        assert d["task_ledger"][0]["task_id"] == "id-1"
        assert d["task_ledger"][1]["task_id"] == "id-2"

    def test_ledger_remove_by_task_id(self, tracker, shared_state):
        """ledger_remove drops the matching entry, keeps the rest."""
        tracker.ledger_add("id-1", "payload-1")
        tracker.ledger_add("id-2", "payload-2")
        tracker.ledger_remove("id-1")
        d = dict(shared_state["TestWorker_0"])
        assert len(d["task_ledger"]) == 1
        assert d["task_ledger"][0]["task_id"] == "id-2"

    def test_ledger_remove_nonexistent_is_noop(self, tracker, shared_state):
        """Removing a missing task_id does not raise."""
        tracker.ledger_add("id-1", "payload-1")
        tracker.ledger_remove("does-not-exist")
        d = dict(shared_state["TestWorker_0"])
        assert len(d["task_ledger"]) == 1

    def test_ledger_drain_returns_all_and_clears(self, tracker, shared_state):
        """ledger_drain returns entries and empties the ledger."""
        tracker.ledger_add("id-1", "payload-1")
        tracker.ledger_add("id-2", "payload-2")
        entries = tracker.ledger_drain()
        assert len(entries) == 2
        assert {e["task_id"] for e in entries} == {"id-1", "id-2"}
        d = dict(shared_state["TestWorker_0"])
        assert d["task_ledger"] == []

    def test_ledger_drain_empty_returns_empty_list(self, tracker):
        """Draining an empty ledger returns []."""
        assert tracker.ledger_drain() == []
