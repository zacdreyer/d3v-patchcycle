"""Report rendering tests (product-spec §23 formats) and engine edge cases."""

from __future__ import annotations

from tests.test_engine import ScriptedProvider, make_engine

from patchcycle.engine import render_report
from patchcycle.models import ErrorInfo, HealthResult, Outcome, ReportData
from patchcycle.state_store import CycleState
from patchcycle.states import State


class TestRenderReport:
    def test_success_report_matches_spec_format(self):
        report = ReportData(
            run_id="run-1",
            hostname="web01.example.com",
            os_pretty_name="Ubuntu 24.04 LTS",
            outcome=Outcome.SUCCESS,
            packages_available=27,
            packages_upgraded=27,
            kernel_update=True,
            reboot_required=True,
            reboot_completed=True,
            kernel_before="6.8.0-55-generic",
            kernel_after="6.8.0-60-generic",
            outstanding_updates=0,
            health_results=(HealthResult("service", "nginx", ok=True, critical=True),),
            failed_services=0,
        )
        body = render_report(report)
        assert "Host: web01.example.com" in body
        assert "OS: Ubuntu 24.04 LTS" in body
        assert "Result: SUCCESS" in body
        assert "Packages available: 27" in body
        assert "Packages upgraded: 27" in body
        assert "Kernel update: Yes" in body
        assert "Reboot required: Yes" in body
        assert "Reboot completed: Yes" in body
        assert "Kernel before: 6.8.0-55-generic" in body
        assert "Kernel after: 6.8.0-60-generic" in body
        assert "Outstanding updates: 0" in body
        assert "Health checks: Passed" in body
        assert "Failed services: 0" in body

    def test_failure_report_matches_spec_format(self):
        report = ReportData(
            run_id="run-2",
            hostname="web01.example.com",
            os_pretty_name="Ubuntu 24.04 LTS",
            outcome=Outcome.FAILED,
            error=ErrorInfo(
                kind="pm-failed",
                message="Package manager returned a failure.",
                stage="UPGRADING",
                manual_intervention=True,
            ),
        )
        body = render_report(report)
        assert "Result: FAILED" in body
        assert "Stage: UPGRADING" in body
        assert "Reason: Package manager returned a failure." in body
        assert "Manual intervention: Required" in body

    def test_notification_status_listed(self):
        report = ReportData(
            run_id="r",
            hostname="h",
            os_pretty_name="x",
            outcome=Outcome.SUCCESS,
            notification_status={"smtp": "sent", "webhook": "failed:timeout"},
        )
        body = render_report(report)
        assert "smtp: sent" in body
        assert "webhook: failed:timeout" in body


class TestEngineEdgeCases:
    def test_internal_error_is_caught_and_reported(self, tmp_path):
        provider = ScriptedProvider(updates=[])
        engine, store, _ = make_engine(tmp_path, provider)
        # Force an unexpected exception inside a handler.
        engine.provider.refresh = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        assert engine.run() == 1
        cycle = store.history()[0]
        assert cycle.state is State.FAILED
        assert cycle.error["kind"] == "internal"
        assert "boom" in cycle.error["message"]

    def test_run_with_terminal_state_file_starts_fresh_cycle(self, tmp_path):
        """A stale terminal state file (crash between save and archive) must
        not block a new cycle."""
        provider = ScriptedProvider(updates=[])
        engine, store, _ = make_engine(tmp_path, provider)
        store.save(CycleState(run_id="old-terminal", state=State.COMPLETED, hostname="h"))
        assert engine.run() == 0
        run_ids = [c.run_id for c in store.history()]
        assert "old-terminal" not in run_ids or len(run_ids) >= 1
        assert store.history()[-1].run_id != "old-terminal"

    def test_run_recovers_upgrading_crash_same_boot(self, tmp_path):
        """FR-S2: crash during UPGRADING, clean dpkg → restart at PRECHECK,
        apt re-run is idempotent."""
        provider = ScriptedProvider()
        engine, store, _ = make_engine(tmp_path, provider)
        store.save(
            CycleState(run_id="run-crash", state=State.UPGRADING, hostname="h", packages_pending=2)
        )
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.run_id == "run-crash"
        assert "apply" in provider.calls  # idempotent re-application
        assert cycle.outcome == "success"

    def test_corrupt_state_blocks_run(self, tmp_path):
        provider = ScriptedProvider()
        engine, store, _ = make_engine(tmp_path, provider)
        store.state_dir.mkdir(parents=True, exist_ok=True)
        store.state_file.write_bytes(b"{not json")
        assert engine.run() == 1  # FR-S8: refuse + quarantine
        assert list(store.state_dir.glob("state.json.corrupt.*"))

    def test_maintenance_disabled_blocks_scheduled_run(self, tmp_path):
        provider = ScriptedProvider(updates=[])
        engine, store, _ = make_engine(
            tmp_path,
            provider,
            config_text="[maintenance]\nenabled = false",
        )
        # Engine honors the enabled flag for non-forced runs.
        assert engine.run(scheduled=True) == 0
        assert store.history() == []  # no cycle was started
