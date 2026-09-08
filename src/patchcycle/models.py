"""Core data model shared across the engine, providers, and notifiers."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Strategy(enum.StrEnum):
    """Update strategy (docs/specifications/configuration.md §[updates])."""

    SECURITY = "security"
    SAFE = "safe"
    FULL = "full"


class Outcome(enum.StrEnum):
    """Final cycle outcomes (architecture §11)."""

    SUCCESS = "success"
    SUCCESS_WITH_WARNINGS = "success_with_warnings"
    NO_UPDATES = "no_updates"
    FAILED = "failed"
    BLOCKED = "blocked"
    MANUAL_REBOOT_REQUIRED = "manual_reboot_required"


@dataclass(frozen=True)
class OsIdentity:
    """Detected operating-system identity (docs/specifications §FR-01)."""

    family: str  # "linux" | "macos" | "unsupported:<system>"
    os_id: str  # os-release ID (e.g. "ubuntu") or platform name
    id_like: tuple[str, ...] = ()
    version_id: str = ""
    codename: str = ""
    pretty_name: str = ""
    arch: str = ""
    kernel: str = ""
    init: str = ""  # "systemd" | "launchd" | "openrc" | ""


@dataclass(frozen=True)
class UpdateInfo:
    """One applicable package update."""

    name: str
    version_from: str
    version_to: str
    security: bool = False
    held: bool = False
    requires_reboot_hint: bool = False
    arch: str = ""


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    kind: str = ""  # e.g. "interrupted-transaction", "pm-locked"
    detail: str = ""
    pre_existing_reboot: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefreshResult:
    ok: bool
    warnings: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    packages_updated: int = 0
    upgraded: tuple[UpdateInfo, ...] = ()
    kernel_update: bool = False
    expected_kernel: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class RebootStatus:
    required: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifyResult:
    consistent: bool
    outstanding: int = 0
    detail: str = ""


@dataclass(frozen=True)
class HealthResult:
    """Result of one health check (configuration.md §health)."""

    kind: str  # service | http | tcp | command | failed-units
    name: str
    ok: bool
    critical: bool
    detail: str = ""
    duration_s: float = 0.0


@dataclass(frozen=True)
class HookResult:
    hook: str
    argv: tuple[str, ...]
    exit_code: int
    ok: bool
    timed_out: bool = False
    duration_s: float = 0.0
    stderr_tail: str = ""


@dataclass(frozen=True)
class ErrorInfo:
    """Structured error carried in state/history (state-machine.md §6)."""

    kind: str
    message: str
    stage: str
    manual_intervention: bool = True


@dataclass(frozen=True)
class ReportData:
    """Everything the final report needs (product-spec §23)."""

    run_id: str
    hostname: str
    os_pretty_name: str
    outcome: Outcome
    packages_available: int = 0
    packages_upgraded: int = 0
    packages_held: int = 0
    kernel_update: bool = False
    reboot_required: bool = False
    reboot_completed: bool = False
    kernel_before: str = ""
    kernel_after: str = ""
    outstanding_updates: int | None = None
    health_results: tuple[HealthResult, ...] = ()
    failed_services: int = 0
    error: ErrorInfo | None = None
    notification_status: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
