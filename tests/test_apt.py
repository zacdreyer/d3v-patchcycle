"""Tests for the APT provider (docs/specifications/provider-contract.md §2)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from patchcycle.models import OsIdentity
from patchcycle.providers.apt import (
    AptProvider,
    parse_policy_security,
    parse_simulation,
    probe_lock,
)
from patchcycle.subproc import CommandResult

DEBIAN = OsIdentity(
    family="linux",
    os_id="debian",
    version_id="13",
    codename="trixie",
    pretty_name="Debian GNU/Linux 13 (trixie)",
    arch="x86_64",
    kernel="6.12.0-amd64",
    init="systemd",
)

APT_UPDATE_OK = CommandResult(0, "Reading package lists... Done\n", "")
APT_UPDATE_PARTIAL = CommandResult(
    100,
    "Hit:1 https://mirror.example/stable stable InRelease\nReading package lists... Done\n",
    "E: Failed to fetch https://mirror.example/broken  404  Not Found\n"
    "W: Some index files failed to download.\n",
)

SIM_UPGRADE = """\
Reading package lists... Done
Building dependency tree... Done
The following packages have been kept back:
  docker-ce (5:27.1.1-1~ubuntu.24.04 => 5:27.2.0-1~ubuntu.24.04)
The following packages will be upgraded:
  libc6 libssl3t64
2 upgraded, 0 newly installed, 0 to remove and 1 not upgraded.
Inst libc6 [2.39-0ubuntu8.3] (2.39-0ubuntu8.4 Ubuntu:24.04/noble-updates [amd64])
Inst libssl3t64 [3.0.13-0ubuntu3.4] (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-security [amd64])
Conf libc6 (2.39-0ubuntu8.4 Ubuntu:24.04/noble-updates [amd64])
Conf libssl3t64 (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-security [amd64])
"""

SIM_HELD_ONLY = """\
Reading package lists... Done
The following packages have been kept back:
  linux-image-generic (6.8.0-55.57 => 6.8.0-60.62)
0 upgraded, 0 newly installed, 0 to remove and 1 not upgraded.
"""

POLICY_SECURITY = """\
libc6:
  Installed: 2.39-0ubuntu8.3
  Candidate: 2.39-0ubuntu8.4
  Version table:
     2.39-0ubuntu8.4 500
        500 http://archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages
 *** 2.39-0ubuntu8.3 100
        100 /var/lib/dpkg/status
libssl3t64:
  Installed: 3.0.13-0ubuntu3.4
  Candidate: 3.0.13-0ubuntu3.5
  Version table:
     3.0.13-0ubuntu3.5 500
        500 http://security.ubuntu.com/ubuntu noble-security/main amd64 Packages
 *** 3.0.13-0ubuntu3.4 100
        100 /var/lib/dpkg/status
