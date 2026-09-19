"""Audit regressions: real asynchronous reboot and truthful durable outcomes."""

from dataclasses import replace

import pytest

from patchcycle.models import HealthResult, PreflightResult, RebootStatus, VerifyResult
from patchcycle.states import State
from test_engine import FakeHealth, FakeNotifier, ScriptedProvider, make_engine


def test_held_packages_remain_visible_without_being_applied(tmp_path):
    from patchcycle.models import UpdateInfo

    provider = ScriptedProvider(updates=(UpdateInfo("held", "1", "2", held=True),))
    engine, store, _ = make_engine(tmp_path, provider)
    assert engine.run() == 0
    cycle = store.history()[0]
    assert engine._build_report(cycle).packages_held == 1
    assert "apply" not in provider.calls


def test_configured_secret_is_redacted_from_failure_archive_and_report(tmp_path):
    provider = ScriptedProvider(
        preflight=PreflightResult(False, detail="failed with secret-canary")
    )
    engine, store, _ = make_engine(tmp_path, provider)
    email = replace(engine.config.notifications.email, smtp_password="secret-canary")  # noqa: S106 - redaction canary
    engine.config = replace(
        engine.config, notifications=replace(engine.config.notifications, email=email)
    )
    assert engine.run() == 2
    for path in store.history_dir.iterdir():
        assert "secret-canary" not in path.read_text()


def test_reboot_facts_durable_at_command_boundary(tmp_path):
    engine, store, _ = make_engine(tmp_path, ScriptedProvider(reboot=RebootStatus(True)))

    def power_off():
        saved = store.load()
        assert saved.state == State.REBOOTING
        assert saved.reboot_attempts == 1
        assert saved.reboot_initiated_at == engine.now_iso()
        raise SystemExit(0)  # process disappears before the command returns

    engine.reboot.rebooter = power_off
    with pytest.raises(SystemExit):
        engine.run()


def test_async_reboot_leaves_pending_then_new_process_resumes(tmp_path):
    provider = ScriptedProvider(reboot=RebootStatus(True))
    engine, store, env = make_engine(tmp_path, provider)
    engine.reboot.rebooter = lambda: None  # systemctl queues shutdown and returns
    assert engine.run() == 0
    assert store.load().state == State.REBOOTING
    assert store.history() == []
    assert "verify" not in provider.calls
    env.reboot()
    assert engine.resume() == 0
    assert store.history()[0].outcome == "success"
    assert provider.calls.count("apply") == 1


@pytest.mark.parametrize("updates,expected", [(None, "success"), ([], "no_updates")])
def test_delivered_outcome_matches_archive_and_text_report(tmp_path, updates, expected):
    provider = ScriptedProvider() if updates is None else ScriptedProvider(updates=[])
    notifier = FakeNotifier()
    engine, store, _ = make_engine(tmp_path, provider, notifiers=[notifier])
    assert engine.run() == 0
    final = store.history()[0]
    assert notifier.deliveries[0].outcome == expected
    assert final.outcome == expected
    assert not notifier.deliveries[0].reboot_completed
    report = (store.history_dir / f"{final.run_id}.report.txt").read_text()
    assert f"Result: {expected.upper()}" in report


def test_report_persisted_without_notifiers(tmp_path):
    engine, store, _ = make_engine(tmp_path, ScriptedProvider())
    assert engine.run() == 0
    assert list(store.history_dir.glob("*.report.txt"))


def test_noncritical_health_failure_is_warning_in_delivery_and_history(tmp_path):
    notifier = FakeNotifier()
    health = FakeHealth([HealthResult("http", "health", False, False)])
    engine, store, _ = make_engine(
        tmp_path, ScriptedProvider(), health=health, notifiers=[notifier]
    )
    assert engine.run() == 0
    assert store.history()[0].outcome == "success_with_warnings"
    assert notifier.deliveries[0].outcome == "success_with_warnings"


def test_outstanding_applicable_updates_fail_verification(tmp_path):
    provider = ScriptedProvider(verify=VerifyResult(True, outstanding=1))
    engine, store, _ = make_engine(tmp_path, provider)
    assert engine.run() == 1
    assert store.history()[0].outstanding_updates == 1


def test_unexpected_notifier_exception_does_not_fail_maintenance(tmp_path):
    notifier = FakeNotifier()
    notifier.deliver = lambda *_: (_ for _ in ()).throw(RuntimeError("secret-server-text"))
    engine, store, _ = make_engine(tmp_path, ScriptedProvider(), notifiers=[notifier])
    assert engine.run() == 0
    assert store.history()[0].outcome == "success"
    assert "secret-server-text" not in str(store.history()[0].notification_status)


def test_dry_run_stops_after_failed_preflight(tmp_path):
    provider = ScriptedProvider(preflight=PreflightResult(False, kind="pm-locked"))
    engine, _, _ = make_engine(tmp_path, provider)
    from patchcycle.errors import PreflightError

    with pytest.raises(PreflightError):
        engine.dry_run()
    assert "refresh" not in provider.calls


def test_terminal_state_archived_on_resume_without_redelivery(tmp_path):
    notifier = FakeNotifier()
    engine, store, _ = make_engine(tmp_path, ScriptedProvider(), notifiers=[notifier])
    cycle = replace(engine._new_cycle(), state=State.COMPLETED, outcome="success")
    store.save(cycle)
    assert engine.resume() == 0
    assert store.load() is None
    assert store.history()[0].run_id == cycle.run_id
    assert notifier.deliveries == []


def test_partial_refresh_warning_survives_archive_and_report(tmp_path):
    from patchcycle.models import RefreshResult
    from test_engine import ScriptedProvider, make_engine

    provider = ScriptedProvider()
    provider.refresh = lambda: RefreshResult(True, warnings=("one mirror unavailable",))
    engine, store, _ = make_engine(tmp_path, provider)
    assert engine.run() == 0
    cycle = store.history()[0]
    assert cycle.outcome == "success_with_warnings"
    assert cycle.warnings == ("one mirror unavailable",)
    assert (
        "one mirror unavailable" in (store.history_dir / f"{cycle.run_id}.report.txt").read_text()
    )


def test_dry_run_planning_honors_refresh_estimate(tmp_path):
    from test_engine import ScriptedProvider, make_engine

    engine, _, _ = make_engine(
        tmp_path,
        ScriptedProvider(updates=[]),
        config_text='[maintenance.estimates]\nrefresh="17s"\n',
    )
    assert engine.dry_run()["estimated_duration_s"] == 17


def test_early_failure_report_does_not_invent_verified_health_or_update_count(tmp_path):
    from patchcycle.models import PreflightResult
    from patchcycle.report import render_report
    from test_engine import ScriptedProvider, make_engine

    engine, store, _ = make_engine(
        tmp_path, ScriptedProvider(preflight=PreflightResult(False, kind="interrupted-transaction"))
    )
    assert engine.run() == 2
    report = engine._build_report(store.history()[0])
    assert report.outstanding_updates is None
    assert "Health checks: Not run" in render_report(report)
    assert "Outstanding updates: Unknown" in render_report(report)
