"""Configuration loading and strict validation.

Spec: docs/specifications/configuration.md. Format: TOML via stdlib
``tomllib`` (ADR-0002). Unknown keys are errors — a typo must never silently
disable a safety policy.
"""

from __future__ import annotations

import difflib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlparse

from patchcycle.errors import ConfigError

DEFAULT_CONFIG_PATH = Path("/etc/d3v-patchcycle/config.toml")

_WEEKDAYS = {
    "monday": "monday",
    "tuesday": "tuesday",
    "wednesday": "wednesday",
    "thursday": "thursday",
    "friday": "friday",
    "saturday": "saturday",
    "sunday": "sunday",
    "1": "monday",
    "2": "tuesday",
    "3": "wednesday",
    "4": "thursday",
    "5": "friday",
    "6": "saturday",
    "7": "sunday",
}
_DURATION_RE = re.compile(r"^([1-9][0-9]*)([smh]?)$")
_DURATION_MULT = {"": 1, "s": 1, "m": 60, "h": 3600}
_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_HOOK_POINT_RE = re.compile(r"^[a-z0-9][a-z0-9+._:-]*$")


def _fail(key: str, message: str, valid: list[str] | None = None) -> NoReturn:
    hint = ""
    if valid is not None:
        hint = f" Expected one of: {', '.join(valid)}."
        close = difflib.get_close_matches(message.strip("\"'"), valid, n=1)
        if close:
            hint += f' Did you mean "{close[0]}"?'
    raise ConfigError(f"config error: {key}: {message}.{hint} No maintenance actions performed.")


def _unknown_keys(key: str, table: dict[str, Any], allowed: set[str]) -> None:
    for k in table:
        if k not in allowed:
            close = difflib.get_close_matches(k, sorted(allowed), n=1)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            raise ConfigError(
                f"config error: {key}.{k}: unknown option.{hint} "
                "No maintenance actions were performed."
            )


