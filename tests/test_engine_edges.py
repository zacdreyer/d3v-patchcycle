"""Engine edge-path tests: lock waits, retries, notifier resilience."""

from __future__ import annotations

from tests.test_engine import ScriptedProvider, make_engine

from patchcycle.models import (
    HealthResult,
    PreflightResult,
)
from patchcycle.state_store import CycleState
from patchcycle.states import State


class TestLockWait:
    def test_pm_locked_then_free_succeeds(self, tmp_path):
        """Lock wait retries within lock_timeout (architecture §7)."""
        calls = {"n": 0}

        provider = ScriptedProvider(updates=[])

        def flaky_preflight():
            calls["n"] += 1
            if calls["n"] < 3:
                return PreflightResult(ok=False, kind="pm-locked", detail="busy")
            return PreflightResult(ok=True)

        provider.preflight = flaky_preflight
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 0
        assert calls["n"] == 3


class TestNotifyResilience:
    def test_notifier_exception_does_not_crash_failure_path(self, tmp_path):
        from patchcycle.models import ApplyResult

        class ExplodingNotifier:
            name = "exploder"

            def deliver(self, report, body):
                raise RuntimeError("smtp library exploded")

        provider = ScriptedProvider(
            apply_result=ApplyResult(ok=False, detail="dpkg broke"),
        )
        engine, store, _ = make_engine(tmp_path, provider, notifiers=[ExplodingNotifier()])
        assert engine.run() == 1
        cycle = store.history()[0]
        assert cycle.state is State.FAILED
        assert "exploder" in cycle.notification_status
        assert cycle.notification_status["exploder"].startswith("failed")

    def test_notifier_eventually_succeeds_after_retries(self, tmp_path):
        attempts = {"n": 0}

        class FlakyNotifier:
            name = "flaky"

            def deliver(self, report, body):
                attempts["n"] += 1
                return "sent" if attempts["n"] == 2 else "failed:timeout"

        engine, store, _ = make_engine(
            tmp_path, ScriptedProvider(updates=[]), notifiers=[FlakyNotifier()]
        )
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.notification_status == {"flaky": "sent"}
        assert attempts["n"] == 2


class TestHealthOutcomes:
    def test_non_critical_health_failure_is_warning(self, tmp_path):
        health_results = [
            HealthResult("http", "selfcheck", ok=False, critical=False, detail="timeout"),
        ]

        class H:
            def run(self, checks):
                return health_results

        engine, store, _ = make_engine(tmp_path, ScriptedProvider(updates=[]), health=H())
        # Non-critical health failure: cycle outcome stays success-ish but the
        # failure is in the report/state.
        code = engine.run()
        cycle = store.history()[0]
        assert code == 0
        assert cycle.health_results[0]["ok"] is False


class TestResumeEdges:
    def test_resume_with_corrupt_state_reports_error(self, tmp_path):
        engine, store, _ = make_engine(tmp_path, ScriptedProvider())
        store.state_dir.mkdir(parents=True, exist_ok=True)
        store.state_file.write_bytes(b"\x00\x01binary garbage")
        assert engine.resume() == 1  # StateError exit path
        assert list(store.state_dir.glob("state.json.corrupt.*"))

    def test_resume_verifying_continues_in_place(self, tmp_path):
        """Crash after reboot during VERIFYING: re-enters VERIFYING."""
        provider = ScriptedProvider(updates=[])
        engine, store, env = make_engine(tmp_path, provider)
        env.reboot()  # current boot = boot-001
        store.save(
            CycleState(
                run_id="run-v",
                state=State.VERIFYING,
                hostname="h",
                updates_applied=True,
                boot_id_before="boot-aaa",
            )
        )
        assert engine.resume() == 0
        cycle = store.history()[0]
        assert cycle.run_id == "run-v"
        assert cycle.verify_passed is True
