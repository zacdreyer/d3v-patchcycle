"""Notifier contract (ADR-0007).

Delivery failure must never change the maintenance outcome; deliver() returns
a status string recorded in state/history instead of raising for expected
delivery problems.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from patchcycle.models import ReportData


class Notifier(ABC):
    name: str = "abstract"

    @abstractmethod
    def deliver(self, report: ReportData, body: str) -> str:
        """Attempt delivery; return "sent" or "failed:<reason>"."""
