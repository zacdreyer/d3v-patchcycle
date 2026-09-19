"""Installer: systemd units, directories, and configuration (FR-19..FR-21).

Spec: operations.md §1, architecture §5, ADR-0003 (timers over cron).
Idempotent by construction: units are rendered deterministically from config
and only rewritten when content changes; existing administrator config is
never overwritten (a ``config.toml.new`` reference is written instead).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from patchcycle.config import DEFAULT_CONFIG_PATH, Config, MaintenanceConfig
from patchcycle.errors import PatchCycleError
from patchcycle.lock import ExecutionLock
from patchcycle.secure_io import check_directory, check_file, read_private
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State

UNIT_TIMER = "d3v-patchcycle.timer"
UNIT_SERVICE = "d3v-patchcycle.service"
UNIT_RESUME = "d3v-patchcycle-resume.service"

_WEEKDAY_MAP = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}

DEFAULT_CONFIG_TEMPLATE = """\
# D3V PatchCycle configuration — see docs/specifications/configuration.md
[maintenance]
enabled = true
schedule = "weekly"
day = "sunday"
time = "02:00"

[updates]
strategy = "safe"

[reboot]
policy = "when_required"
existing_pending = "reboot_first"
allow_if_users_logged_in = false

[notifications.email]
enabled = false
to = []

[logging]
level = "info"
"""


def schedule_to_oncalendar(maintenance: MaintenanceConfig) -> str | None:
    """Translate [maintenance] schedule into an OnCalendar expression.

    Returns None for `manual` (no timer installed). Validated again at
    install time via `systemd-analyze calendar`.
    """
    if maintenance.schedule == "manual":
        return None
    time_part = f"{maintenance.time}:00"
    if maintenance.schedule == "daily":
        return f"*-*-* {time_part}"
    if maintenance.schedule == "weekly":
        return f"{_WEEKDAY_MAP[maintenance.day]} *-*-* {time_part}"
    if maintenance.schedule == "monthly":
        return f"*-*-{int(maintenance.day):02d} {time_part}"
    raise ValueError(f"unknown schedule: {maintenance.schedule}")


def render_timer(config: Config, executable: str) -> str:
    on_calendar = schedule_to_oncalendar(config.maintenance)
    if on_calendar is None:
        raise ValueError("timer is not rendered for manual schedules")
    return f"""\
[Unit]
Description=D3V PatchCycle scheduled maintenance
Documentation=file:///etc/d3v-patchcycle/config.toml

[Timer]
OnCalendar={on_calendar}
Persistent=true
RandomizedDelaySec={config.maintenance.random_delay_s}
Unit={UNIT_SERVICE}

[Install]
WantedBy=timers.target
"""


def _unit_arg(value: str) -> str:
    if any(ord(c) < 32 for c in value):
        raise ValueError("systemd arguments must not contain control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def render_service(config: Config, executable: str, config_path: Path = DEFAULT_CONFIG_PATH) -> str:
    # Hardening per threat-model §5; note the documented trade-offs there.
    return f"""\
[Unit]
Description=D3V PatchCycle maintenance run
Documentation=file:///etc/d3v-patchcycle/config.toml
ConditionPathExists={_unit_arg(config_path.as_posix())}
Wants=network-online.target
After=network-online.target d3v-patchcycle-resume.service

[Service]
Type=oneshot
TimeoutStartSec=infinity
ExecStart={_unit_arg(executable)} run --scheduled --config {_unit_arg(config_path.as_posix())}
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=false
EnvironmentFile=-/etc/d3v-patchcycle/environment
StandardOutput=journal
StandardError=journal
SyslogIdentifier=d3v-patchcycle
"""


def render_resume_service(executable: str, config_path: Path = DEFAULT_CONFIG_PATH) -> str:
    return f"""\
[Unit]
Description=D3V PatchCycle post-boot maintenance resume
Documentation=file:///etc/d3v-patchcycle/config.toml
Wants=network-online.target
After=network-online.target
Before=d3v-patchcycle.service

[Service]
Type=oneshot
TimeoutStartSec=infinity
ExecStart={_unit_arg(executable)} resume --config {_unit_arg(config_path.as_posix())}
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
RestrictSUIDSGID=false
EnvironmentFile=-/etc/d3v-patchcycle/environment
StandardOutput=journal
StandardError=journal
SyslogIdentifier=d3v-patchcycle-resume

