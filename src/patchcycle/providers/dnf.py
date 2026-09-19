"""DNF provider (provider-contract.md §3): RHEL/Rocky/Alma/Fedora.

Critical convention (never shared with the APT provider):
``dnf check-update`` exits **100 when updates are available**, 0 when none,
1 on error — the inversion of apt-get's 100=error.
"""

from __future__ import annotations

import fnmatch
import os
import re
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
from patchcycle.providers.versionlock import is_held, locked_names
from patchcycle.subproc import CommandResult, CommandTimeout

_REQUIRED_BINARIES = ("dnf", "rpm")
_DEFAULT_SEARCH_PATHS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

# check-update line: name.arch  version-release  repo
_UPDATE_RE = re.compile(r"^(\S+)\.(\S+)\s+(\S+)\s+(\S+)\s*$")
# DNF4 uses three columns, DNF5 five; identify the NEVRA token in either.
_ADVISORY_PACKAGE_RE = re.compile(r"^(.+)-[^\s-]+-[^\s-]+\.[A-Za-z0-9_]+$")
_KERNEL_RE = re.compile(r"^kernel-core-(\S+)$")

Runner = Callable[..., CommandResult]


def _default_runner(
    argv: list[str], *, timeout: float, extra_env: dict[str, str] | None = None
) -> CommandResult:  # pragma: no cover - thin wiring
    from patchcycle.subproc import run_argv

    return run_argv(argv, timeout=timeout, extra_env=extra_env)


def parse_check_update(output: str) -> list[UpdateInfo]:
    """Parse ``dnf check-update`` output. Malformed input yields partial/empty
    results, never an exception (failure-injection rule)."""
    updates: list[UpdateInfo] = []
    in_obsoletes = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Obsoletes"):
            in_obsoletes = True
            continue
        if stripped.startswith("Last metadata") or not stripped:
            # Header/footer noise — does not toggle the obsoletes block.
            continue
        m = _UPDATE_RE.match(line)
        if m and not in_obsoletes:
            name = m.group(1)
            updates.append(
                UpdateInfo(
                    name=name,
                    version_from="",
                    version_to=m.group(3),
                    arch=m.group(2),
                    requires_reboot_hint=name in ("kernel", "kernel-core", "glibc", "systemd"),
                )
            )
    return updates


def parse_kernel_versions(rpm_output: str) -> str | None:
    """Newest installed kernel-core version-release (rpm -q output)."""
    best: str | None = None
    for line in rpm_output.splitlines():
        m = _KERNEL_RE.match(line.strip())
        if m:
            version = m.group(1)
            if best is None or version > best:
                best = version
    return best


