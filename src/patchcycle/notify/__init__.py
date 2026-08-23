"""Notifier registry (ADR-0007). V1 ships SMTP and generic webhook (Phase 5)."""

from patchcycle.notify.base import Notifier

__all__ = ["Notifier"]