"""

DPKG_KERNELS = """\
linux-image-6.8.0-55-generic	6.8.0-55.57
linux-image-6.8.0-60-generic	6.8.0-60.62
linux-image-generic	6.8.0-60.62
"""


@dataclass
class FakeRunner:
    """Records argv calls and returns scripted results."""

    results: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    default: CommandResult = field(default_factory=lambda: CommandResult(0, "", ""))

    def __call__(self, argv, *, timeout, extra_env=None):
        self.calls.append((tuple(argv), extra_env))
        for pattern, result in self.results.items():
            if isinstance(pattern, str):
                if pattern in argv:
                    return result
            elif all(p in argv for p in pattern):
                return result
        return self.default


@pytest.fixture()
def fake_bin(tmp_path):
    """Executable-looking files for provider construction."""
    bindir = tmp_path / "sbin"
    bindir.mkdir()
    for name in ("apt-get", "dpkg", "dpkg-query", "apt-cache"):
        path = bindir / name
        path.write_text("#!fixture\n")
        if os.name == "posix":
            path.chmod(0o755)  # X_OK check fails on Linux without this
    return bindir


def make_provider(runner=None, fake_bin=None, tmp_path_override=None, **kwargs) -> AptProvider:
    runner = runner or FakeRunner()
    if fake_bin is not None and "search_paths" not in kwargs:
        kwargs["search_paths"] = (str(fake_bin),)
    return AptProvider(DEBIAN, runner=runner, **kwargs)


class TestBinaryResolution:
    def test_resolves_from_fixed_search_path(self, tmp_path):
        for name in ("apt-get", "dpkg", "dpkg-query", "apt-cache"):
            path = tmp_path / name
            path.write_text("#!x\n")
            if os.name == "posix":
                path.chmod(0o755)
        provider = make_provider(search_paths=(str(tmp_path),))
        assert provider.binaries["apt-get"] == str(tmp_path / "apt-get")

    def test_missing_binary_fails_at_construction(self, tmp_path):
        (tmp_path / "apt-get").write_text("#!x\n")
        from patchcycle.errors import PreflightError

        with pytest.raises(PreflightError, match="dpkg"):
            make_provider(search_paths=(str(tmp_path),))

    def test_ambient_path_is_never_used(self, tmp_path, monkeypatch):
        """T2: a planted earlier-PATH binary must not be picked up."""
        evil = tmp_path / "evil"
        good = tmp_path / "good"
        evil.mkdir()
        good.mkdir()
        (evil / "apt-get").write_text("#!evil\n")
        for name in ("apt-get", "dpkg", "dpkg-query", "apt-cache"):
            path = good / name
            path.write_text("#!x\n")
            if os.name == "posix":
                path.chmod(0o755)
        monkeypatch.setenv("PATH", str(evil))
        provider = make_provider(search_paths=(str(good),))
        assert "evil" not in provider.binaries["apt-get"]


class TestPreflight:
    def test_healthy(self, tmp_path):
        runner = FakeRunner(
            {
                ("--version",): CommandResult(0, "apt 3.0.3\n", ""),
                ("--audit",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.preflight()
        assert result.ok is True

    def test_interrupted_transaction_detected(self, tmp_path):
        runner = FakeRunner(
            {
                ("--version",): CommandResult(0, "apt 3.0.3\n", ""),
                ("--audit",): CommandResult(
                    0,
                    "The following packages are only half configured:\n libc6\n",
                    "",
                ),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.preflight()
        assert result.ok is False
        assert result.kind == "interrupted-transaction"
        assert "libc6" in result.detail

    def test_pm_probe_does_not_confuse_flock_with_dpkg_record_locks(self, tmp_path):
        lock = tmp_path / "dpkg-lock-frontend"
        lock.write_text("")
        if os.name != "posix":
            pytest.skip("flock semantics are POSIX-only")
        import fcntl

        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # flock is independent of dpkg's fcntl record lock. The separate
            # process contention regression lives in test_readiness_providers.
            assert probe_lock(lock) is False
        finally:
            os.close(fd)
        # After close the lock is free again.
        assert probe_lock(lock) is False

    def test_pm_lock_free(self, tmp_path):
        lock = tmp_path / "free-lock"
        lock.write_text("")
        assert probe_lock(lock) is False

    def test_pre_existing_reboot_snapshot(self, tmp_path):
        sentinel_dir = tmp_path / "var" / "run"
        sentinel_dir.mkdir(parents=True)
        (sentinel_dir / "reboot-required").write_text("")
        runner = FakeRunner(
            {
                ("--version",): CommandResult(0, "apt 3.0.3\n", ""),
                ("--audit",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.preflight()
        assert result.pre_existing_reboot is True

    def test_unattended_upgrades_timers_warn(self, tmp_path):
        runner = FakeRunner(
            {
                ("--version",): CommandResult(0, "apt 3.0.3\n", ""),
                ("--audit",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(
            runner,
            state_root=tmp_path,
            timer_active=lambda unit: unit == "apt-daily-upgrade.timer",
        )
        result = provider.preflight()
        assert result.ok is True
        assert any("unattended-upgrades" in w or "apt-daily" in w for w in result.warnings)


class TestRefresh:
    def test_refresh_success(self, tmp_path):
        runner = FakeRunner({"update": APT_UPDATE_OK})
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.refresh()
        assert result.ok is True
        argv, env = runner.calls[0]
        assert "apt-get" in argv[0]
        assert "update" in argv
        assert env["DEBIAN_FRONTEND"] == "noninteractive"
        assert "DEBIAN_PRIORITY" in env

    def test_partial_repo_failure_is_warning(self, tmp_path):
        runner = FakeRunner({"update": APT_UPDATE_PARTIAL})
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.refresh()
        assert result.ok is True
        assert result.warnings

    def test_timeout(self, tmp_path):
        from patchcycle.subproc import CommandTimeout

        def timeout_runner(argv, *, timeout, extra_env=None):
            raise CommandTimeout("timed out")

        provider = make_provider(timeout_runner, state_root=tmp_path)
        result = provider.refresh()
        assert result.ok is False
        assert "timed out" in result.detail


class TestSimulationParsing:
    def test_parse_upgrades_and_held(self):
        updates = parse_simulation(SIM_UPGRADE)
        by_name = {u.name: u for u in updates}
        assert by_name["libc6"].version_from == "2.39-0ubuntu8.3"
        assert by_name["libc6"].version_to == "2.39-0ubuntu8.4"
        assert by_name["libssl3t64"].version_to == "3.0.13-0ubuntu3.5"
        held = [u for u in updates if u.held]
        assert len(held) == 1
        assert held[0].name == "docker-ce"
        assert held[0].version_from == "5:27.1.1-1~ubuntu.24.04"

    def test_held_only(self):
        updates = parse_simulation(SIM_HELD_ONLY)
        assert len(updates) == 1
        assert updates[0].held is True

    def test_security_classification_from_policy(self):
        security = parse_policy_security(POLICY_SECURITY)
        assert security == {"libssl3t64"}

    def test_malformed_simulation_is_empty_not_crash(self):
        assert parse_simulation("garbage\nlines\n") == []


class TestListUpdates:
    def test_safe_strategy(self, tmp_path):
        runner = FakeRunner(
            {
                ("-s", "upgrade"): CommandResult(0, SIM_UPGRADE, ""),
                "policy": CommandResult(0, POLICY_SECURITY, ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        updates = provider.list_updates("safe")
        names = {u.name for u in updates}
        assert names == {"libc6", "libssl3t64", "docker-ce"}
        assert next(u for u in updates if u.name == "libssl3t64").security is True
        assert next(u for u in updates if u.name == "libc6").security is False
        argv, _ = runner.calls[0]
        assert "-s" in argv and "upgrade" in argv

    def test_security_strategy_filters(self, tmp_path):
        runner = FakeRunner(
            {
                ("-s", "upgrade"): CommandResult(0, SIM_UPGRADE, ""),
                "policy": CommandResult(0, POLICY_SECURITY, ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        updates = provider.list_updates("security")
        applicable = [u for u in updates if not u.held]
        assert [u.name for u in applicable] == ["libssl3t64"]
        # Held packages are still reported for visibility.
        assert any(u.held for u in updates)

    def test_full_strategy_uses_dist_upgrade_sim(self, tmp_path):
        runner = FakeRunner(
            {
                ("-s", "dist-upgrade"): CommandResult(0, SIM_UPGRADE, ""),
                "policy": CommandResult(0, POLICY_SECURITY, ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        provider.list_updates("full")
        argv, _ = runner.calls[0]
        assert "dist-upgrade" in argv


class TestApplyUpdates:
    def test_safe_command_shape(self, tmp_path):
        out = "2 upgraded, 0 newly installed, 0 to remove and 1 not upgraded.\n"
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, out, ""),
                ("-W",): CommandResult(0, DPKG_KERNELS, ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.apply_updates("safe", [])
        assert result.ok is True
        argv, env = runner.calls[0]
        assert "upgrade" in argv and "-y" in argv
        joined = " ".join(argv)
        assert "--force-confdef" in joined
        assert "--force-confold" in joined  # keep_existing default
        assert env["DEBIAN_FRONTEND"] == "noninteractive"

    def test_full_uses_dist_upgrade(self, tmp_path):
        runner = FakeRunner(
            {
                ("dist-upgrade",): CommandResult(0, "1 upgraded, 0 newly installed.\n", ""),
                ("-W",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        assert provider.apply_updates("full", []).ok is True
        assert "dist-upgrade" in runner.calls[0][0]

    def test_security_installs_named_packages_only(self, tmp_path):
        from patchcycle.models import UpdateInfo

        updates = [UpdateInfo("libssl3t64", "a", "b", security=True)]
        runner = FakeRunner(
            {
                ("--only-upgrade",): CommandResult(0, "1 upgraded.\n", ""),
                ("-W",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.apply_updates("security", updates)
        assert result.ok is True
        argv = runner.calls[0][0]
        assert "install" in argv and "--only-upgrade" in argv
        assert "libssl3t64=b" in argv

    def test_security_with_no_security_updates_is_noop(self, tmp_path):
        from patchcycle.models import UpdateInfo

        updates = [UpdateInfo("libc6", "a", "b", security=False)]
        provider = make_provider(FakeRunner(), state_root=tmp_path)
        result = provider.apply_updates("security", updates)
        assert result.ok is True
        assert result.packages_updated == 0

    def test_exit_100_is_failure_with_detail(self, tmp_path):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(100, "", "E: dpkg was interrupted\n"),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.apply_updates("safe", [])
        assert result.ok is False
        assert "interrupted" in result.detail

    def test_kernel_update_detected(self, tmp_path):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "1 upgraded: linux-image-6.8.0-60-generic.\n", ""),
                ("-W",): CommandResult(0, DPKG_KERNELS, ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.apply_updates("safe", [])
        assert result.kernel_update is True
        assert result.expected_kernel == "6.8.0-60-generic"

    def test_take_package_policy_uses_confnew(self, tmp_path):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "0 upgraded.\n", ""),
                ("-W",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path, config_files_policy="take_package")
        provider.apply_updates("safe", [])
        joined = " ".join(runner.calls[0][0])
        assert "--force-confnew" in joined
        assert "--force-confold" not in joined


class TestProhibitedFlags:
    def test_provider_source_contains_no_dangerous_flags(self):
        """Static guarantee (ADR-0009): these flags can never be emitted."""
        src = (
            Path(__file__).parents[1] / "src" / "patchcycle" / "providers" / "apt.py"
        ).read_text()
        for forbidden in (
            "--allow-remove-essential",
            "--allow-downgrades",
            "--allow-change-held-packages",
            "--allow-unauthenticated",
            "--allow-insecure-repositories",
            "--force-yes",
            "--ignore-hold",
            "shell=True",
        ):
            assert forbidden not in src, f"prohibited flag present: {forbidden}"


class TestRebootRequired:
    def test_sentinel_file(self, tmp_path, fake_bin):
        sentinel_dir = tmp_path / "var" / "run"
        sentinel_dir.mkdir(parents=True)
        (sentinel_dir / "reboot-required").write_text("")
        (sentinel_dir / "reboot-required.pkgs").write_text("libc6\nlinux-base\n")
        # Running kernel matches the newest installed: only the sentinel speaks.
        runner = FakeRunner(
            {
                ("-W",): CommandResult(0, "linux-image-6.12.0-amd64\t6.12.0\n", ""),
            }
        )
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        status = provider.reboot_required()
        assert status.required is True
        assert any("libc6" in r for r in status.reasons)

    def test_kernel_not_running(self, tmp_path, fake_bin):
        runner = FakeRunner({("-W",): CommandResult(0, DPKG_KERNELS, "")})
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        # DEBIAN identity kernel is 6.12.0-amd64; newest installed differs.
        status = provider.reboot_required()
        assert status.required is True
        assert any("kernel-not-running" in r for r in status.reasons)

    def test_no_reboot_when_clean(self, tmp_path, fake_bin):
        runner = FakeRunner(
            {
                ("-W",): CommandResult(0, "linux-image-6.12.0-amd64\t6.12.0\n", ""),
            }
        )
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        status = provider.reboot_required()
        assert status.required is False

    def test_probe_error_is_conservative(self, tmp_path, fake_bin):
        def boom(argv, *, timeout, extra_env=None):
            raise OSError("dpkg-query exploded")

        provider = make_provider(boom, fake_bin=fake_bin, state_root=tmp_path)
        status = provider.reboot_required()
        assert status.required is True  # never miss a required reboot
        assert any(r.startswith("probe-error") for r in status.reasons)


class TestVerify:
    def test_healthy(self, tmp_path, fake_bin):
        runner = FakeRunner(
            {
                ("check",): CommandResult(0, "", ""),
                ("--audit",): CommandResult(0, "", ""),
                ("-s", "upgrade"): CommandResult(0, "0 upgraded, 0 newly installed.\n", ""),
                "policy": CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        result = provider.verify()
        assert result.consistent is True
        assert result.outstanding == 0

    def test_broken_dependencies(self, tmp_path, fake_bin):
        runner = FakeRunner(
            {
                ("check",): CommandResult(100, "", "E: Unmet dependencies\n"),
            }
        )
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        result = provider.verify()
        assert result.consistent is False

    def test_audit_dirty(self, tmp_path, fake_bin):
        runner = FakeRunner(
            {
                ("check",): CommandResult(0, "", ""),
                ("--audit",): CommandResult(0, "half-configured: libc6\n", ""),
            }
        )
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        result = provider.verify()
        assert result.consistent is False

    def test_outstanding_updates_counted(self, tmp_path, fake_bin):
        runner = FakeRunner(
            {
                ("check",): CommandResult(0, "", ""),
                ("--audit",): CommandResult(0, "", ""),
                ("-s", "upgrade"): CommandResult(0, SIM_UPGRADE, ""),
                "policy": CommandResult(0, POLICY_SECURITY, ""),
            }
        )
        provider = make_provider(runner, fake_bin=fake_bin, state_root=tmp_path)
        result = provider.verify()
        assert result.outstanding == 2  # held-back docker-ce excluded
