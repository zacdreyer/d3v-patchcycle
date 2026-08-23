"""Installer: systemd units, directories, and configuration (FR-19..FR-21).

Spec: operations.md §1, architecture §5, ADR-0003 (timers over cron).
Idempotent by construction: units are rendered deterministically from config
and only rewritten when content changes; existing administrator config is
never overwritten (a ``config.toml.new`` reference is written instead).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from patchcycle.config import Config, MaintenanceConfig

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


def render_service(config: Config, executable: str) -> str:
    # Hardening per threat-model §5; note the documented trade-offs there.
    return f"""\
[Unit]
Description=D3V PatchCycle maintenance run
Documentation=file:///etc/d3v-patchcycle/config.toml
ConditionPathExists=/etc/d3v-patchcycle/config.toml

[Service]
Type=oneshot
ExecStart={executable} run --scheduled
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
EnvironmentFile=-/etc/d3v-patchcycle/environment
StandardOutput=journal
StandardError=journal
SyslogIdentifier=d3v-patchcycle
"""


def render_resume_service(executable: str) -> str:
    return f"""\
[Unit]
Description=D3V PatchCycle post-boot maintenance resume
Documentation=file:///etc/d3v-patchcycle/config.toml
After=multi-user.target

[Service]
Type=oneshot
ExecStart={executable} resume
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
RestrictSUIDSGID=true
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
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["/usr/bin/systemctl", *args],
        shell=False,
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
        self.state_dir = state_dir or Path(config.paths.state_dir)
        self.unit_dir = unit_dir
        self.executable = executable

    # ------------------------------------------------------------- install

    def install(self) -> InstallResult:
        changed: list[str] = []
        try:
            self._systemd("daemon-reload")  # presence probe as well
        except (FileNotFoundError, OSError):
            return InstallResult(
                False,
                "systemd not available: scheduling requires a systemd-based "
                "system; no files were installed",
            )

        on_calendar = schedule_to_oncalendar(self.config.maintenance)
        if on_calendar is not None:
            code, out = self._systemd("analyze", "calendar", on_calendar)
            if code != 0:
                return InstallResult(False, f"invalid OnCalendar expression: {out}")

        self._write_config_if_missing(changed)
        self._write_state_dirs(changed)
        self._write_unit(UNIT_SERVICE, render_service(self.config, self.executable), changed)
        self._write_unit(UNIT_RESUME, render_resume_service(self.executable), changed)
        if on_calendar is not None:
            self._write_unit(UNIT_TIMER, render_timer(self.config, self.executable), changed)

        code, out = self._systemd(
            "analyze",
            "verify",
            str(self.unit_dir / UNIT_SERVICE),
            str(self.unit_dir / UNIT_RESUME),
        )
        if code != 0:
            return InstallResult(False, f"systemd-analyze verify failed: {out}")

        self._systemd("daemon-reload")
        self._systemd("enable", UNIT_RESUME)
        if on_calendar is not None:
            self._systemd("enable", "--now", UNIT_TIMER)
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
        changed: list[str] = []
        try:
            self._systemd("disable", "--now", UNIT_TIMER)
            self._systemd("disable", UNIT_RESUME)
        except (FileNotFoundError, OSError):
            pass  # systemd already gone: keep removing files
        for unit in (UNIT_TIMER, UNIT_SERVICE, UNIT_RESUME):
            path = self.unit_dir / unit
            if path.exists():
                path.unlink()
                changed.append(str(path))
        with contextlib.suppress(FileNotFoundError, OSError):
            self._systemd("daemon-reload")
        if purge:
            for path in (self.config_dir / "config.toml",):
                if path.exists():
                    path.unlink()
                    changed.append(str(path))
            if self.state_dir.exists():
                import shutil

                shutil.rmtree(self.state_dir)
                changed.append(str(self.state_dir))
        return InstallResult(True, changed=tuple(changed))

    # ------------------------------------------------------------- helpers

    def _write_config_if_missing(self, changed: list[str]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        target = self.config_dir / "config.toml"
        if target.exists():
            reference = self.config_dir / "config.toml.new"
            if not reference.exists() or reference.read_text() != DEFAULT_CONFIG_TEMPLATE:
                reference.write_text(DEFAULT_CONFIG_TEMPLATE)
                changed.append(str(reference))
            return
        _secure_write(target, DEFAULT_CONFIG_TEMPLATE, mode=0o600)
        changed.append(str(target))

    def _write_state_dirs(self, changed: list[str]) -> None:
        for path in (self.state_dir, self.state_dir / "history"):
            if not path.exists():
                path.mkdir(parents=True)
                changed.append(str(path))
        if os.name == "posix":
            os.chmod(self.state_dir, 0o700)

    def _write_unit(self, name: str, content: str, changed: list[str]) -> None:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        target = self.unit_dir / name
        if target.exists() and target.read_text() == content:
            return  # idempotent: nothing to do
        _secure_write(target, content, mode=0o644)
        changed.append(str(target))


def _secure_write(path: Path, content: str, *, mode: int) -> None:
    """Atomic write with fixed permissions (no partial unit files)."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
