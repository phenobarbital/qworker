"""Unit tests for QueueSizePolicy (TASK-017).

Tests cover all acceptance criteria rows from spec §4 Module 1.
"""
import pytest
from qw.queues import QueueSizePolicy


@pytest.fixture
def policy() -> QueueSizePolicy:
    return QueueSizePolicy(
        base_size=4,
        grow_margin=2,
        warn_ratio=0.8,
        shrink_cooldown=10.0,
        low_watermark_ratio=0.5,
    )


class TestQueueSizePolicy:
    def test_policy_default_initial_maxsize(self, policy):
        assert policy.current_maxsize == 4
        assert policy.ceiling() == 6

    def test_policy_should_warn_at_threshold(self, policy):
        # maxsize=4, warn_ratio=0.8 → warn at size >= 4 (since 3/4=0.75 < 0.8)
        assert policy.should_warn(3) is False
        assert policy.should_warn(4) is True

    def test_policy_can_grow_until_margin(self, policy):
        assert policy.can_grow() is True
        policy.register_grow()
        assert policy.current_maxsize == 5
        policy.register_grow()
        assert policy.current_maxsize == 6
        assert policy.can_grow() is False

    def test_policy_register_grow_past_ceiling_raises(self, policy):
        policy.register_grow()
        policy.register_grow()
        with pytest.raises(RuntimeError):
            policy.register_grow()

    def test_policy_can_shrink_after_cooldown(self, policy):
        policy.register_grow()  # maxsize=5
        # Simulate time past cooldown AND size below low watermark
        policy.note_high_usage(now=0.0)
        # size=2, maxsize=5 → ratio=0.4 <= 0.5, elapsed=11.0 > 10.0
        assert policy.can_shrink(current_size=2, now=11.0) is True

    def test_policy_cannot_shrink_before_cooldown(self, policy):
        policy.register_grow()
        policy.note_high_usage(now=0.0)
        assert policy.can_shrink(current_size=2, now=5.0) is False  # elapsed=5 < 10

    def test_policy_cannot_shrink_at_base(self, policy):
        # current_maxsize == base_size, can_shrink must be False
        assert policy.can_shrink(current_size=0, now=1e9) is False

    def test_policy_register_shrink_floors_at_base(self, policy):
        policy.register_grow()
        policy.register_shrink()
        policy.register_shrink()  # already at base, should not go below
        assert policy.current_maxsize == policy.base_size

    def test_policy_counters(self, policy):
        policy.register_grow()
        policy.register_discard()
        assert policy.grow_events == 1
        assert policy.discard_events == 1

    def test_policy_rejects_invalid_config(self):
        with pytest.raises(ValueError):
            QueueSizePolicy(base_size=0)
        with pytest.raises(ValueError):
            QueueSizePolicy(base_size=4, grow_margin=-1)
        with pytest.raises(ValueError):
            QueueSizePolicy(base_size=4, warn_ratio=1.5)

    def test_policy_from_config_picks_up_defaults(self, monkeypatch):
        import qw.conf as c
        monkeypatch.setattr(c, "WORKER_QUEUE_GROW_MARGIN", 3, raising=False)
        monkeypatch.setattr(c, "WORKER_QUEUE_WARN_THRESHOLD", 0.9, raising=False)
        monkeypatch.setattr(c, "WORKER_QUEUE_SHRINK_COOLDOWN", 5, raising=False)
        p = QueueSizePolicy.from_config(base_size=4)
        assert p.grow_margin == 3
        assert p.warn_ratio == 0.9
        assert p.shrink_cooldown == 5.0
