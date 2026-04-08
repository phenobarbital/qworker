"""Unit tests for qw/state.py — StateTracker class."""
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
