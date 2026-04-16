"""Queue-size policy for dynamic growth/shrink decisions.

Pure synchronous logic; no asyncio and no I/O. The owning QueueManager
is responsible for mutating the underlying asyncio.Queue._maxsize and
for logging around the decisions returned by this policy.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import time


@dataclass
class QueueSizePolicy:
    """Policy that governs dynamic growth and shrink of a queue's max size.

    Attributes:
        base_size: The configured baseline queue capacity.
        grow_margin: How many extra slots may be added above base_size.
        warn_ratio: Fraction of current_maxsize that triggers a warning.
        shrink_cooldown: Seconds of low usage required before shrinking.
        low_watermark_ratio: Fraction below which queue is considered idle.
        current_maxsize: Current effective maximum size (starts at base_size).
        grow_events: Total number of grow operations performed.
        discard_events: Total number of discarded (rejected) tasks.
        _last_high_usage_at: Monotonic timestamp of last high-usage observation.
    """

    base_size: int
    grow_margin: int = 2
    warn_ratio: float = 0.80
    shrink_cooldown: float = 30.0
    low_watermark_ratio: float = 0.50
    current_maxsize: int = 0
    grow_events: int = 0
    discard_events: int = 0
    _last_high_usage_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        """Validate configuration and set current_maxsize to base_size."""
        if self.base_size < 1:
            raise ValueError("base_size must be >= 1")
        if self.grow_margin < 0:
            raise ValueError("grow_margin must be >= 0")
        if not (0.0 < self.warn_ratio <= 1.0):
            raise ValueError("warn_ratio must be in (0, 1]")
        if not (0.0 < self.low_watermark_ratio < 1.0):
            raise ValueError("low_watermark_ratio must be in (0, 1)")
        self.current_maxsize = self.base_size

    @classmethod
    def from_config(cls, base_size: int) -> "QueueSizePolicy":
        """Construct a policy using defaults from qw.conf.

        Args:
            base_size: The baseline queue capacity.

        Returns:
            A QueueSizePolicy configured from environment/config defaults.
        """
        from qw.conf import (
            WORKER_QUEUE_GROW_MARGIN,
            WORKER_QUEUE_WARN_THRESHOLD,
            WORKER_QUEUE_SHRINK_COOLDOWN,
        )
        return cls(
            base_size=base_size,
            grow_margin=WORKER_QUEUE_GROW_MARGIN,
            warn_ratio=WORKER_QUEUE_WARN_THRESHOLD,
            shrink_cooldown=float(WORKER_QUEUE_SHRINK_COOLDOWN),
        )

    def ceiling(self) -> int:
        """Return the maximum allowed queue size (base + margin).

        Returns:
            int: base_size + grow_margin.
        """
        return self.base_size + self.grow_margin

    def should_warn(self, current_size: int) -> bool:
        """Return True when the queue is near its limit.

        Args:
            current_size: The current number of items in the queue.

        Returns:
            True if current_size / current_maxsize >= warn_ratio and
            current_maxsize > 0.
        """
        if self.current_maxsize <= 0:
            return False
        return (current_size / self.current_maxsize) >= self.warn_ratio

    def can_grow(self) -> bool:
        """Return True if the queue may grow by one slot.

        Returns:
            True iff current_maxsize < ceiling().
        """
        return self.current_maxsize < self.ceiling()

    def can_shrink(self, current_size: int, now: float) -> bool:
        """Return True if conditions allow shrinking by one slot.

        Args:
            current_size: The current number of items in the queue.
            now: Current monotonic time (time.monotonic()).

        Returns:
            True iff all three conditions hold:
              - current_maxsize > base_size
              - current_size / current_maxsize <= low_watermark_ratio
              - now - _last_high_usage_at >= shrink_cooldown
        """
        if self.current_maxsize <= self.base_size:
            return False
        if self.current_maxsize <= 0:
            return False
        usage = current_size / self.current_maxsize
        if usage > self.low_watermark_ratio:
            return False
        elapsed = now - self._last_high_usage_at
        return elapsed >= self.shrink_cooldown

    def register_grow(self) -> None:
        """Increment current_maxsize by one and record the event.

        Raises:
            RuntimeError: If called when current_maxsize == ceiling().
        """
        if self.current_maxsize >= self.ceiling():
            raise RuntimeError("grow margin exhausted")
        self.current_maxsize += 1
        self.grow_events += 1

    def register_shrink(self) -> None:
        """Decrement current_maxsize by one, flooring at base_size."""
        if self.current_maxsize > self.base_size:
            self.current_maxsize -= 1

    def register_discard(self) -> None:
        """Record that a task was discarded due to queue at ceiling."""
        self.discard_events += 1

    def note_high_usage(self, now: float) -> None:
        """Update the high-usage timestamp to reset the shrink cooldown.

        Args:
            now: Current monotonic time (time.monotonic()).
        """
        self._last_high_usage_at = now
