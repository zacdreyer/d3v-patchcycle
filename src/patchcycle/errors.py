"""PatchCycle exception hierarchy.

Expected operational failures carry the process exit code defined in the
product specification (§8) so the CLI can translate them uniformly.
"""

from __future__ import annotations


class PatchCycleError(Exception):
    """Base class for all expected PatchCycle failures."""

    exit_code = 1
    error_kind = "internal"
    manual_intervention = True


class ConfigError(PatchCycleError):
    """Configuration is invalid (unknown keys, bad types, bad values)."""

    exit_code = 4
    error_kind = "config-invalid"
    manual_intervention = True


class UnsupportedPlatformError(PatchCycleError):
    """The host OS has no registered, tested provider."""

    exit_code = 5
    error_kind = "unsupported-platform"
    manual_intervention = False


class LockHeldError(PatchCycleError):
    """Another PatchCycle instance holds the execution lock."""

    exit_code = 3
    error_kind = "lock-held"
    manual_intervention = False


class PreflightError(PatchCycleError):
    """A pre-flight check failed; no update action was taken."""

    exit_code = 2
    error_kind = "preflight-failed"
    manual_intervention = True

    def __init__(self, message: str = "", *, kind: str | None = None) -> None:
        super().__init__(message)
        if kind:
            self.error_kind = kind


class PackageManagerLockedError(PreflightError):
    """The package manager remained locked past lock_timeout."""

    error_kind = "pm-locked"


class PackageManagerError(PatchCycleError):
    """The package manager returned a failure or unusable output."""

    exit_code = 1
    error_kind = "pm-failed"
    manual_intervention = True


class StateError(PatchCycleError):
    """Persisted state is corrupt, tampered with, or from a newer schema."""

    exit_code = 1
    error_kind = "state-corrupt"
    manual_intervention = True


class RebootError(PatchCycleError):
    """The reboot could not be initiated or verified."""

    exit_code = 1
    error_kind = "reboot-failed"
    manual_intervention = True


class VerificationError(PatchCycleError):
    """Post-maintenance verification failed."""

    exit_code = 1
    error_kind = "verify-failed"
    manual_intervention = True
