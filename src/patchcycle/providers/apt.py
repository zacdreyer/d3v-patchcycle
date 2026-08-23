"""APT provider (docs/specifications/provider-contract.md §2).

Automation-safe rules honoured here (research §2-3, ADR-0009):

- ``apt-get`` only (never the interactive ``apt`` CLI);
- ``DEBIAN_FRONTEND=noninteractive``, ``DEBIAN_PRIORITY=critical``, C locale;
- dpkg conffile policy via ``--force-confdef`` + ``--force-confold``
  (default ``keep_existing``) or ``--force-confnew`` (``take_package``);
- dangerous apt-get flags are never emitted (enforced by a static test);
- PM locks are probed, never deleted; holders are never killed;
- simulation (``-s``) drives discovery; strategy semantics differ by design:
  ``safe`` → upgrade, ``full`` → dist-upgrade, ``security`` → named
  ``install --only-upgrade`` of security-pocket candidates.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from patchcycle.errors import PreflightError
from patchcycle.models import (
    ApplyResult,
    OsIdentity,
    PreflightResult,
    RebootStatus,
    RefreshResult,
    UpdateInfo,
    VerifyResult,
)
from patchcycle.providers.base import UpdateProvider
from patchcycle.subproc import CommandResult, CommandTimeout

if sys.platform != "win32":
    import fcntl

_REQUIRED_BINARIES = ("apt-get", "dpkg", "dpkg-query", "apt-cache")
_DEFAULT_SEARCH_PATHS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

# apt-get(8): 0 = success, 100 = error.
_APT_ERROR = 100

# Matches lines like:
#   Inst libc6 [2.39-0ubuntu8.3] (2.39-0ubuntu8.4 Ubuntu:24.04/noble-updates [amd64])
#   Inst linux-image-generic [6.8.0-55.57] (6.8.0-60.62 Ubuntu:24.04 [amd64]) []
_INST_RE = re.compile(r"^Inst\s+(\S+)\s+\[([^\]]+)\]\s+\((\S+)")
# Kept-back list entries: "  pkg (old => new)" — only inside the kept-back block.
_KEPT_RE = re.compile(r"^\s{2,}(\S+)\s+\((\S+)\s+=>\s+(\S+)\)")
_SECURITY_POCKET_RE = re.compile(r"-security[ /]")

#: Debian package-name grammar (deb-control): lowercase alnum start, then
#: alnum/+/-/./: — anything else never reaches an argv (defence in depth, T1).
_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-:_]*$")

Runner = Callable[..., CommandResult]


def _default_runner(
    argv: list[str], *, timeout: float, extra_env: dict[str, str] | None = None
) -> CommandResult:  # pragma: no cover - thin wiring
    from patchcycle.subproc import run_argv

    return run_argv(argv, timeout=timeout, extra_env=extra_env)


def probe_lock(path: Path) -> bool:
    """Non-blocking lock probe: True when the lock is held by someone."""
    if not path.exists():
        return False
    if sys.platform != "win32":
        fd = os.open(path, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)
    # Windows dev hosts: approximate via exclusive-open attempt.
    try:  # pragma: no cover - dev-platform approximation
        fd = os.open(path, os.O_RDWR | os.O_EXCL)
        os.close(fd)
        return False
    except OSError:
        return False


def parse_simulation(output: str) -> list[UpdateInfo]:
    """Parse ``apt-get -s`` output into UpdateInfo records.

    Robustness rule (failure-injection): malformed/unexpected output yields
    an empty/partial list, never an exception.
    """
    updates: list[UpdateInfo] = []
    lines = output.splitlines()
    for i, line in enumerate(lines):
        m = _INST_RE.match(line)
        if m:
            updates.append(
                UpdateInfo(name=m.group(1), version_from=m.group(2), version_to=m.group(3))
            )
            continue
        # "The following packages have been kept back:" list lines; the block
        # ends at the first line that is not an indented entry (e.g. the
        # "Inst ..." actions that follow).
        if line.startswith("The following packages have been kept back:"):
            for kept_line in lines[i + 1 :]:
                km = _KEPT_RE.match(kept_line)
                if not km or "Inst " in kept_line:
                    break
                updates.append(
                    UpdateInfo(
                        name=km.group(1),
                        version_from=km.group(2),
                        version_to=km.group(3),
                        held=True,
                    )
                )
            continue
    return updates


def parse_policy_security(policy_output: str) -> set[str]:
    """Names whose candidate comes from a *-security pocket (apt-cache policy)."""
    security: set[str] = set()
    current: str | None = None
    seen_candidate = False
    for line in policy_output.splitlines():
        if line and not line.startswith(" ") and line.endswith(":"):
            current = line[:-1].strip()
            seen_candidate = False
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            seen_candidate = True
            continue
        # Candidate version entry: "     <version> <pin>" then its origin lines.
        if seen_candidate and re.match(r"^\d{3}\s+\S+", stripped):
            if _SECURITY_POCKET_RE.search(stripped):
                security.add(current)
            seen_candidate = False
    return security


def _kernel_release_from_package(version: str) -> str:
    """6.8.0-60.62 -> 6.8.0-60-generic-ish release key (best effort)."""
    m = re.match(r"^(\d+\.\d+\.\d+-\d+)", version)
    return m.group(1) if m else version


class AptProvider(UpdateProvider):
    """Debian/Ubuntu provider (apt-get + dpkg)."""

    name = "apt"

    def __init__(
        self,
        os_identity: OsIdentity,
        *,
        runner: Runner | None = None,
        search_paths: tuple[str, ...] | None = None,
        state_root: Path | None = None,
        timer_active: Callable[[str], bool] | None = None,
        config_files_policy: str = "keep_existing",
        refresh_timeout_s: float = 600,
        upgrade_timeout_s: float = 3600,
    ) -> None:
        super().__init__(os_identity)
        self._runner: Runner = runner or _default_runner
        # Read the module constant at call time so tests can override it.
        self.binaries = self._resolve_binaries(search_paths or _DEFAULT_SEARCH_PATHS)
        self._state_root = state_root or Path("/")
        self._timer_active = timer_active or _systemd_timer_active
        self._conf_policy = config_files_policy
        self._refresh_timeout = refresh_timeout_s
        self._upgrade_timeout = upgrade_timeout_s

    def configure(self, config: object) -> None:
        """Adopt [updates]/[package_manager] settings from the config."""
        updates = getattr(config, "updates", None)
        pm = getattr(config, "package_manager", None)
        if updates is not None:
            self._conf_policy = updates.config_files_policy
        if pm is not None:
            self._refresh_timeout = pm.refresh_timeout_s
            self._upgrade_timeout = pm.upgrade_timeout_s

    # ------------------------------------------------------------ plumbing

    @staticmethod
    def _resolve_binaries(search_paths: tuple[str, ...]) -> dict[str, str]:
        """Resolve trusted system binaries from a fixed search path (T2)."""
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for name in _REQUIRED_BINARIES:
            found = None
            for directory in search_paths:
                candidate = Path(directory) / name
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    found = str(candidate)
                    break
            if found is None:
                missing.append(name)
            else:
                resolved[name] = found
        if missing:
            raise PreflightError(
                f"apt provider unavailable: missing binaries {', '.join(missing)} "
                f"in {':'.join(search_paths)}"
            )
        return resolved

    def _env(self) -> dict[str, str]:
        return {"DEBIAN_FRONTEND": "noninteractive", "DEBIAN_PRIORITY": "critical"}

    def _apt(self, *args: str, timeout: float) -> CommandResult:
        return self._runner(
            [self.binaries["apt-get"], *args],
            timeout=timeout,
            extra_env=self._env(),
        )

    # ------------------------------------------------------------ contract

    def preflight(self) -> PreflightResult:
        try:
            version = self._runner(
                [self.binaries["apt-get"], "--version"], timeout=30, extra_env=self._env()
            )
        except (OSError, CommandTimeout) as exc:
            return PreflightResult(ok=False, kind="pm-unhealthy", detail=str(exc))
        if version.exit_code != 0:
            detail = version.stderr.strip() or "apt-get --version failed"
            return PreflightResult(ok=False, kind="pm-unhealthy", detail=detail)
        audit = self._runner([self.binaries["dpkg"], "--audit"], timeout=120, extra_env=self._env())
        if audit.stdout.strip():
            return PreflightResult(
                ok=False,
                kind="interrupted-transaction",
                detail=(
                    "dpkg reports packages in an inconsistent state:\n"
                    + audit.stdout.strip()
                    + "\nRun 'dpkg --configure -a' manually, or set "
                    "updates.repair_interrupted = true (never removes packages)."
                ),
            )
        locks = (
            self._state_root / "var/lib/dpkg/lock-frontend",
            self._state_root / "var/lib/dpkg/lock",
            self._state_root / "var/lib/apt/lists/lock",
        )
        busy = [str(p) for p in locks if probe_lock(p)]
        if busy:
            return PreflightResult(
                ok=False,
                kind="pm-locked",
                detail="package manager locks held: " + ", ".join(busy),
            )
        warnings: list[str] = []
        for timer in ("apt-daily.timer", "apt-daily-upgrade.timer"):
            if self._timer_active(timer):
                warnings.append(
                    f"{timer} is active: unattended-upgrades may contend for apt "
                    "locks at boot (operations.md §4)"
                )
                break
        return PreflightResult(
            ok=True,
            pre_existing_reboot=self.reboot_required().required,
            warnings=tuple(warnings),
        )

    def refresh(self) -> RefreshResult:
        try:
            result = self._apt("update", timeout=self._refresh_timeout)
        except CommandTimeout as exc:
            return RefreshResult(ok=False, detail=str(exc))
        except OSError as exc:
            return RefreshResult(ok=False, detail=f"cannot execute apt-get: {exc}")
        if result.exit_code == 0:
            return RefreshResult(ok=True)
        # Partial repo failure: some indexes downloaded (apt logs W: lines).
        if "W:" in result.stderr and "Failed to fetch" in result.stderr:
            return RefreshResult(
                ok=True,
                warnings=tuple(line.strip() for line in result.stderr.splitlines() if line.strip()),
            )
        return RefreshResult(
            ok=False,
            detail=(result.stderr or result.stdout).strip()[-2000:],
        )

    def list_updates(self, strategy: str) -> list[UpdateInfo]:
        target = "dist-upgrade" if strategy == "full" else "upgrade"
        sim = self._apt("-s", target, timeout=120)
        if sim.exit_code != 0:
            raise PreflightError(
                f"apt simulation failed: {(sim.stderr or sim.stdout).strip()[-500:]}"
            )
        updates = parse_simulation(sim.stdout)
        if not updates:
            return []
        security_names = self._security_names([u.name for u in updates])
        classified = [
            UpdateInfo(
                name=u.name,
                version_from=u.version_from,
                version_to=u.version_to,
                security=u.name in security_names,
                held=u.held,
                requires_reboot_hint=u.name.startswith(("linux-image", "linux-base"))
                or u.name in ("libc6", "systemd"),
            )
            for u in updates
        ]
        if strategy == "security":
            return [u for u in classified if u.security or u.held]
        return classified

    def _security_names(self, names: list[str]) -> set[str]:
        real = [n for n in names if n]
        if not real:
            return set()
        result = self._runner(
            [self.binaries["apt-cache"], "policy", *real],
            timeout=120,
            extra_env=self._env(),
        )
        if result.exit_code != 0:
            return set()
        return parse_policy_security(result.stdout)

    def apply_updates(self, strategy: str, updates: list[UpdateInfo]) -> ApplyResult:
        conf_new_or_old = (
            "--force-confnew" if self._conf_policy == "take_package" else "--force-confold"
        )
        base_opts = [
            "-y",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "-o",
            f"Dpkg::Options::={conf_new_or_old}",
        ]
        if strategy == "security":
            targets = [
                u.name
                for u in updates
                if u.security and not u.held and _PACKAGE_NAME_RE.match(u.name)
            ]
            if not targets:
                return ApplyResult(ok=True, packages_updated=0)
            argv = [*base_opts, "install", "--only-upgrade", *targets]
        elif strategy == "full":
            argv = [*base_opts, "dist-upgrade"]
        else:
            argv = [*base_opts, "upgrade"]
        try:
            result = self._apt(*argv, timeout=self._upgrade_timeout)
        except CommandTimeout as exc:
            return ApplyResult(ok=False, detail=str(exc))
        except OSError as exc:
            return ApplyResult(ok=False, detail=f"cannot execute apt-get: {exc}")
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            return ApplyResult(ok=False, detail=detail or f"exit {result.exit_code}")
        kernel_update = "linux-image" in result.stdout
        expected_kernel = self._newest_installed_kernel() if kernel_update else None
        upgraded = [u for u in updates if not u.held]
        return ApplyResult(
            ok=True,
            packages_updated=len(upgraded),
            upgraded=tuple(upgraded),
            kernel_update=kernel_update,
            expected_kernel=expected_kernel,
        )

    def reboot_required(self) -> RebootStatus:
        reasons: list[str] = []
        sentinel = self._state_root / "var/run/reboot-required"
        pkgs_file = self._state_root / "var/run/reboot-required.pkgs"
        try:
            if sentinel.exists():
                pkgs = ""
                if pkgs_file.exists():
                    pkgs = pkgs_file.read_text(errors="replace").strip()
                reasons.append(f"reboot-required sentinel: {pkgs or 'unspecified'}")
        except OSError as exc:
            reasons.append(f"probe-error:sentinel:{exc}")
        try:
            newest = self._newest_installed_kernel()
        except (OSError, CommandTimeout) as exc:
            reasons.append(f"probe-error:kernel:{exc}")
        else:
            if newest and self.os_identity.kernel and newest != self.os_identity.kernel:
                reasons.append(
                    f"kernel-not-running: running {self.os_identity.kernel}, installed {newest}"
                )
        return RebootStatus(required=bool(reasons), reasons=tuple(reasons))

    def verify(self) -> VerifyResult:
        check = self._apt("check", timeout=120)
        if check.exit_code != 0:
            return VerifyResult(
                consistent=False,
                detail=(check.stderr or check.stdout).strip()[-1000:],
            )
        audit = self._runner([self.binaries["dpkg"], "--audit"], timeout=120, extra_env=self._env())
        if audit.stdout.strip():
            return VerifyResult(consistent=False, detail="dpkg --audit: " + audit.stdout.strip())
        outstanding = len([u for u in self.list_updates("safe") if not u.held])
        return VerifyResult(consistent=True, outstanding=outstanding)

    # ------------------------------------------------------------ helpers

    def _newest_installed_kernel(self) -> str | None:
        """Newest installed linux-image release (running-kernel comparison)."""
        result = self._runner(
            [self.binaries["dpkg-query"], "-W", "-f=${Package}\t${Version}\n", "linux-image-*"],
            timeout=60,
            extra_env=self._env(),
        )
        if result.exit_code != 0:
            return None
        best: str | None = None
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            package, version = parts
            if not package.startswith("linux-image-"):
                continue
            if package.endswith("-generic") and package.count("-") < 3:
                # meta packages carry no bootable version on their own
                continue
            release = _kernel_release_from_package(version)
            flavour = package[len("linux-image-") :]
            flavour_suffix = flavour[len(release) :] if flavour.startswith(release) else ""
            candidate = release + flavour_suffix
            if best is None or candidate > best:
                best = candidate
        return best


def _systemd_timer_active(unit: str) -> bool:  # pragma: no cover - Linux-only (L3)
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["/usr/bin/systemctl", "is-active", "--quiet", unit],
            shell=False,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0
