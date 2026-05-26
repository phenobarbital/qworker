"""Unit tests for ResourceMonitor — TASK-037."""
import pytest

from qw.backends.monitor import ResourceMonitor


class TestResourceMonitor:
    """Tests for ResourceMonitor hysteresis logic."""

    @pytest.fixture
    def monitor(self):
        """ResourceMonitor with overridden memory at 50%."""
        m = ResourceMonitor(high_threshold=90.0, low_threshold=75.0)
        m._override_memory = 50.0
        return m

    def test_no_overflow_at_low_memory(self, monitor):
        """should_overflow() returns False when memory is well below high threshold."""
        monitor._override_memory = 50.0
        assert monitor.should_overflow() is False

    def test_overflow_at_high_memory(self, monitor):
        """should_overflow() returns True when memory >= high threshold."""
        monitor._override_memory = 95.0
        assert monitor.should_overflow() is True

    def test_overflow_at_exact_threshold(self, monitor):
        """should_overflow() returns True at exactly the high threshold."""
        monitor._override_memory = 90.0
        assert monitor.should_overflow() is True

    def test_no_overflow_just_below_threshold(self, monitor):
        """should_overflow() returns False just below the threshold."""
        monitor._override_memory = 89.9
        assert monitor.should_overflow() is False

    def test_stays_overflowing_between_thresholds(self, monitor):
        """Once overflowing, stays overflowing even if RAM drops between thresholds."""
        monitor._override_memory = 95.0
        monitor.should_overflow()  # triggers overflow
        monitor._override_memory = 80.0  # between 75-90
        assert monitor.should_overflow() is True  # still overflowing
        assert monitor.should_recover() is False

    def test_recovers_below_low_threshold(self, monitor):
        """should_recover() returns True when RAM drops below low threshold."""
        monitor._override_memory = 95.0
        monitor.should_overflow()  # triggers overflow
        monitor._override_memory = 70.0
        assert monitor.should_recover() is True
        assert monitor.should_overflow() is False

    def test_recover_at_exact_low_threshold(self, monitor):
        """should_recover() returns True at exactly the low threshold."""
        monitor._override_memory = 95.0
        monitor.should_overflow()
        monitor._override_memory = 75.0
        assert monitor.should_recover() is True

    def test_no_flap(self, monitor):
        """Between thresholds, should_recover() returns False and state stays."""
        monitor._override_memory = 95.0
        monitor.should_overflow()
        monitor._override_memory = 80.0
        monitor.should_recover()  # False — still above 75
        assert monitor._overflowing is True

    def test_memory_percent(self, monitor):
        """get_memory_percent() returns the override value."""
        monitor._override_memory = 42.5
        assert monitor.get_memory_percent() == 42.5

    def test_is_overflowing_property(self, monitor):
        """is_overflowing property mirrors internal state."""
        assert monitor.is_overflowing is False
        monitor._override_memory = 95.0
        monitor.should_overflow()
        assert monitor.is_overflowing is True

    def test_reset_clears_overflow(self, monitor):
        """reset() force-clears overflow state."""
        monitor._override_memory = 95.0
        monitor.should_overflow()
        assert monitor.is_overflowing is True
        monitor.reset()
        assert monitor.is_overflowing is False

    def test_no_overflow_without_psutil_and_no_override(self):
        """should_overflow() returns False when psutil is unavailable."""
        import qw.backends.monitor as monitor_module
        original = monitor_module.HAS_PSUTIL
        try:
            monitor_module.HAS_PSUTIL = False
            m = ResourceMonitor(high_threshold=50.0, low_threshold=25.0)
            # No _override_memory set — psutil path
            assert m.should_overflow() is False
        finally:
            monitor_module.HAS_PSUTIL = original

    def test_memory_zero_without_psutil(self):
        """get_memory_percent() returns 0.0 when psutil is unavailable."""
        import qw.backends.monitor as monitor_module
        original = monitor_module.HAS_PSUTIL
        try:
            monitor_module.HAS_PSUTIL = False
            m = ResourceMonitor()
            # No override — psutil unavailable
            assert m.get_memory_percent() == 0.0
        finally:
            monitor_module.HAS_PSUTIL = original

    def test_recover_returns_false_when_not_overflowing(self, monitor):
        """should_recover() returns False when not in overflow state."""
        assert monitor._overflowing is False
        assert monitor.should_recover() is False

    def test_second_overflow_trigger_no_duplicate_log(self, monitor):
        """Calling should_overflow() multiple times while overflowing is idempotent."""
        monitor._override_memory = 95.0
        assert monitor.should_overflow() is True
        assert monitor.should_overflow() is True
        assert monitor._overflowing is True

    def test_full_cycle(self, monitor):
        """Full overflow → recovery cycle works correctly."""
        # Normal state
        monitor._override_memory = 50.0
        assert monitor.should_overflow() is False
        assert monitor.should_recover() is False

        # Spike above threshold
        monitor._override_memory = 92.0
        assert monitor.should_overflow() is True

        # Between thresholds — no recovery
        monitor._override_memory = 82.0
        assert monitor.should_overflow() is True
        assert monitor.should_recover() is False

        # Drop below low threshold
        monitor._override_memory = 60.0
        assert monitor.should_recover() is True
        assert monitor.should_overflow() is False

        # Stays recovered
        assert monitor.should_recover() is False
