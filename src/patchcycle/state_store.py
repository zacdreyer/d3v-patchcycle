"""Atomic, schema-versioned cycle-state persistence (ADR-0006).

Write protocol: temp file in the same directory (mode 0600, O_NOFOLLOW) ->
fsync -> os.replace -> fsync directory. Corrupt or tampered state is
quarantined and refused (failure-recovery FR-S8), never silently healed.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from patchcycle import STATE_SCHEMA_VERSION
from patchcycle.errors import StateError
from patchcycle.models import ErrorInfo, HealthResult, Outcome, UpdateInfo
from patchcycle.secure_io import check_directory, check_file, read_private
from patchcycle.states import State


@dataclass(frozen=True)
class CycleState:
    """Persisted maintenance-cycle state (state-machine.md §6, schema v2)."""

    run_id: str
    state: State
    hostname: str
    schema_version: int = STATE_SCHEMA_VERSION
    outcome: str | None = None
    started_at: str = ""
    updated_at: str = ""
    os: dict[str, str] = field(default_factory=dict)
    provider: str = ""
    updates_available: tuple[dict[str, Any], ...] = ()
    packages_pending: int = 0
    packages_updated: int = 0
    updates_applied: bool = False
    kernel_update: bool = False
    expected_kernel: str | None = None
    reboot_required: bool = False
    reboot_reasons: tuple[str, ...] = ()
    pre_existing_reboot: bool = False
    boot_id_before: str | None = None
    boot_id_after: str | None = None
    kernel_before: str = ""
    kernel_after: str | None = None
    reboot_initiated_at: str | None = None
    reboot_attempts: int = 0
    reboot_before_updates: bool = False
    initial_reboot_completed: bool = False
    verify_passed: bool | None = None
    outstanding_updates: int | None = None
    health_results: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    notification_status: dict[str, str] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    transitions: tuple[dict[str, str], ...] = ()
    dry_run: bool = False

    def record_transition(self, src: State, dst: State, *, at: str) -> CycleState:
        entry = {"from": src.value, "to": dst.value, "at": at}
        return replace(self, transitions=(*self.transitions, entry))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "state": self.state.value,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "host": {"hostname": self.hostname},
            "os": self.os,
            "provider": self.provider,
            "updates_available": list(self.updates_available),
            "packages_pending": self.packages_pending,
            "packages_updated": self.packages_updated,
            "updates_applied": self.updates_applied,
            "kernel_update": self.kernel_update,
            "expected_kernel": self.expected_kernel,
            "reboot_required": self.reboot_required,
            "reboot_reasons": list(self.reboot_reasons),
            "pre_existing_reboot": self.pre_existing_reboot,
            "boot_id_before": self.boot_id_before,
            "boot_id_after": self.boot_id_after,
            "kernel_before": self.kernel_before,
            "kernel_after": self.kernel_after,
            "reboot_initiated_at": self.reboot_initiated_at,
            "reboot_attempts": self.reboot_attempts,
            "reboot_before_updates": self.reboot_before_updates,
            "initial_reboot_completed": self.initial_reboot_completed,
            "verify_passed": self.verify_passed,
            "outstanding_updates": self.outstanding_updates,
            "health_results": list(self.health_results),
            "warnings": list(self.warnings),
            "notification_status": self.notification_status,
            "error": self.error,
            "transitions": list(self.transitions),
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CycleState:
        if not isinstance(data, dict):
            raise StateError("state file does not contain an object")
        version = data.get("schema_version")
        if type(version) is not int or version < 1:
            raise StateError("state file has no integer schema_version")
        if version > STATE_SCHEMA_VERSION:
            raise StateError(
                f"state schema version {version} is newer than supported "
                f"{STATE_SCHEMA_VERSION}; refusing to mutate (upgrade PatchCycle)"
            )
        _validate_schema(data)
        if version == 1:
            data = _migrate_v1(data)
            version = 2
        try:
            state = State(data["state"])
        except (KeyError, ValueError) as exc:
            raise StateError(f"state file has invalid state: {data.get('state')!r}") from exc
        return cls(
            run_id=str(data.get("run_id", "")),
            state=state,
            hostname=str(data.get("host", {}).get("hostname", "")),
            schema_version=version,
            outcome=data.get("outcome"),
            started_at=str(data.get("started_at", "")),
            updated_at=str(data.get("updated_at", "")),
            os=dict(data.get("os", {})),
            provider=str(data.get("provider", "")),
            updates_available=tuple(data.get("updates_available", [])),
            packages_pending=int(data.get("packages_pending", 0)),
            packages_updated=int(data.get("packages_updated", 0)),
            updates_applied=bool(data.get("updates_applied", False)),
            kernel_update=bool(data.get("kernel_update", False)),
            expected_kernel=data.get("expected_kernel"),
            reboot_required=bool(data.get("reboot_required", False)),
            reboot_reasons=tuple(data.get("reboot_reasons", [])),
            pre_existing_reboot=bool(data.get("pre_existing_reboot", False)),
            boot_id_before=data.get("boot_id_before"),
            boot_id_after=data.get("boot_id_after"),
            kernel_before=str(data.get("kernel_before", "")),
            kernel_after=data.get("kernel_after"),
            reboot_initiated_at=data.get("reboot_initiated_at"),
            reboot_attempts=int(data.get("reboot_attempts", 0)),
            reboot_before_updates=data.get("reboot_before_updates", False),
            initial_reboot_completed=data.get("initial_reboot_completed", False),
            verify_passed=data.get("verify_passed"),
            outstanding_updates=data.get("outstanding_updates"),
            health_results=tuple(data.get("health_results", [])),
            warnings=tuple(data.get("warnings", [])),
            notification_status=dict(data.get("notification_status", {})),
            error=data.get("error"),
            transitions=tuple(data.get("transitions", [])),
            dry_run=bool(data.get("dry_run", False)),
        )


class StateStore:
    """Reads/writes the single active cycle state and the history archive."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self.history_dir = state_dir / "history"

    # -- writing ---------------------------------------------------------

    def _write_temp(self, path: Path, data: str) -> None:
        path.write_text(data, encoding="utf-8")

    def _atomic_write(self, target: Path, data: str) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            check_directory(self.state_dir)
            check_directory(target.parent)
            if target.exists() or target.is_symlink():
                check_file(target)
        except OSError as exc:
            raise StateError(str(exc)) from exc
        if os.name == "posix":
            self._check_not_symlink(target, for_write=True)
        # Unique exclusive temp creation prevents truncating a planted sibling.
        import uuid

        tmp = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                self._write_temp(Path(os.devnull), "")  # hook point for tests
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            _fsync_dir(target.parent)
        finally:
            tmp.unlink(missing_ok=True)

    def save(self, cycle: CycleState) -> None:
        _validate_run_id(cycle.run_id)
        payload = json.dumps(cycle.to_dict(), indent=2, sort_keys=True)
        self._atomic_write(self.state_file, payload)

    def archive(self, cycle: CycleState) -> Path:
        """Move a terminal cycle into history and reset state to IDLE."""
        _validate_run_id(cycle.run_id)
        self.history_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self.history_dir / f"{cycle.run_id}.json"
        self._atomic_write(target, json.dumps(cycle.to_dict(), indent=2, sort_keys=True))
        self.state_file.unlink(missing_ok=True)
        _fsync_dir(self.state_dir)
        return target

    def save_report(self, run_id: str, body: str) -> None:
        """Persist the operator report independently of network delivery."""
        _validate_run_id(run_id)
        self.history_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._atomic_write(self.history_dir / f"{run_id}.report.txt", body)

    # -- reading ---------------------------------------------------------

    def load(self) -> CycleState | None:
        """Load the active cycle, or None if the machine is IDLE.

        Raises:
            StateError: state exists but is corrupt/tampered (quarantined).
        """
        if self.state_dir.exists() or self.state_dir.is_symlink():
            try:
                check_directory(self.state_dir)
            except OSError as exc:
                raise StateError(str(exc)) from exc
        if any(self.state_dir.glob("state.json.corrupt.*")):
            raise StateError("quarantined state requires deliberate operator recovery (FR-S8)")
        if os.name == "posix":
            self._check_not_symlink(self.state_file, for_write=False)
        try:
            raw = read_private(self.state_file)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StateError(f"cannot read state file: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
            return CycleState.from_dict(data)
        except (StateError, ValueError, TypeError, AttributeError, UnicodeDecodeError) as exc:
            if isinstance(exc, StateError) and "newer than supported" in str(exc):
                raise  # version refusal is not corruption; never quarantine
            self._quarantine(raw)
            raise StateError(
                f"state file failed integrity checks and was quarantined: {exc}. "
                "Manual intervention required (failure-recovery FR-S8)."
            ) from exc

    def history(self) -> list[CycleState]:
        """Archived cycles, oldest first; corrupt entries are skipped."""
        if not self.history_dir.is_dir():
            return []
        cycles: list[CycleState] = []
        for path in sorted(self.history_dir.glob("*.json")):
            try:
                cycles.append(CycleState.from_dict(json.loads(read_private(path))))
            except (ValueError, StateError, OSError):
                continue
        return sorted(cycles, key=lambda cycle: (cycle.started_at, cycle.run_id))

    # -- internals -------------------------------------------------------

    def _quarantine(self, raw: bytes) -> None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        quarantine = self.state_dir / f"state.json.corrupt.{stamp}"
        fd = os.open(quarantine, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_dir(self.state_dir)
        self.state_file.unlink(missing_ok=True)
        _fsync_dir(self.state_dir)

    @staticmethod
    def _check_not_symlink(path: Path, *, for_write: bool) -> None:
        if path.is_symlink():
            raise StateError(
                f"state path {path} is a symlink; refusing to "
                f"{'overwrite' if for_write else 'read'} (threat-model T6)"
            )


_O_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0


def _migrate_v1(data: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(data)
    migrated["schema_version"] = 2
    migrated.setdefault("boot_id_after", None)
    migrated.setdefault("reboot_before_updates", False)
    migrated.setdefault("initial_reboot_completed", False)
    return migrated


def _validate_run_id(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise StateError("invalid run_id: expected a safe archive identifier")


def _validate_schema(data: dict[str, Any]) -> None:
    _validate_run_id(data.get("run_id"))
    for key in (
        "updates_applied",
        "kernel_update",
        "reboot_required",
        "pre_existing_reboot",
        "reboot_before_updates",
        "initial_reboot_completed",
        "dry_run",
    ):
        if key in data and type(data[key]) is not bool:
            raise StateError(f"{key}: expected boolean")
    _validate_counters(data)
    for key in ("host", "os", "notification_status"):
        value = data.get(key, {})
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise StateError(f"{key}: expected object with string values")
    if data.get("error") is not None and not isinstance(data["error"], dict):
        raise StateError("error: expected object or null")
    for key in ("updates_available", "health_results", "transitions"):
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise StateError(f"{key}: expected array of objects")
    _validate_recovery_values(data)


def _validate_counters(data: dict[str, Any]) -> None:
    for key in ("packages_pending", "packages_updated", "reboot_attempts", "outstanding_updates"):
        if key == "outstanding_updates" and data.get(key) is None:
            continue
        if key in data and (type(data[key]) is not int or data[key] < 0):
            raise StateError(f"{key}: expected non-negative integer")


def _validate_recovery_values(data: dict[str, Any]) -> None:
    if data.get("outcome") is not None:
        try:
            Outcome(data["outcome"])
        except (ValueError, TypeError) as exc:
            raise StateError("outcome: invalid cycle outcome") from exc
    for key in ("state", "started_at", "updated_at", "provider", "kernel_before"):
        if key in data and not isinstance(data[key], str):
            raise StateError(f"{key}: expected string")
    for key in (
        "outcome",
        "expected_kernel",
        "boot_id_before",
        "boot_id_after",
        "kernel_after",
        "reboot_initiated_at",
    ):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise StateError(f"{key}: expected string or null")
    if data.get("verify_passed") is not None and type(data["verify_passed"]) is not bool:
        raise StateError("verify_passed: expected boolean or null")
    for key in ("reboot_reasons", "warnings"):
        values = data.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise StateError(f"{key}: expected string array")
    _validate_nested_records(data)


def _validate_nested_records(data: dict[str, Any]) -> None:
    try:
        for item in data.get("updates_available", []):
            UpdateInfo(**item)
        for item in data.get("health_results", []):
            HealthResult(**item)
        if data.get("error") is not None:
            ErrorInfo(**data["error"])
    except TypeError as exc:
        raise StateError("incomplete or unknown nested state fields") from exc
    for item in data.get("updates_available", []):
        _validate_record(
            item,
            ("name", "version_from", "version_to", "arch"),
            ("held", "security", "requires_reboot_hint"),
        )
    for item in data.get("health_results", []):
        _validate_record(item, ("kind", "name", "detail"), ("ok", "critical"))
        _validate_duration(item.get("duration_s", 0))
    for item in data.get("transitions", []):
        _validate_record(item, ("from", "to", "at"), ())
    if data.get("error"):
        _validate_record(data["error"], ("kind", "message", "stage"), ("manual_intervention",))


def _validate_duration(duration: Any) -> None:
    try:
        valid = type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise StateError("health duration must be finite and non-negative")


def _validate_record(
    record: dict[str, Any], strings: tuple[str, ...], bools: tuple[str, ...]
) -> None:
    for key in strings:
        if key in record and not isinstance(record[key], str):
            raise StateError(f"{key}: expected string")
    for key in bools:
        if key in record and type(record[key]) is not bool:
            raise StateError(f"{key}: expected boolean")


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