[Install]
WantedBy=multi-user.target
"""


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    detail: str = ""
    changed: tuple[str, ...] = ()


SystemdFn = Callable[..., tuple[int, str]]


def _real_systemd(*args: str) -> tuple[int, str]:  # pragma: no cover - Linux-only (L3/L4)
    argv = (
        ["/usr/bin/systemd-analyze", *args[1:]]
        if args[0] == "analyze"
        else ["/usr/bin/systemctl", *args]
    )
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv,
        shell=False,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


class Installer:
    """Installs/validates/removes PatchCycle host integration."""

    def __init__(
        self,
        *,
        config: Config,
        systemd: SystemdFn | None = None,
        install_root: Path = Path("/opt/d3v-patchcycle"),
        config_dir: Path = Path("/etc/d3v-patchcycle"),
        state_dir: Path | None = None,
        unit_dir: Path = Path("/etc/systemd/system"),
        executable: str = "/usr/local/bin/d3v-patchcycle",
    ) -> None:
        self.config = config
        self._systemd: SystemdFn = systemd or _real_systemd
        self.install_root = install_root
        self.config_dir = config_dir
        self.config_path = config_dir / "config.toml"
        self.state_dir = state_dir or Path(config.paths.state_dir)
        self.lock_path = (
            Path(config.paths.lock_file) if state_dir is None else state_dir.with_suffix(".lock")
        )
        self.unit_dir = unit_dir
        self.executable = executable

    # ------------------------------------------------------------- install

    def install(self) -> InstallResult:
        if getattr(os, "geteuid", lambda: 0)() != 0:
            return InstallResult(False, "installation requires root")
        try:
            with ExecutionLock(self.lock_path):
                self._require_idle()
                return self._install()
        except (OSError, ValueError, subprocess.SubprocessError, PatchCycleError) as exc:
            return InstallResult(False, f"systemd installation failed: {exc}")

    def _checked(self, *args: str) -> None:
        code, out = self._systemd(*args)
        if code != 0:
            raise OSError(f"{' '.join(args)} failed: {out}")

    def _install(self) -> InstallResult:
        changed: list[str] = []
        self._checked("daemon-reload")

        on_calendar = schedule_to_oncalendar(self.config.maintenance)
        if on_calendar is not None:
            self._checked("analyze", "calendar", on_calendar)

        self._write_config_if_missing(changed)
        self._write_state_dirs(changed)
        self._write_unit(
            UNIT_SERVICE, render_service(self.config, self.executable, self.config_path), changed
        )
        self._write_unit(
            UNIT_RESUME, render_resume_service(self.executable, self.config_path), changed
        )
        if on_calendar is not None:
            self._write_unit(UNIT_TIMER, render_timer(self.config, self.executable), changed)
        elif (self.unit_dir / UNIT_TIMER).exists():
            self._checked("disable", "--now", UNIT_TIMER)
            (self.unit_dir / UNIT_TIMER).unlink()

        self._checked(
            "analyze",
            "verify",
            str(self.unit_dir / UNIT_SERVICE),
            str(self.unit_dir / UNIT_RESUME),
        )

        commands = [("daemon-reload",), ("enable", UNIT_RESUME)]
        if on_calendar is not None:
            commands.append(("enable", "--now", UNIT_TIMER))
        for command in commands:
            self._checked(*command)
        return InstallResult(True, changed=tuple(changed))

    def resume_unit_enabled(self) -> bool:
        """FR-S15 seam used by the reboot controller in production."""
        try:
            code, out = self._systemd("is-enabled", UNIT_RESUME)
        except (FileNotFoundError, OSError):
            return False
        return code == 0 and out.strip() == "enabled"

    # ----------------------------------------------------------- uninstall

    def uninstall(self, *, purge: bool = False) -> InstallResult:
        if getattr(os, "geteuid", lambda: 0)() != 0:
            return InstallResult(False, "uninstallation requires root")
        try:
            with ExecutionLock(self.lock_path):
                self._require_idle()
                return self._uninstall(purge=purge)
        except (OSError, ValueError, subprocess.SubprocessError, PatchCycleError) as exc:
            return InstallResult(False, f"uninstallation failed: {exc}")

    def _require_idle(self) -> None:
        cycle = StateStore(self.state_dir).load()
        if cycle is not None and cycle.state not in (State.COMPLETED, State.FAILED):
            raise OSError("maintenance cycle pending; preserve recovery services until it finishes")

    def _uninstall(self, *, purge: bool) -> InstallResult:
        changed: list[str] = []
        if purge:
            self._check_purge_target()
        if (self.unit_dir / UNIT_TIMER).exists():
            self._checked("disable", "--now", UNIT_TIMER)
        if (self.unit_dir / UNIT_RESUME).exists():
            self._checked("disable", UNIT_RESUME)
        for unit in (UNIT_TIMER, UNIT_SERVICE, UNIT_RESUME):
            path = self.unit_dir / unit
            if path.exists():
                path.unlink()
                changed.append(str(path))
        self._checked("daemon-reload")
        if purge:
            for path in (self.config_path,):
                if path.exists():
                    path.unlink()
                    changed.append(str(path))
            if self.state_dir.exists():
                import shutil

                shutil.rmtree(self.state_dir)
                changed.append(str(self.state_dir))
        return InstallResult(True, changed=tuple(changed))

    def _check_purge_target(self) -> None:
        protected = {
            Path(p).resolve() for p in ("/", "/etc", "/var", "/var/lib", "/usr", "/opt", "/home")
        }
        if self.state_dir.resolve() in protected:
            raise OSError("refusing to purge a system directory")
        if self.state_dir.exists():
            check_directory(self.state_dir)
            for child in self.state_dir.iterdir():
                if child.name == "state.json":
                    check_file(child)
                elif child.name == "history":
                    self._check_purge_history(child)
                else:
                    raise OSError("refusing to purge a state directory containing unrelated files")

    @staticmethod
    def _check_purge_history(directory: Path) -> None:
        check_directory(directory)
        for path in directory.iterdir():
            check_file(path)
            if path.suffix == ".json":
                cycle = CycleState.from_dict(json.loads(read_private(path)))
                if cycle.run_id != path.stem:
                    raise OSError("refusing to purge mismatched history record")
            elif path.name.endswith(".report.txt"):
                state = directory / (path.name.removesuffix(".report.txt") + ".json")
                if not state.is_file():
                    raise OSError("refusing to purge an unpaired report")
            else:
                raise OSError("refusing to purge unrelated history data")

    # ------------------------------------------------------------- helpers

    def _write_config_if_missing(self, changed: list[str]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        check_directory(self.config_path.parent)
        target = self.config_path
        if target.exists():
            check_file(target)
            reference = target.with_name(f"{target.name}.new")
            if reference.exists() or reference.is_symlink():
                check_file(reference)
            if not reference.exists() or reference.read_text() != DEFAULT_CONFIG_TEMPLATE:
                _secure_write(reference, DEFAULT_CONFIG_TEMPLATE, mode=0o600)
                changed.append(str(reference))
            return
        _secure_write(target, DEFAULT_CONFIG_TEMPLATE, mode=0o600)
        changed.append(str(target))

    def _write_state_dirs(self, changed: list[str]) -> None:
        for path in (self.state_dir, self.state_dir / "history"):
            if not path.exists():
                path.mkdir(parents=True)
                changed.append(str(path))
            check_directory(path)
        if os.name == "posix":
            os.chmod(self.state_dir, 0o700)

    def _write_unit(self, name: str, content: str, changed: list[str]) -> None:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        check_directory(self.unit_dir)
        target = self.unit_dir / name
        if target.exists() or target.is_symlink():
            check_file(target, private=False)
        if target.exists() and target.read_text() == content:
            return  # idempotent: nothing to do
        _secure_write(target, content, mode=0o644)
        changed.append(str(target))


def _secure_write(path: Path, content: str, *, mode: int) -> None:
    """Atomic write with fixed permissions (no partial unit files)."""
    import uuid

    check_directory(path.parent)
    if path.exists() or path.is_symlink():
        check_file(path, private=mode == 0o600)
    tmp = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
