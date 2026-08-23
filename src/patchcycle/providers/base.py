"""UpdateProvider contract (docs/specifications/provider-contract.md §1)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from patchcycle.models import (
    ApplyResult,
    OsIdentity,
    PreflightResult,
    RebootStatus,
    RefreshResult,
    UpdateInfo,
    VerifyResult,
)


class UpdateProvider(ABC):
    """Isolates all OS/package-manager-specific behaviour.

    Expected operational failures are encoded in result objects; exceptions
    indicate programmer error or unexpected I/O failure.
    """

    name: str = "abstract"

    def __init__(self, os_identity: OsIdentity) -> None:
        self.os_identity = os_identity

    @abstractmethod
    def preflight(self) -> PreflightResult:
        """Verify PM health, lock availability, interrupted transactions."""

    @abstractmethod
    def refresh(self) -> RefreshResult:
        """Update package indexes only. Never installs/removes."""

    @abstractmethod
    def list_updates(self, strategy: str) -> list[UpdateInfo]:
        """Pure discovery; MUST NOT alter installed state."""

    @abstractmethod
    def apply_updates(self, strategy: str, updates: list[UpdateInfo]) -> ApplyResult:
        """Apply updates noninteractively per strategy; no force flags."""

    @abstractmethod
    def reboot_required(self) -> RebootStatus:
        """Probe the native reboot-required mechanism (conservative on error)."""

    @abstractmethod
    def verify(self) -> VerifyResult:
        """PM consistency check + outstanding applicable update count."""
