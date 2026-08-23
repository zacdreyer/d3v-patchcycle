"""Tests for maintenance-window math (state-machine.md §5)."""

from __future__ import annotations

from datetime import datetime

from patchcycle.window import Window


def at(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 8, 23, hh, mm)  # a Sunday


class TestWindowContainment:
    def test_no_window_always_allows(self):
        w = Window(None, None)
        assert w.allows_disruptive(at(15, 0), estimate_s=3600)

    def test_simple_window(self):
        w = Window(2 * 60, 5 * 60)  # 02:00-05:00
        assert w.allows_disruptive(at(2, 30), estimate_s=60)
        assert not w.allows_disruptive(at(1, 59), estimate_s=60)
        assert not w.allows_disruptive(at(5, 1), estimate_s=60)

    def test_overnight_window(self):
        w = Window(23 * 60, 4 * 60)  # 23:00-04:00
        assert w.allows_disruptive(at(23, 30), estimate_s=60)
        assert w.allows_disruptive(at(2, 0), estimate_s=60)
        assert not w.allows_disruptive(at(12, 0), estimate_s=60)

    def test_estimate_must_fit_remaining_window(self):
        w = Window(2 * 60, 5 * 60)
        # At 04:30 only 30 minutes remain; a 45-minute upgrade must not start.
        assert not w.allows_disruptive(at(4, 30), estimate_s=45 * 60)
        assert w.allows_disruptive(at(4, 30), estimate_s=25 * 60)

    def test_overnight_estimate_crossing_midnight(self):
        w = Window(23 * 60, 4 * 60)
        # At 03:30, 30 min remain; 45 min job must not start.
        assert not w.allows_disruptive(at(3, 30), estimate_s=45 * 60)

    def test_estimate_longer_than_window_never_starts(self):
        w = Window(2 * 60, 3 * 60)
        assert not w.allows_disruptive(at(2, 0), estimate_s=3601)

    def test_window_end_exact_boundary(self):
        w = Window(2 * 60, 5 * 60)
        # Exactly fits: allowed (finishes at the boundary).
        assert w.allows_disruptive(at(4, 0), estimate_s=3600)