def _duration(key: str, value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            _fail(key, f"duration must be positive, got {value}")
        return value
    if isinstance(value, str):
        m = _DURATION_RE.match(value)
        if m:
            return int(m.group(1)) * _DURATION_MULT[m.group(2)]
    _fail(key, f"invalid duration {value!r} (use e.g. 90s, 15m, 2h)")
    raise AssertionError("unreachable")


def _time_minutes(key: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and (m := _TIME_RE.match(value)):
        return int(m.group(1)) * 60 + int(m.group(2))
    _fail(key, f"invalid time {value!r} (use HH:MM, 24-hour)")
    raise AssertionError("unreachable")


def _enum(key: str, value: Any, allowed: list[str], default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str) and value in allowed:
        return value
    _fail(key, f"invalid value {value!r}", allowed)
    raise AssertionError("unreachable")


def _bool(key: str, value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    _fail(key, f"expected true/false, got {value!r}")
    raise AssertionError("unreachable")


def _str(key: str, value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str) and value:
        return value
    _fail(key, f"expected a non-empty string, got {value!r}")
    raise AssertionError("unreachable")


def _int(key: str, value: Any, default: int, minimum: int = 0, maximum: int = 65535) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum:
        return value
    _fail(key, f"expected an integer in [{minimum}, {maximum}], got {value!r}")
    raise AssertionError("unreachable")


_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _abs_path(key: str, value: Any, default: str) -> str:
    s = _str(key, value, default)
    # Target platforms are POSIX ("/..."). Drive-letter paths are accepted so
    # the dev test-suite can exercise configs on Windows hosts; they never
    # appear in production configs.
    if not (s.startswith("/") or _WIN_ABS_RE.match(s)):
        _fail(key, f"must be an absolute path, got {s!r}")
    return s


@dataclass(frozen=True)
class EstimatesConfig:
    refresh_s: int = 300
    per_package_s: int = 30
    upgrade_min_s: int = 600
    reboot_verify_s: int = 900


@dataclass(frozen=True)
class MaintenanceConfig:
    enabled: bool = True
    schedule: str = "weekly"  # daily | weekly | monthly | manual
    day: str = "sunday"
    time: str = "02:00"
    random_delay_s: int = 300
    window_start: int | None = None  # minutes since midnight, local
    window_end: int | None = None
    estimates: EstimatesConfig = field(default_factory=EstimatesConfig)


@dataclass(frozen=True)
class DnfConfig:
    allow_erasing: bool = False  # dnf full strategy escape hatch (contract §3)


@dataclass(frozen=True)
class UpdatesConfig:
    strategy: str = "safe"  # security | safe | full
    repair_interrupted: bool = False
    config_files_policy: str = "keep_existing"  # keep_existing | take_package
    dnf: DnfConfig = field(default_factory=DnfConfig)


@dataclass(frozen=True)
class RebootConfig:
    policy: str = "when_required"  # when_required | always | never | notify_only
    existing_pending: str = "reboot_first"  # reboot_first | continue_then_reboot | report_only
    allow_if_users_logged_in: bool = False
    user_wait_timeout_s: int = 1800


@dataclass(frozen=True)
class PackageManagerConfig:
    lock_timeout_s: int = 900
    lock_poll_interval_s: int = 15
    refresh_timeout_s: int = 600
    upgrade_timeout_s: int = 3600


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool = False
    to: tuple[str, ...] = ()
    from_addr: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_starttls: bool = False
    smtp_username: str = ""
    smtp_password_env: str = ""
    smtp_password: str = ""  # discouraged; triggers a warning


@dataclass(frozen=True)
class WebhookConfig:
    enabled: bool = False
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    timeout_s: int = 15


@dataclass(frozen=True)
class NotificationsConfig:
    email: EmailConfig = field(default_factory=EmailConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)


@dataclass(frozen=True)
class HooksConfig:
    before_upgrade: tuple[tuple[str, ...], ...] = ()
    before_reboot: tuple[tuple[str, ...], ...] = ()
    after_reboot: tuple[tuple[str, ...], ...] = ()
    after_upgrade: tuple[tuple[str, ...], ...] = ()
    on_failure: tuple[tuple[str, ...], ...] = ()
    timeout_s: int = 300
    failure_policy: str = "abort"  # abort | continue


@dataclass(frozen=True)
class HealthCheckConfig:
    kind: str  # service | http | tcp | command
    critical: bool = True
    # service
    name: str = ""
    # http
    url: str = ""
    expected_status: int = 200
    # tcp
    host: str = ""
    port: int = 0
    # command
    argv: tuple[str, ...] = ()
    timeout_s: int = 10


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "info"  # debug | info | warning | error
    file: str = ""


@dataclass(frozen=True)
class PathsConfig:
    state_dir: str = "/var/lib/d3v-patchcycle"
    lock_file: str = "/run/d3v-patchcycle/lock"


@dataclass(frozen=True)
class Config:
    """Validated configuration. Immutable by construction."""

    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    reboot: RebootConfig = field(default_factory=RebootConfig)
    package_manager: PackageManagerConfig = field(default_factory=PackageManagerConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    health_checks: tuple[HealthCheckConfig, ...] = ()
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    warnings: tuple[str, ...] = ()


def _parse_hooks(key: str, value: Any) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        _fail(key, f"expected a list of argv arrays, got {value!r}")
    result = []
    for i, entry in enumerate(value):
        if not isinstance(entry, list) or not entry or not all(isinstance(a, str) for a in entry):
            _fail(f"{key}[{i}]", f"expected a non-empty argv array, got {entry!r}")
        if not entry[0].startswith("/"):
            _fail(f"{key}[{i}]", f"hook path must be absolute, got {entry[0]!r}")
        if not _HOOK_POINT_RE.match(entry[0].split("/")[-1]):
            _fail(f"{key}[{i}]", f"invalid hook executable name {entry[0]!r}")
        result.append(tuple(entry))
    return tuple(result)


def _parse_health_entry(kind: str, k: str, entry: Any) -> HealthCheckConfig:
    if not isinstance(entry, dict):
        _fail(k, f"expected a table, got {entry!r}")
    critical = _bool(f"{k}.critical", entry.get("critical"), True)
    timeout = _duration(f"{k}.timeout", entry.get("timeout", "10s"))
    if kind == "service":
        _unknown_keys(k, entry, {"name", "critical", "timeout"})
        name = _str(f"{k}.name", entry.get("name"), "")
        if not _HOOK_POINT_RE.match(name):
            _fail(f"{k}.name", f"invalid service name {name!r}")
        return HealthCheckConfig(kind, critical, name=name, timeout_s=timeout)
    if kind == "http":
        _unknown_keys(k, entry, {"url", "expected_status", "timeout", "critical"})
        url = _str(f"{k}.url", entry.get("url"), "")
        if urlparse(url).scheme not in ("http", "https"):
            _fail(f"{k}.url", f"must be http(s), got {url!r}")
        status = _int(f"{k}.expected_status", entry.get("expected_status"), 200, 100, 599)
        return HealthCheckConfig(
            kind, critical, name=url, url=url, expected_status=status, timeout_s=timeout
        )
    if kind == "tcp":
        _unknown_keys(k, entry, {"host", "port", "timeout", "critical"})
        host = _str(f"{k}.host", entry.get("host"), "")
        port = _int(f"{k}.port", entry.get("port"), 0, 1, 65535)
        return HealthCheckConfig(
            kind, critical, name=f"{host}:{port}", host=host, port=port, timeout_s=timeout
        )
    _unknown_keys(k, entry, {"argv", "timeout", "critical"})
    argv: Any = entry.get("argv")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
        _fail(f"{k}.argv", f"expected a non-empty argv array, got {argv!r}")
    if not argv[0].startswith("/"):
        _fail(f"{k}.argv", f"command path must be absolute, got {argv[0]!r}")
    return HealthCheckConfig(kind, critical, name=argv[0], argv=tuple(argv), timeout_s=timeout)


def _parse_health(data: dict[str, Any]) -> tuple[HealthCheckConfig, ...]:
    checks: list[HealthCheckConfig] = []
    for kind, table in data.items():
        if kind not in {"service", "http", "tcp", "command"}:
            _fail(
                f"health.{kind}", "unknown health check type", ["service", "http", "tcp", "command"]
            )
        if not isinstance(table, list):
            _fail(f"health.{kind}", "expected [[health.<kind>]] tables")
        for i, entry in enumerate(table):
            checks.append(_parse_health_entry(kind, f"health.{kind}[{i}]", entry))
    return tuple(checks)


def load_config(path: Path) -> Config:
    """Load and strictly validate a TOML config file.

    Raises:
        ConfigError: file unreadable, malformed, or failing validation.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"config error: cannot read {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"config error: {path}: invalid TOML: {exc}") from exc
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> Config:
    """Validate an already-parsed TOML document (also used by tests)."""
    _unknown_keys(
        "config",
        data,
        {
            "maintenance",
            "updates",
            "reboot",
            "package_manager",
            "notifications",
            "hooks",
            "health",
            "logging",
            "paths",
        },
    )
    warnings: list[str] = []

    # [maintenance]
    m = data.get("maintenance", {})
    _unknown_keys(
        "maintenance",
        m,
        {
            "enabled",
            "schedule",
            "day",
            "time",
            "random_delay",
            "window_start",
            "window_end",
            "estimates",
        },
    )
    schedule = _enum(
        "maintenance.schedule",
        m.get("schedule"),
        ["daily", "weekly", "monthly", "manual"],
        "weekly",
    )
    day_raw = m.get("day", "sunday")
    if schedule == "weekly":
        day = _WEEKDAYS.get(str(day_raw).lower())
        if day is None:
            _fail("maintenance.day", f"invalid weekday {day_raw!r}")
    elif schedule == "monthly":
        if not (isinstance(day_raw, int) and 1 <= day_raw <= 28) and str(day_raw) not in {
            str(d) for d in range(1, 29)
        }:
            _fail("maintenance.day", f"monthly day must be 1..28, got {day_raw!r}")
        day = str(day_raw)
    else:
        day = str(day_raw)
    time_str = m.get("time", "02:00")
    if _time_minutes("maintenance.time", time_str) is None:
        _fail("maintenance.time", f"invalid time {time_str!r}")
    random_delay = _duration("maintenance.random_delay", m.get("random_delay", "5m"))
    if random_delay > 3600:
        _fail("maintenance.random_delay", "must be <= 1h")
    window_start = _time_minutes("maintenance.window_start", m.get("window_start"))
    window_end = _time_minutes("maintenance.window_end", m.get("window_end"))
    if (window_start is None) != (window_end is None):
        _fail("maintenance.window_*", "window_start and window_end must be set together")

    est = m.get("estimates", {})
    _unknown_keys(
        "maintenance.estimates", est, {"refresh", "per_package", "upgrade_min", "reboot_verify"}
    )
    estimates = EstimatesConfig(
        refresh_s=_duration("maintenance.estimates.refresh", est.get("refresh", "5m")),
        per_package_s=_duration("maintenance.estimates.per_package", est.get("per_package", "30s")),
        upgrade_min_s=_duration("maintenance.estimates.upgrade_min", est.get("upgrade_min", "10m")),
        reboot_verify_s=_duration(
            "maintenance.estimates.reboot_verify", est.get("reboot_verify", "15m")
        ),
    )
    maintenance = MaintenanceConfig(
        enabled=_bool("maintenance.enabled", m.get("enabled"), True),
        schedule=schedule,
        day=day,
        time=time_str,
        random_delay_s=random_delay,
        window_start=window_start,
        window_end=window_end,
        estimates=estimates,
    )

    # [updates]
    u = data.get("updates", {})
    _unknown_keys("updates", u, {"strategy", "repair_interrupted", "config_files", "dnf"})
    cf = u.get("config_files", {})
    _unknown_keys("updates.config_files", cf, {"policy"})
    cf_policy = _enum(
        "updates.config_files.policy",
        cf.get("policy"),
        ["keep_existing", "take_package"],
        "keep_existing",
    )
    if cf_policy == "take_package":
        warnings.append(
            "updates.config_files.policy=take_package replaces locally modified "
            "configuration files with package defaults (ADR-0009)."
        )
    dnf_tbl = u.get("dnf", {})
    _unknown_keys("updates.dnf", dnf_tbl, {"allow_erasing"})
    allow_erasing = _bool("updates.dnf.allow_erasing", dnf_tbl.get("allow_erasing"), False)
    if allow_erasing:
        warnings.append(
            "updates.dnf.allow_erasing=true permits dnf to remove packages during "
            "full upgrades (--allowerasing); use with care (contract §3)."
        )
    updates = UpdatesConfig(
        strategy=_enum("updates.strategy", u.get("strategy"), ["security", "safe", "full"], "safe"),
        repair_interrupted=_bool("updates.repair_interrupted", u.get("repair_interrupted"), False),
        config_files_policy=cf_policy,
        dnf=DnfConfig(allow_erasing=allow_erasing),
    )

    # [reboot]
    r = data.get("reboot", {})
    _unknown_keys(
        "reboot", r, {"policy", "existing_pending", "allow_if_users_logged_in", "user_wait_timeout"}
    )
    reboot = RebootConfig(
        policy=_enum(
            "reboot.policy",
            r.get("policy"),
            ["when_required", "always", "never", "notify_only"],
            "when_required",
        ),
        existing_pending=_enum(
            "reboot.existing_pending",
            r.get("existing_pending"),
            ["reboot_first", "continue_then_reboot", "report_only"],
            "reboot_first",
        ),
        allow_if_users_logged_in=_bool(
            "reboot.allow_if_users_logged_in", r.get("allow_if_users_logged_in"), False
        ),
        user_wait_timeout_s=_duration(
            "reboot.user_wait_timeout", r.get("user_wait_timeout", "30m")
        ),
    )

    # [package_manager]
    pm = data.get("package_manager", {})
    _unknown_keys(
        "package_manager",
        pm,
        {"lock_timeout", "lock_poll_interval", "refresh_timeout", "upgrade_timeout"},
    )
    package_manager = PackageManagerConfig(
        lock_timeout_s=_duration("package_manager.lock_timeout", pm.get("lock_timeout", "15m")),
        lock_poll_interval_s=_duration(
            "package_manager.lock_poll_interval", pm.get("lock_poll_interval", "15s")
        ),
        refresh_timeout_s=_duration(
            "package_manager.refresh_timeout", pm.get("refresh_timeout", "10m")
        ),
        upgrade_timeout_s=_duration(
            "package_manager.upgrade_timeout", pm.get("upgrade_timeout", "60m")
        ),
    )

    notifications = _parse_notifications(data, warnings)
    hooks, logging_cfg, paths = _parse_rest(data)
    health_checks = _parse_health(data.get("health", {}))

    return Config(
        maintenance=maintenance,
        updates=updates,
        reboot=reboot,
        package_manager=package_manager,
        notifications=notifications,
        hooks=hooks,
        health_checks=health_checks,
        logging=logging_cfg,
        paths=paths,
        warnings=tuple(warnings),
    )


def _parse_notifications(data: dict[str, Any], warnings: list[str]) -> NotificationsConfig:
    n = data.get("notifications", {})
    _unknown_keys("notifications", n, {"email", "webhook"})
    e = n.get("email", {})
    _unknown_keys(
        "notifications.email",
        e,
        {
            "enabled",
            "to",
            "from",
            "smtp_host",
            "smtp_port",
            "smtp_starttls",
            "smtp_username",
            "smtp_password_env",
            "smtp_password",
        },
    )
    email_enabled = _bool("notifications.email.enabled", e.get("enabled"), False)
    to_raw = e.get("to", [])
    if isinstance(to_raw, str):
        to_raw = [to_raw]
    if not isinstance(to_raw, list) or not all(isinstance(t, str) for t in to_raw):
        _fail("notifications.email.to", f"expected a list of addresses, got {to_raw!r}")
    if email_enabled and not to_raw:
        _fail("notifications.email.to", "required when notifications.email.enabled = true")
    if e.get("smtp_password"):
        warnings.append(
            "notifications.email.smtp_password stores a secret in the config file; "
            "prefer smtp_password_env with a systemd EnvironmentFile (threat-model T5)."
        )
    has_credentials = e.get("smtp_username") or e.get("smtp_password_env")
    if email_enabled and has_credentials and not e.get("smtp_starttls"):
        warnings.append(
            "SMTP credentials configured without smtp_starttls; credentials may cross "
            "the network in cleartext (threat-model T9)."
        )
    email = EmailConfig(
        enabled=email_enabled,
        to=tuple(to_raw),
        from_addr=_str("notifications.email.from", e.get("from"), "") if e.get("from") else "",
        smtp_host=_str("notifications.email.smtp_host", e.get("smtp_host"), "localhost"),
        smtp_port=_int("notifications.email.smtp_port", e.get("smtp_port"), 25, 1, 65535),
        smtp_starttls=_bool("notifications.email.smtp_starttls", e.get("smtp_starttls"), False),
        smtp_username=_str("notifications.email.smtp_username", e.get("smtp_username"), "")
        if e.get("smtp_username")
        else "",
        smtp_password_env=(  # noqa: S105 - holds an env var NAME, not a secret
            _str("notifications.email.smtp_password_env", e.get("smtp_password_env"), "")
            if e.get("smtp_password_env")
            else ""
        ),
        smtp_password=str(e.get("smtp_password", "")),
    )

    w = n.get("webhook", {})
    _unknown_keys("notifications.webhook", w, {"enabled", "url", "headers", "timeout"})
    webhook_enabled = _bool("notifications.webhook.enabled", w.get("enabled"), False)
    url = _str("notifications.webhook.url", w.get("url"), "") if w.get("url") else ""
    if webhook_enabled:
        if not url:
            _fail("notifications.webhook.url", "required when webhook.enabled = true")
        parsed = urlparse(url)
        loopback = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            _fail(
                "notifications.webhook.url",
                f"must be https (http only allowed on loopback), got {url!r}",
            )
    headers_raw = w.get("headers", {})
    if not isinstance(headers_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers_raw.items()
    ):
        _fail("notifications.webhook.headers", "expected a string map")
    webhook = WebhookConfig(
        enabled=webhook_enabled,
        url=url,
        headers=tuple(sorted(headers_raw.items())),
        timeout_s=_duration("notifications.webhook.timeout", w.get("timeout", "15s")),
    )
    return NotificationsConfig(email=email, webhook=webhook)


def _parse_rest(data: dict[str, Any]) -> tuple[HooksConfig, LoggingConfig, PathsConfig]:
    """Parse [hooks], [logging], and [paths] sections."""
    h = data.get("hooks", {})
    _unknown_keys(
        "hooks",
        h,
        {
            "before_upgrade",
            "before_reboot",
            "after_reboot",
            "after_upgrade",
            "on_failure",
            "timeout",
            "failure_policy",
        },
    )
    hooks = HooksConfig(
        before_upgrade=_parse_hooks("hooks.before_upgrade", h.get("before_upgrade")),
        before_reboot=_parse_hooks("hooks.before_reboot", h.get("before_reboot")),
        after_reboot=_parse_hooks("hooks.after_reboot", h.get("after_reboot")),
        after_upgrade=_parse_hooks("hooks.after_upgrade", h.get("after_upgrade")),
        on_failure=_parse_hooks("hooks.on_failure", h.get("on_failure")),
        timeout_s=_duration("hooks.timeout", h.get("timeout", "5m")),
        failure_policy=_enum(
            "hooks.failure_policy", h.get("failure_policy"), ["abort", "continue"], "abort"
        ),
    )

    # [logging]
    lg = data.get("logging", {})
    _unknown_keys("logging", lg, {"level", "file"})
    logging_cfg = LoggingConfig(
        level=_enum(
            "logging.level", lg.get("level"), ["debug", "info", "warning", "error"], "info"
        ),
        file=_abs_path("logging.file", lg.get("file"), "") if lg.get("file") else "",
    )

    # [paths]
    p = data.get("paths", {})
    _unknown_keys("paths", p, {"state_dir", "lock_file"})
    paths = PathsConfig(
        state_dir=_abs_path("paths.state_dir", p.get("state_dir"), "/var/lib/d3v-patchcycle"),
        lock_file=_abs_path("paths.lock_file", p.get("lock_file"), "/run/d3v-patchcycle/lock"),
    )
    return hooks, logging_cfg, paths
