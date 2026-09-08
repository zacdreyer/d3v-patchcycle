"""Edge-case coverage for the APT provider and subproc wrapper."""

from __future__ import annotations

import pytest
from tests.test_apt import (
    POLICY_SECURITY,
    SIM_UPGRADE,
    FakeRunner,
    make_provider,
)

from patchcycle.errors import PreflightError
from patchcycle.subproc import CommandResult, CommandTimeout, wait_for_lock_release


class TestConfigure:
    def test_configure_adopts_config(self, tmp_path):
        from patchcycle.config import parse_config

        cfg = parse_config(
            __import__("tomllib").loads(
                '[updates.config_files]\npolicy = "take_package"\n'
                '[package_manager]\nrefresh_timeout = "5m"\nupgrade_timeout = "90m"'
            )
        )
        provider = make_provider(state_root=tmp_path)
        provider.configure(cfg)
        assert provider._conf_policy == "take_package"
        assert provider._refresh_timeout == 300
        assert provider._upgrade_timeout == 5400

    def test_configure_noop_on_bare_object(self, tmp_path):
        provider = make_provider(state_root=tmp_path)
        provider.configure(object())  # no attributes -> defaults retained
        assert provider._conf_policy == "keep_existing"


class TestRefreshEdges:
    def test_total_failure(self, tmp_path):
        runner = FakeRunner({"update": CommandResult(100, "", "E: All mirrors down\n")})
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.refresh()
        assert result.ok is False
        assert "mirrors" in result.detail

    def test_oserror(self, tmp_path):
        def boom(argv, *, timeout, extra_env=None):
            raise OSError("exec failed")

        provider = make_provider(boom, state_root=tmp_path)
        assert provider.refresh().ok is False


class TestListUpdatesEdges:
    def test_simulation_failure_raises(self, tmp_path):
        runner = FakeRunner({("-s", "upgrade"): CommandResult(100, "", "E: broke\n")})
        provider = make_provider(runner, state_root=tmp_path)
        with pytest.raises(PreflightError, match="simulation failed"):
            provider.list_updates("safe")

    def test_empty_simulation(self, tmp_path):
        runner = FakeRunner({("-s", "upgrade"): CommandResult(0, "0 upgraded.\n", "")})
        provider = make_provider(runner, state_root=tmp_path)
        assert provider.list_updates("safe") == []

    def test_policy_failure_refuses_unknown_security_classification(self, tmp_path):
        runner = FakeRunner(
            {
                ("-s", "upgrade"): CommandResult(0, SIM_UPGRADE, ""),
                "policy": CommandResult(100, "", "E: no cache\n"),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        from patchcycle.errors import PreflightError

        with pytest.raises(PreflightError):
            provider.list_updates("safe")

    def test_reboot_hints_flagged(self, tmp_path):
        runner = FakeRunner(
            {
                ("-s", "upgrade"): CommandResult(0, SIM_UPGRADE, ""),
                "policy": CommandResult(0, POLICY_SECURITY, ""),
            }
        )
        provider = make_provider(runner, state_root=tmp_path)
        updates = {u.name: u for u in provider.list_updates("safe")}
        assert updates["libc6"].requires_reboot_hint is True
        assert updates["libssl3t64"].requires_reboot_hint is False


class TestApplyEdges:
    def test_timeout_result(self, tmp_path):
        def timeout_runner(argv, *, timeout, extra_env=None):
            raise CommandTimeout("timed out")

        provider = make_provider(timeout_runner, state_root=tmp_path)
        result = provider.apply_updates("safe", [])
        assert result.ok is False
        assert "timed out" in result.detail

    def test_oserror_result(self, tmp_path):
        def boom(argv, *, timeout, extra_env=None):
            raise OSError("gone")

        provider = make_provider(boom, state_root=tmp_path)
        assert provider.apply_updates("safe", []).ok is False


class TestKernelParsing:
    def test_meta_package_only_returns_none(self, tmp_path):
        runner = FakeRunner({("-W",): CommandResult(0, "linux-image-generic\t6.8.0-60.62\n", "")})
        provider = make_provider(runner, state_root=tmp_path)
        assert provider._newest_installed_kernel() is None

    def test_query_failure_returns_none(self, tmp_path):
        runner = FakeRunner({("-W",): CommandResult(100, "", "no packages\n")})
        provider = make_provider(runner, state_root=tmp_path)
        assert provider._newest_installed_kernel() is None

    def test_preflight_pm_unhealthy_exit(self, tmp_path):
        runner = FakeRunner({("--version",): CommandResult(100, "", "E: busted\n")})
        provider = make_provider(runner, state_root=tmp_path)
        result = provider.preflight()
        assert result.ok is False
        assert result.kind == "pm-unhealthy"

    def test_preflight_pm_unhealthy_exec(self, tmp_path):
        def boom(argv, *, timeout, extra_env=None):
            raise OSError("gone")

        provider = make_provider(boom, state_root=tmp_path)
        assert provider.preflight().kind == "pm-unhealthy"

    def test_preflight_reports_busy_lock(self, tmp_path):
        lockdir = tmp_path / "var" / "lib" / "dpkg"
        lockdir.mkdir(parents=True)
        lock = lockdir / "lock-frontend"
        lock.write_text("")

        import patchcycle.providers.apt as apt_mod

        def busy_probe(path):
            return path == lock

        provider = make_provider(state_root=tmp_path)
        orig = apt_mod.probe_lock
        apt_mod.probe_lock = busy_probe
        try:
            result = provider.preflight()
        finally:
            apt_mod.probe_lock = orig
        assert result.ok is False
        assert result.kind == "pm-locked"


class TestWaitForLockRelease:
    def test_eventually_free(self):
        calls = iter([True, True, False])
        fake_time = [0.0]

        def clock():
            return fake_time[0]

        def sleep(s):
            fake_time[0] += s

        busy = wait_for_lock_release(
            lambda: next(calls), timeout_s=60, poll_s=10, sleeper=sleep, clock=clock
        )
        assert busy is False

    def test_timeout_returns_busy(self):
        fake_time = [0.0]

        def clock():
            return fake_time[0]

        def sleep(s):
            fake_time[0] += s

        busy = wait_for_lock_release(
            lambda: True, timeout_s=30, poll_s=10, sleeper=sleep, clock=clock
        )
        assert busy is True

    def test_real_clock_immediately_free(self):
        assert wait_for_lock_release(lambda: False, timeout_s=1, poll_s=1) is False
