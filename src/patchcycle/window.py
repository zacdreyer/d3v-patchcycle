"""Maintenance-window math (state-machine.md §5).

Windows gate the *start* of disruptive actions only; a running package
transaction is never interrupted for the clock, and verification/health/
notification are not window-restricted.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast


class Window:
    """A daily local-time window; start > end means overnight."""

    def __init__(self, start_min: int | None, end_min: int | None) -> None:
        self.start_min = start_min
        self.end_min = end_min

    @property
    def configured(self) -> bool:
        return self.start_min is not None and self.end_min is not None

    def allows_disruptive(self, now: datetime, estimate_s: int) -> bool:
        """True when a disruptive action estimated at estimate_s may start.

        The action must both start inside the window and be expected to
        finish before the window ends.
        """
        if not self.configured:
            return True
        start_min = cast(int, self.start_min)
        end_min = cast(int, self.end_min)
        minute = now.hour * 60 + now.minute + now.second / 60
        remaining = self._remaining_minutes(minute, start_min, end_min)
        if remaining is None:
            return False
        return estimate_s <= remaining * 60

    def _remaining_minutes(self, minute: float, start: int, end: int) -> float | None:
        """Minutes until window end, or None if outside the window."""
        if start <= end:  # same-day window, e.g. 02:00-05:00
            if start <= minute <= end:
                return end - minute
            return None
        # Overnight window, e.g. 23:00-04:00
        if minute >= start:
            return (24 * 60 - minute) + end
        if minute <= end:
            return end - minute
        return None
