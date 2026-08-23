"""Command-line interface (product spec §24).

Composition root: builds real collaborators and dispatches subcommands.
Contains no business logic. Exit codes per product specification §8.

Phase 2 note: ``run``/``resume`` acquire a real provider; with no matching
registered provider they fail safely with exit 5 (support-honesty rule,
ADR-0005). ``install``/``uninstall``/``schedule`` manage the systemd
integration (ADR-0003).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from patchcycle import STATE_SCHEMA_VERSION, __version__
from patchcycle.config import DEFAULT_CONFIG_PATH, Config, load_config
from patchcycle.engine import CycleEngine
from patchcycle.errors import ConfigError, PatchCycleError, UnsupportedPlatformError
from patchcycle.health import HealthCheckRunner
from patchcycle.hooks import HookRunner, validate_hook_paths
from patchcycle.installer import Installer, _real_systemd, schedule_to_oncalendar
from patchcycle.lock import ExecutionLock
from patchcycle.logging_setup import collect_config_secrets, configure_logging
from patchcycle.models import OsIdentity
from patchcycle.notify.base import Notifier
from patchcycle.notify.smtp import SmtpNotifier
from patchcycle.notify.webhook import WebhookNotifier
from patchcycle.osdetect import OsDetector
from patchcycle.providers import select_provider
from patchcycle.providers.base import UpdateProvider
from patchcycle.reboot import RebootController
from patchcycle.state_store import StateStore
from patchcycle.window import Window


def build_parser() -> argparse.ArgumentParser:
    # --config is accepted both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="path to config.toml"
    )
    parser = argparse.ArgumentParser(
        prog="d3v-patchcycle",
        description="Stateful, reboot-safe server maintenance utility.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", parents=[common], help="print version information")
    detect = sub.add_parser(
        "detect", parents=[common], help="show detected OS and provider support"
    )
    detect.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("config-check", parents=[common], help="validate configuration and environment")
    sub.add_parser("status", parents=[common], help="show current cycle state")
    sub.add_parser("history", parents=[common], help="show recent maintenance cycles")
    sub.add_parser("updates", parents=[common], help="list applicable updates (no changes)")
    run = sub.add_parser("run", parents=[common], help="run a maintenance cycle now")
    run.add_argument(
        "--dry-run", action="store_true", help="plan only: no changes, no state writes"
    )
    run.add_argument(
        "--force",
        action="store_true",
        help="override maintenance.enabled = false for a manual run",
    )
    run.add_argument(
        "--scheduled",
        action="store_true",
        help="invoked by the systemd timer (honours maintenance.enabled)",
    )
    resume = sub.add_parser(
        "resume", parents=[common], help="boot-time continuation (used by the resume service)"
    )
    resume.add_argument(
        "--force", action="store_true", help="allow interactive use outside the resume service"
    )
    sub.add_parser(
        "install",
        parents=[common],
        help="install config, state dirs, and systemd units (idempotent)",
    )
    uninstall = sub.add_parser("uninstall", parents=[common], help="remove systemd integration")
    uninstall.add_argument(
        "--purge",
        action="store_true",
        help="also remove configuration, state, logs, and history",
    )
    sub.add_parser(
        "schedule",
        parents=[common],
        help="show the effective schedule and re-render the systemd timer",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(f"d3v-patchcycle {__version__} (state schema v{STATE_SCHEMA_VERSION})")
        return 0
    if args.command == "detect":
        return _cmd_detect(args)
    if args.command == "config-check":
        return _cmd_config_check(args)
    if args.command in ("status", "history"):
        return _cmd_status_history(args)
    if args.command in ("run", "updates", "resume"):
        return _cmd_cycle(args)
    if args.command in ("install", "uninstall", "schedule"):
        return _cmd_install(args)
    build_parser().print_help()
    return 2


# --------------------------------------------------------------------- cmds


def _cmd_detect(args: argparse.Namespace) -> int:
    identity = OsDetector().detect()
    supported = True
    provider_name = ""
    try:
        provider_name = select_provider(identity).name
    except UnsupportedPlatformError as exc:
        supported = False
        provider_error = str(exc)
    if args.json:
        print(
            json.dumps(
                {
                    "family": identity.family,
                    "id": identity.os_id,
                    "id_like": list(identity.id_like),
                    "version_id": identity.version_id,
                    "codename": identity.codename,
                    "pretty_name": identity.pretty_name,
                    "arch": identity.arch,
                    "kernel": identity.kernel,
                    "init": identity.init,
                    "supported": supported,
                    "provider": provider_name,
                },
                indent=2,
            )
        )
    else:
        print(f"OS: {identity.pretty_name}")
        print(f"ID: {identity.os_id} (like: {', '.join(identity.id_like) or '-'})")
        print(f"Version: {identity.version_id or '-'}  Codename: {identity.codename or '-'}")
        print(f"Kernel: {identity.kernel}  Arch: {identity.arch}  Init: {identity.init or '-'}")
        if supported:
            print(f"Provider: {provider_name} (supported)")
        else:
            print(provider_error)
    return 0 if supported else 5


def _cmd_config_check(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    problems: list[str] = []
    hook_lists = (
        config.hooks.before_upgrade
        + config.hooks.before_reboot
        + config.hooks.after_reboot
        + config.hooks.after_upgrade
        + config.hooks.on_failure
    )
    problems.extend(validate_hook_paths(hook_lists))
    for check in config.health_checks:
        if check.kind == "command":
            problems.extend(validate_hook_paths((check.argv,)))
    print(f"config OK: {args.config}")
    if config.warnings:
        for warning in config.warnings:
            print(f"warning: {warning}")
    if problems:
        for problem in problems:
            print(f"problem: {problem}", file=sys.stderr)
        return 4
    print(
        f"schedule: {config.maintenance.schedule} {config.maintenance.day} "
        f"{config.maintenance.time} (random delay {config.maintenance.random_delay_s}s)"
    )
    notifiers = []
    if config.notifications.email.enabled:
        notifiers.append(f"email -> {', '.join(config.notifications.email.to)}")
    if config.notifications.webhook.enabled:
        notifiers.append(f"webhook -> {config.notifications.webhook.url}")
    print(f"notifiers: {', '.join(notifiers) if notifiers else 'none enabled'}")
    return 0


def _cmd_status_history(args: argparse.Namespace) -> int:
    config = _load_config_tolerant(args.config)
    store = StateStore(Path(config.paths.state_dir))
    if args.command == "status":
        try:
            cycle = store.load()
        except PatchCycleError as exc:
            print(str(exc), file=sys.stderr)
            return exc.exit_code
        if cycle is None:
            print("state: IDLE (no active maintenance cycle)")
            history = store.history()
            if history:
                last = history[-1]
                print(
                    f"last cycle: {last.run_id} -> {last.outcome or last.state.value} "
                    f"({last.started_at})"
                )
            return 0
        print(f"state: {cycle.state.value}")
        print(f"run_id: {cycle.run_id}")
        print(f"started: {cycle.started_at}  updated: {cycle.updated_at}")
        if cycle.error:
            print(f"error: {cycle.error.get('kind')}: {cycle.error.get('message')}")
        return 0
    cycles = store.history()
    if not cycles:
        print("no cycles in history yet")
        return 0
    for cycle in cycles[-20:]:
        outcome = cycle.outcome or cycle.state.value
        print(
            f"{cycle.started_at or '-'}  {cycle.run_id}  {outcome}  "
            f"updates={cycle.packages_updated}/{cycle.packages_pending}  "
            f"reboot={'yes' if cycle.reboot_required else 'no'}"
        )
    return 0


def _cmd_cycle(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    configure_logging(
        level=config.logging.level,
        log_file=Path(config.logging.file) if config.logging.file else None,
        secrets=collect_config_secrets(config),
    )
    logger = logging.getLogger("patchcycle")

    identity = OsDetector().detect()
    try:
        provider = select_provider(identity)
    except UnsupportedPlatformError as exc:
        print(str(exc), file=sys.stderr)
        logger.error("unsupported platform: %s", identity.pretty_name)
        return 5
    provider.configure(config)

    engine = _build_engine(config, identity, provider, logger)
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        plan = engine.dry_run()
        print(json.dumps(plan, indent=2, default=str))
        return 0

    try:
        with ExecutionLock(Path(config.paths.lock_file)):
            if args.command == "updates":
                result = engine.dry_run()
                for update in result["updates"]:
                    flags = []
                    if update.get("security"):
                        flags.append("security")
                    if update.get("held"):
                        flags.append("held")
                    print(
                        f"{update['name']}\t{update['version_from']} -> "
                        f"{update['version_to']}\t{','.join(flags)}"
                    )
                print(
                    f"{result['packages_pending']} applicable update(s); "
                    f"reboot required: {result['reboot_required']}"
                )
                return 0
            if args.command == "resume":
                return engine.resume()
            return engine.run(
                scheduled=getattr(args, "scheduled", False),
                force=getattr(args, "force", False),
            )
    except PatchCycleError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


def _cmd_install(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    installer = _make_installer(config, executable=_entrypoint())
    if args.command == "schedule":
        on_calendar = schedule_to_oncalendar(config.maintenance)
        if on_calendar is None:
            print("schedule: manual (no timer installed; run maintenance on demand)")
        else:
            print(f"schedule: {on_calendar}")
            print(f"timer: Persistent=true, RandomizedDelaySec={config.maintenance.random_delay_s}")
            print("apply with: d3v-patchcycle install")
        return 0
    if args.command == "install":
        result = installer.install()
        if not result.ok:
            print(f"install failed: {result.detail}", file=sys.stderr)
            return 2
        print(f"installed ({len(result.changed)} path(s) written)")
        for path in result.changed:
            print(f"  {path}")
        print("next: d3v-patchcycle config-check && d3v-patchcycle run --dry-run")
        return 0
    # uninstall
    purge = getattr(args, "purge", False)
    result = installer.uninstall(purge=purge)
    if not result.ok:
        print(f"uninstall failed: {result.detail}", file=sys.stderr)
        return 1
    kept = "" if purge else "; configuration, state, logs and history preserved"
    print(f"uninstalled{kept}")
    return 0


def _make_installer(config: Config, executable: str) -> Installer:
    return Installer(config=config, executable=executable)


def _build_notifiers(config: Config) -> list[Notifier]:
    """Compose enabled notifiers from [notifications] (ADR-0007)."""
    notifiers: list[Notifier] = []
    if config.notifications.email.enabled:
        notifiers.append(SmtpNotifier(config.notifications.email))
    if config.notifications.webhook.enabled:
        notifiers.append(WebhookNotifier(config.notifications.webhook))
    return notifiers


def _entrypoint() -> str:
    script = Path(sys.argv[0]).resolve()
    if script.name.startswith("d3v-patchcycle"):
        return str(script)
    return "/usr/local/bin/d3v-patchcycle"


# ----------------------------------------------------------------- building


def _load_config_tolerant(path: Path) -> Config:
    """status/history work without a valid config (paths fall back)."""
    try:
        return load_config(path)
    except ConfigError:
        return Config()


def _build_engine(
    config: Config, identity: OsIdentity, provider: UpdateProvider, logger: logging.Logger
) -> CycleEngine:
    """Composition root for real collaborators (architecture §2)."""
    state_dir = Path(config.paths.state_dir)
    store = StateStore(state_dir)
    window = Window(config.maintenance.window_start, config.maintenance.window_end)
    reboot = RebootController(
        config=config.reboot,
        window=window,
        now_local=datetime.now,
        boot_id_reader=_read_boot_id,
        kernel_reader=platform.release,
        users_logged_in=_users_logged_in,
        rebooter=_system_reboot,
        resume_path_ok=_resume_unit_enabled,
        sleeper=time.sleep,
    )
    return CycleEngine(
        store=store,
        provider=provider,
        config=config,
        hooks=HookRunner(config.hooks),
        health=HealthCheckRunner(),
        notifiers=_build_notifiers(config),
        reboot=reboot,
        hostname=platform.node(),
        is_root=lambda: os.geteuid() == 0 if hasattr(os, "geteuid") else True,
        now_iso=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now_local=datetime.now,
        sleeper=time.sleep,
        logger=logger,
    )


def _read_boot_id() -> str:  # pragma: no cover - Linux-only wiring (L3/L4)
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _users_logged_in() -> bool:  # pragma: no cover - Linux-only wiring (L3/L4)
    # S603: fixed argv, no shell, bounded timeout; system binaries only.
    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/loginctl", "list-sessions", "--no-legend"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            return any(line.strip() for line in proc.stdout.splitlines())
    except OSError:
        pass
    try:
        proc = subprocess.run(  # noqa: S603
            ["/usr/bin/who"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return bool(proc.stdout.strip())
    except OSError:
        return False


def _system_reboot() -> None:  # pragma: no cover - Linux-only wiring (L4)
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["/usr/bin/systemctl", "reboot"],
        shell=False,
        check=True,
        timeout=30,
    )


def _resume_unit_enabled() -> bool:  # pragma: no cover - Linux-only wiring (L3/L4)
    # FR-S15 fail-safe: reuse the installer's systemd seam so the check and
    # the installer can never disagree about the unit name.
    try:
        code, out = _real_systemd("is-enabled", "d3v-patchcycle-resume.service")
    except (FileNotFoundError, OSError):
        return False
    return code == 0 and out.strip() == "enabled"


if __name__ == "__main__":
    raise SystemExit(main())
