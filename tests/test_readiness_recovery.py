"""Failure-path and policy regressions from the readiness audit."""

from dataclasses import replace
from datetime import datetime

from patchcycle.models import PreflightResult, RebootStatus
from patchcycle.state_store import CycleState
from patchcycle.states import State
from test_engine import ScriptedProvider, make_engine


def test_reboot_retry_rechecks_window_before_command(tmp_path):
    engine, store, env = make_engine(
        tmp_path,
        ScriptedProvider(),
        now=datetime(2026, 8, 23, 12),
        config_text='[maintenance]\nwindow_start="02:00"\nwindow_end="04:00"\n',
    )
    store.save(
        CycleState(
            "retry",
            State.REBOOTING,
            "host",
            boot_id_before=env.boot_id,
            reboot_required=True,
            reboot_attempts=1,
            reboot_initiated_at="2026-08-23T02:29:55Z",
        )
    )
    assert engine.resume() == 2
    assert env.reboot_calls == 0


def test_unexpected_reboot_runs_read_only_diagnostics(tmp_path):
    provider = ScriptedProvider()
    engine, store, _ = make_engine(tmp_path, provider)
    store.save(CycleState("old", State.UPGRADING, "h", boot_id_before="other-boot"))
    assert engine.resume() == 1
    assert "preflight" in provider.calls
    assert "verify" in provider.calls
    assert "apply" not in provider.calls


def test_stale_cycle_does_not_resume_upgrades(tmp_path):
    provider = ScriptedProvider()
    engine, store, _ = make_engine(tmp_path, provider)
    store.save(CycleState("old", State.UPGRADING, "h", updated_at="2026-08-01T00:00:00Z"))
    assert engine.run() == 1
    assert "apply" not in provider.calls
    assert store.history()[0].error["kind"] == "stale-cycle"


def test_wait_for_users_then_recheck_window_and_reboot(tmp_path):
    engine, _, env = make_engine(tmp_path, ScriptedProvider(reboot=RebootStatus(True)))
    env.users = True
    engine.reboot.sleeper = lambda _: setattr(env, "users", False)
    assert engine.run() == 0
    assert env.reboot_calls == 1


def test_reboot_first_precedes_refresh_and_apply(tmp_path):
    provider = ScriptedProvider(preflight=PreflightResult(True, pre_existing_reboot=True))
    engine, store, env = make_engine(tmp_path, provider)
    original = env.reboot

    def reboot():
        assert "refresh" not in provider.calls
        assert "apply" not in provider.calls
        provider._preflight = PreflightResult(True)
        original()

    engine.reboot.rebooter = reboot
    assert engine.run() == 0
    assert store.history()[0].updates_applied
    assert env.reboot_calls == 1


def test_failed_failure_hook_does_not_prevent_archive(tmp_path):
    provider = ScriptedProvider(preflight=PreflightResult(False, kind="unhealthy"))
    engine, store, _ = make_engine(tmp_path, provider)
    engine.config = replace(
        engine.config, hooks=replace(engine.config.hooks, on_failure=(("/usr/local/bin/fail",),))
    )
    engine.hooks.run_all = lambda *_: (_ for _ in ()).throw(RuntimeError("hook failure"))
    assert engine.run() == 2
    assert store.history()[0].error["kind"] == "unhealthy"


def test_opted_in_repair_is_used_only_for_real_cycle(tmp_path):
    provider = ScriptedProvider(preflight=PreflightResult(False, kind="interrupted-transaction"))
    engine, _, _ = make_engine(tmp_path, provider)
    engine.config = replace(
        engine.config, updates=replace(engine.config.updates, repair_interrupted=True)
    )
    calls = []

    def repair():
        calls.append("repair")
        provider._preflight = PreflightResult(True)
        return PreflightResult(True)

    provider.repair_interrupted = repair
    import pytest

    from patchcycle.errors import PreflightError

    with pytest.raises(PreflightError):
        engine.dry_run()
    assert calls == []
    assert engine.run() == 0
    assert calls == ["repair"]


def test_low_root_disk_blocks_before_package_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    provider = ScriptedProvider()
    engine, store, _ = make_engine(tmp_path, provider)
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=100))
    assert engine.run() == 2
    assert "apply" not in provider.calls
    assert store.history()[0].error["kind"] == "disk-space"


def test_reboot_command_failure_retries_with_durable_budget(tmp_path):
    engine, store, env = make_engine(tmp_path, ScriptedProvider(reboot=RebootStatus(True)))
    attempts = []

    def reboot():
        attempts.append(store.load().reboot_attempts)
        if len(attempts) < 3:
            raise OSError("transient command failure")
        env.reboot()

    engine.reboot.rebooter = reboot
    assert engine.run() == 0
    assert attempts == [1, 2, 3]


def test_stuck_reboot_does_not_issue_late_command(tmp_path):
    engine, store, env = make_engine(tmp_path, ScriptedProvider())
    store.save(
        CycleState(
            "stuck",
            State.REBOOTING,
            "host",
            boot_id_before=env.boot_id,
            reboot_attempts=1,
            reboot_initiated_at="2026-08-23T02:00:00Z",
        )
    )
    assert engine.resume() == 1
    assert env.reboot_calls == 0


def test_process_restart_after_verified_reboot_does_not_look_like_another_boot(tmp_path):
    engine, store, env = make_engine(tmp_path, ScriptedProvider())
    cycle = CycleState(
        "after", State.VERIFYING, "host", boot_id_before="older-boot", kernel_after="kernel"
    )
    data = cycle.to_dict()
    data["boot_id_after"] = env.boot_id
    store.save(CycleState.from_dict(data))
    assert engine.run() == 0
    assert store.history()[0].error is None


def test_package_manager_lock_is_rechecked_after_before_upgrade_hook(tmp_path):
    provider = ScriptedProvider()
    engine, store, _ = make_engine(tmp_path, provider)

    def hooks(*_):
        provider._preflight = PreflightResult(False, "pm-locked", "other updater acquired lock")
        return []

    engine.config = replace(
        engine.config,
        hooks=replace(engine.config.hooks, before_upgrade=(("/fixture",),)),
        package_manager=replace(engine.config.package_manager, lock_timeout_s=0),
    )
    engine.hooks.run_all = hooks
    assert engine.run() == 2
    assert "apply" not in provider.calls
    assert store.history()[0].error["kind"] == "pm-locked"
