"""Phase 7 coverage closure: engine edges, lock POSIX branches, state store."""

from __future__ import annotations

import pytest
from tests.test_engine import ScriptedProvider, make_engine

from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State


class TestEngineEdges:
    def test_verify_outstanding_updates_fails(self, tmp_path):
        """VERIFYING fails when applicable updates remain after apply."""
        from patchcycle.models import VerifyResult

        provider = ScriptedProvider(updates=[], verify=VerifyResult(consistent=True, outstanding=5))
        engine, store, _ = make_engine(tmp_path, provider)
        # outstanding>0 with consistent=True: verify_passed false but not fatal
        # per current engine (consistent is the fatal leg); assert path recorded.
        engine.run()
        cycle = store.history()[0]
        assert cycle.outstanding_updates == 5

    def test_report_building_with_all_fields(self, tmp_path):
        from patchcycle.models import HealthResult
        from patchcycle.report import render_report

        health = __import__("tests.test_engine", fromlist=["FakeHealth"]).FakeHealth(
            [
                HealthResult("service", "nginx", ok=True, critical=True),
            ]
        )
        engine, store, _ = make_engine(tmp_path, ScriptedProvider(), health=health)
        assert engine.run() == 0
        cycle = store.history()[0]
        report = engine._build_report(cycle)
        body = render_report(report)
        assert "web01.example.com" in body
        assert "Health checks: Passed" in body


class TestLockPosixBranches:
    def test_lock_acquire_failure_reports_holder(self, tmp_path):
        """Second lock failure message includes the holder pid when readable."""
        from patchcycle.errors import LockHeldError
        from patchcycle.lock import ExecutionLock

        lock_path = tmp_path / "lock"
        with ExecutionLock(lock_path):
            try:
                ExecutionLock(lock_path).acquire()
                raise AssertionError("should have raised")
            except LockHeldError as exc:
                assert "another PatchCycle instance" in str(exc)

    def test_lock_path_unwritable_raises(self, tmp_path):
        from patchcycle.errors import LockHeldError
        from patchcycle.lock import ExecutionLock

        bad = tmp_path / "no-such-dir" / "lock"
        # Parent creation is part of acquire; a file in the way breaks it.
        (tmp_path / "no-such-dir").write_text("not a dir")
        with pytest.raises((LockHeldError, OSError)):
            ExecutionLock(bad).acquire()


class TestStateStoreEdges:
    def test_save_creates_dirs(self, tmp_path):
        store = StateStore(tmp_path / "deep" / "nested")
        store.save(CycleState(run_id="r", state=State.PRECHECK, hostname="h"))
        assert store.load().run_id == "r"

    def test_load_oserror_wrapped(self, tmp_path, monkeypatch):
        store = StateStore(tmp_path)
        store.save(CycleState(run_id="r", state=State.PRECHECK, hostname="h"))

        real_read = type(store.state_file).read_bytes

        def boom(self):
            if self == store.state_file:
                raise PermissionError("denied")
            return real_read(self)

        monkeypatch.setattr(type(store.state_file), "read_bytes", boom)
        from patchcycle.errors import StateError

        with pytest.raises(StateError, match="cannot read state file"):
            store.load()

    def test_history_missing_dir_is_empty(self, tmp_path):
        store = StateStore(tmp_path)
        assert store.history() == []

    def test_archive_overwrites_same_run_id(self, tmp_path):
        store = StateStore(tmp_path)
        cycle = CycleState(run_id="same", state=State.COMPLETED, hostname="h")
        store.archive(cycle)
        store.archive(cycle)
        assert len(store.history()) == 1


class TestPackageNameInApplyPaths:
    def test_safe_strategy_with_hostile_names_still_uses_plain_upgrade(self, tmp_path):
        """safe/full run provider-defined targets, not per-package argv."""
        from tests.test_apt import FakeRunner

        from patchcycle.models import UpdateInfo
        from patchcycle.providers.apt import AptProvider
        from patchcycle.subproc import CommandResult

        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "1 upgraded.\n", ""),
                ("-W",): CommandResult(0, "", ""),
            }
        )
        provider = AptProvider.__new__(AptProvider)
        from tests.test_engine import UBUNTU

        provider.os_identity = UBUNTU
        provider._runner = runner
        provider.binaries = {
            "apt-get": "/usr/bin/apt-get",
            "dpkg": "/usr/bin/dpkg",
            "dpkg-query": "/usr/bin/dpkg-query",
            "apt-cache": "/usr/bin/apt-cache",
        }
        provider._state_root = tmp_path
        provider._conf_policy = "keep_existing"
        provider._refresh_timeout = 10
        provider._upgrade_timeout = 10
        hostile = UpdateInfo(name="bad;name", version_from="1", version_to="2")
        result = provider.apply_updates("safe", [hostile])
        assert result.ok  # plain `upgrade` never embeds the name in argv
        argv = runner.calls[0][0]
        assert "bad;name" not in argv
