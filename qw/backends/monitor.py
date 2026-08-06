"""Resource monitor for QWorker container execution backends.

Monitors local RAM usage via psutil and provides overflow/recovery decisions
with hysteresis logic. Used by the BackendDispatcher to decide when to route
tasks to container backends instead of local execution.

The psutil package is an optional dependency:
    uv pip install qworker[containers]
"""
import logging
from typing import Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


class ResourceMonitor:
    """Monitors RAM usage and provides hysteresis-based overflow decisions.

    Uses a high-water/low-water threshold model:
    - Once RAM exceeds high_threshold, overflow mode activates.
    - Overflow mode stays active until RAM drops below low_threshold.
    - Between thresholds, state doesn't change (prevents flapping).

    Args:
        high_threshold: RAM percentage that triggers overflow (default: 90.0).
        low_threshold: RAM percentage that clears overflow (default: 75.0).

    Example:
        monitor = ResourceMonitor(high_threshold=90.0, low_threshold=75.0)
        if monitor.should_overflow():
            # route task to container backend
            ...
        if monitor.should_recover():
            # return to local execution
            ...
    """

    def __init__(
        self,
        high_threshold: float = 90.0,
        low_threshold: float = 75.0,
    ) -> None:
        self.logger = logging.getLogger("QW.Backend.Monitor")
        self._high = high_threshold
        self._low = low_threshold
        self._overflowing: bool = False
        # Used for testing — bypasses psutil entirely
        self._override_memory: Optional[float] = None

    def get_memory_percent(self) -> float:
        """Return current RAM usage as a percentage (0.0–100.0).

        If _override_memory is set, returns that value (for testing).
        If psutil is not installed, returns 0.0.

        Returns:
            Current memory usage percentage.
        """
        if self._override_memory is not None:
            return self._override_memory
        if not HAS_PSUTIL:
            return 0.0  # Can't measure — assume no pressure
        return psutil.virtual_memory().percent

    def should_overflow(self) -> bool:
        """Determine whether tasks should overflow to container backends.

        Returns True when:
        - RAM >= high_threshold AND not yet overflowing → activates overflow
        - Already overflowing (regardless of current RAM, until should_recover())

        Returns False when:
        - psutil not installed and no _override_memory set
        - RAM is below high_threshold and not currently overflowing

        Returns:
            True if tasks should be routed to container backends.
        """
        if not HAS_PSUTIL and self._override_memory is None:
            return False
        mem = self.get_memory_percent()
        if not self._overflowing and mem >= self._high:
            self._overflowing = True
            self.logger.warning(
                "RAM at %.1f%% (>= %.1f%%) — overflow activated",
                mem,
                self._high,
            )
        return self._overflowing

    def should_recover(self) -> bool:
        """Determine whether overflow state should be cleared.

        Returns True (and clears overflow state) when:
        - Currently overflowing AND RAM <= low_threshold

        Returns False when:
        - Not currently overflowing
        - Currently overflowing but RAM is still above low_threshold

        Returns:
            True if overflow has just been cleared (transition only).
        """
        if self._overflowing:
            mem = self.get_memory_percent()
            if mem <= self._low:
                self._overflowing = False
                self.logger.info(
                    "RAM at %.1f%% (<= %.1f%%) — overflow deactivated",
                    mem,
                    self._low,
                )
                return True
        return False

    @property
    def is_overflowing(self) -> bool:
        """Return whether overflow mode is currently active.

        Returns:
            True if currently in overflow state.
        """
        return self._overflowing

    def reset(self) -> None:
        """Force-reset overflow state.

        Useful for testing or manual operator intervention.
        """
        self._overflowing = False
        self.logger.debug("ResourceMonitor state reset")