class DnfProvider(UpdateProvider):
    """RHEL-family provider (dnf + rpm + needs-restarting plugin)."""

    name = "dnf"

    def __init__(
        self,
        os_identity: OsIdentity,
        *,
        runner: Runner | None = None,
        search_paths: tuple[str, ...] | None = None,
        state_root: Path | None = None,
        allow_erasing: bool = False,
        refresh_timeout_s: float = 600,
        upgrade_timeout_s: float = 3600,
    ) -> None:
        super().__init__(os_identity)
        self._runner: Runner = runner or _default_runner
        self.binaries = self._resolve_binaries(search_paths or _DEFAULT_SEARCH_PATHS)
        self._state_root = state_root or Path("/")
        self._allow_erasing = allow_erasing
        self._refresh_timeout = refresh_timeout_s
        self._upgrade_timeout = upgrade_timeout_s

    def configure(self, config: object) -> None:
        updates = getattr(config, "updates", None)
        if updates is not None:
            self._strategy = updates.strategy
        pm = getattr(config, "package_manager", None)
        if pm is not None:
            self._refresh_timeout = pm.refresh_timeout_s
            self._upgrade_timeout = pm.upgrade_timeout_s
        dnf_cfg = getattr(getattr(config, "updates", None), "dnf", None)
        if dnf_cfg is not None:
            self._allow_erasing = getattr(dnf_cfg, "allow_erasing", self._allow_erasing)

    # ------------------------------------------------------------ plumbing

    @staticmethod
    def _resolve_binaries(search_paths: tuple[str, ...]) -> dict[str, str]:
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
            raise PreflightError(f"dnf provider unavailable: missing binaries {', '.join(missing)}")
        return resolved

    def _dnf(self, *args: str, timeout: float) -> CommandResult:
        return self._runner(
            [self.binaries["dnf"], "--setopt=exit_on_lock=True", *args],
            timeout=timeout,
            extra_env={"LANG": "C", "LC_ALL": "C"},
        )

    # ------------------------------------------------------------ contract

    def preflight(self) -> PreflightResult:
        try:
            version = self._dnf("--version", timeout=30)
        except (OSError, CommandTimeout) as exc:
            return PreflightResult(ok=False, kind="pm-unhealthy", detail=str(exc))
        if version.exit_code != 0:
            return PreflightResult(ok=False, kind="pm-unhealthy", detail=version.stderr.strip())
        check = self._dnf("check", timeout=120)
        if check.exit_code == 200 or (
            check.exit_code != 0 and "transaction lock" in check.stderr.lower()
        ):
            return PreflightResult(
                ok=False, kind="pm-locked", detail="DNF transaction lock is held"
            )
        if check.exit_code != 0:
            return PreflightResult(
                ok=False,
                kind="interrupted-transaction",
                detail="dnf check reports package problems:\n"
                + check.stderr.strip()
                + "\nRun 'dnf clean packages && dnf check' manually.",
            )
        return PreflightResult(ok=True, pre_existing_reboot=self.reboot_required().required)

    def refresh(self) -> RefreshResult:
        try:
            result = self._dnf("-y", "makecache", "--refresh", timeout=self._refresh_timeout)
        except CommandTimeout as exc:
            return RefreshResult(ok=False, detail=str(exc))
        except OSError as exc:
            return RefreshResult(ok=False, detail=f"cannot execute dnf: {exc}")
        if result.exit_code == 0:
            return RefreshResult(ok=True)
        return RefreshResult(ok=False, detail=(result.stderr or result.stdout).strip()[-2000:])

    def list_updates(self, strategy: str) -> list[UpdateInfo]:
        self._strategy = strategy
        result = self._dnf("--refresh", "--quiet", "check-update", timeout=300)
        if result.exit_code not in (0, 100):
            raise PreflightError(
                f"dnf check-update failed: {(result.stderr or result.stdout).strip()[-500:]}"
            )
        # exit 0 = none, 100 = updates available
        updates = parse_check_update(result.stdout)
        security_names = self._security_names() if updates else set()
        locks = self._versionlocks()
        classified = [
            UpdateInfo(
                name=u.name,
                version_from=u.version_from,
                version_to=u.version_to,
                security=u.name in security_names,
                held=is_held(u, locks, self._rpm_compare),
                arch=u.arch,
                requires_reboot_hint=u.requires_reboot_hint,
            )
            for u in updates
        ]
        classified.extend(self._hidden_locked_packages(locks, classified))
        if strategy == "security":
            return [u for u in classified if u.security or u.held]
        return classified

    def _hidden_locked_packages(self, listing: str, updates: list[UpdateInfo]) -> list[UpdateInfo]:
        patterns = locked_names(listing)
        if not patterns:
            return []
        installed = self._runner(
            [
                self.binaries["rpm"],
                "-qa",
                "--queryformat",
                "%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n",
            ],
            timeout=60,
            extra_env={"LANG": "C", "LC_ALL": "C"},
        )
        if installed.exit_code != 0:
            raise PreflightError("cannot inspect installed versionlocked packages")
        visible = {(u.name, u.arch) for u in updates}
        held = []
        for line in installed.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 3:
                continue
            name, version, arch = fields
            if (name, arch) not in visible and any(fnmatch.fnmatchcase(name, p) for p in patterns):
                held.append(UpdateInfo(name, version, "", held=True, arch=arch))
        return held

    def _versionlocks(self) -> str:
        result = self._dnf("--quiet", "versionlock", "list", timeout=60)
        if result.exit_code == 0:
            return result.stdout
        error = result.stdout + result.stderr
        if "No such command" in error or 'Unknown argument "versionlock"' in error:
            return ""  # absent plugin also means native DNF cannot apply versionlocks
        raise PreflightError("cannot read native DNF versionlocks")

    def _security_names(self) -> set[str]:
        result = self._dnf(
            "--quiet", "updateinfo", "list", "--available", "--security", timeout=120
        )
        if result.exit_code != 0:
            raise PreflightError("dnf updateinfo failed; security classification is unavailable")
        names: set[str] = set()
        for line in result.stdout.splitlines():
            for token in line.split():
                match = _ADVISORY_PACKAGE_RE.fullmatch(token)
                if match:
                    names.add(match.group(1))
        return names

    def apply_updates(self, strategy: str, updates: list[UpdateInfo]) -> ApplyResult:
        if strategy == "security":
            argv = ["-y", "upgrade", "--security"]
        else:
            argv = ["-y", "upgrade", "--refresh"]
            if strategy == "full" and self._allow_erasing:
                argv.append("--allowerasing")
        try:
            result = self._dnf(*argv, timeout=self._upgrade_timeout)
        except CommandTimeout as exc:
            return ApplyResult(ok=False, detail=str(exc))
        except OSError as exc:
            return ApplyResult(ok=False, detail=f"cannot execute dnf: {exc}")
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            return ApplyResult(ok=False, detail=detail or f"exit {result.exit_code}")
        kernel_update = "kernel-core" in result.stdout or "kernel." in result.stdout
        expected_kernel = self._newest_kernel() if kernel_update else None
        upgraded = [u for u in updates if not u.held]
        count = len(upgraded) if upgraded else self._count_upgraded(result.stdout)
        return ApplyResult(
            ok=True,
            packages_updated=count,
            upgraded=tuple(upgraded),
            kernel_update=kernel_update,
            expected_kernel=expected_kernel,
        )

    @staticmethod
    def _count_upgraded(output: str) -> int:
        m = re.search(r"^Upgraded:\s*(\d+)", output, re.MULTILINE)
        return int(m.group(1)) if m else 0

    def reboot_required(self) -> RebootStatus:
        reasons: list[str] = []
        try:
            result = self._dnf("needs-restarting", "-r", timeout=60)
        except (OSError, CommandTimeout) as exc:
            return RebootStatus(required=True, reasons=(f"probe-error:{exc}",))
        plugin_missing = "No such command" in (result.stderr + result.stdout)
        if result.exit_code == 1 and not plugin_missing:
            reasons.append("needs-restarting: reboot required")
        elif result.exit_code not in (0, 1):
            reasons.append(f"probe-error:needs-restarting exit {result.exit_code}")
        if plugin_missing or not reasons:
            newest = self._newest_kernel()
            if newest and self.os_identity.kernel and newest != self.os_identity.kernel:
                reasons.append(
                    f"kernel-not-running: running {self.os_identity.kernel}, installed {newest}"
                )
        return RebootStatus(required=bool(reasons), reasons=tuple(reasons))

    def verify(self) -> VerifyResult:
        check = self._dnf("check", timeout=120)
        if check.exit_code != 0:
            return VerifyResult(
                consistent=False, detail=(check.stderr or check.stdout).strip()[-1000:]
            )
        outstanding = len(
            [u for u in self.list_updates(getattr(self, "_strategy", "safe")) if not u.held]
        )
        return VerifyResult(consistent=True, outstanding=outstanding)

    def _newest_kernel(self) -> str | None:
        result = self._runner(
            [
                self.binaries["rpm"],
                "-q",
                "kernel-core",
                "--queryformat",
                "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n",
            ],
            timeout=60,
            extra_env={"LANG": "C", "LC_ALL": "C"},
        )
        if result.exit_code != 0:
            return None
        best: str | None = None
        for line in result.stdout.splitlines():
            match = _KERNEL_RE.fullmatch(line.strip())
            if match:
                version = match.group(1)
                if best is None or self._rpm_compare(version, best) > 0:
                    best = version
        return best

    def _rpm_compare(self, version: str, previous: str) -> int:
        # RPM version labels cannot inject Lua code through this restricted grammar.
        if not all(re.fullmatch(r"[A-Za-z0-9._+~^:\-]+", value) for value in (version, previous)):
            raise PreflightError("invalid installed kernel version label")
        result = self._runner(
            [
                self.binaries["rpm"],
                "--eval",
                f"%{{lua:print(rpm.vercmp('{version}', '{previous}'))}}",
            ],
            timeout=30,
            extra_env={"LANG": "C", "LC_ALL": "C"},
        )
        if result.exit_code != 0 or result.stdout.strip() not in ("-1", "0", "1"):
            raise PreflightError("native RPM kernel comparison failed")
        return int(result.stdout.strip())
