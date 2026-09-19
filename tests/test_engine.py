"""L2 engine tests: full simulated cycles incl. simulated reboot.

Specs: state-machine.md, failure-recovery.md, provider-contract.md.
All system interaction is faked; no real package manager, reboot, or network.
"""

from __future__ import annotations

from datetime import datetime

from patchcycle.config import parse_config
from patchcycle.engine import CycleEngine
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
from patchcycle.reboot import RebootController
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State
from patchcycle.window import Window

UBUNTU = OsIdentity(
    family="linux",
    os_id="ubuntu",
    id_like=("debian",),
    version_id="24.04",
    codename="noble",
    pretty_name="Ubuntu 24.04.2 LTS",
    arch="x86_64",
    kernel="6.8.0-55-generic",
    init="systemd",
)

UPDATES = [
    UpdateInfo("libc6", "2.39-0ubuntu8.3", "2.39-0ubuntu8.4", security=True),
    UpdateInfo("linux-image-6.8.0-60-generic", "", "6.8.0-60", requires_reboot_hint=True),
]


class ScriptedProvider(UpdateProvider):
    """Fake provider driven by test-supplied results."""

    name = "apt"

    def __init__(
        self,
        os_identity=UBUNTU,
        *,
        updates=UPDATES,
        preflight=None,
        apply_result=None,
        reboot=None,
        verify=None,
    ):
        super().__init__(os_identity)
        self._updates = updates
        self._preflight = preflight or PreflightResult(ok=True)
        self._apply = apply_result
        self._reboot = reboot if reboot is not None else RebootStatus(False)
        self._verify = verify or VerifyResult(consistent=True, outstanding=0)
        self.calls: list[str] = []

    def preflight(self):
        self.calls.append("preflight")
        return self._preflight

    def refresh(self):
        self.calls.append("refresh")
        return RefreshResult(ok=True)

    def list_updates(self, strategy):
        self.calls.append(f"list:{strategy}")
        return list(self._updates)

    def apply_updates(self, strategy, updates):
        self.calls.append("apply")
        if self._apply is not None:
            return self._apply
        return ApplyResult(ok=True, packages_updated=len(updates), upgraded=tuple(updates))

    def reboot_required(self):
        self.calls.append("reboot?")
        return self._reboot

    def verify(self):
        self.calls.append("verify")
        return self._verify


class FakeHooks:
    def __init__(self, abort_on: str | None = None):
        self.ran: list[str] = []
        self.abort_on = abort_on

    def run_all(self, hooks, point):
        self.ran.append(point)
        return []

    def should_abort(self, results, point):
        return point == self.abort_on


class FakeHealth:
    def __init__(self, results=()):
        self._results = list(results)

    def run(self, checks):
        return list(self._results)


class FakeNotifier:
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail
        self.deliveries = []

    def deliver(self, report, body):
        self.deliveries.append(report)
        return "failed:smtp refused" if self.fail else "sent"


class FakeRebootEnv:
    """Simulates the machine: boot id flips when 'rebooted'."""

    def __init__(self):
        self.boot_id = "boot-aaa"
        self.kernel = "6.8.0-55-generic"
        self.users = False
        self.reboot_calls = 0
        self.resume_ok = True

    def reboot(self):
        self.reboot_calls += 1
        self.boot_id = f"boot-{self.reboot_calls:03d}"


def make_engine(
    tmp_path,
    provider,
    *,
    config_text="",
    env=None,
    health=None,
    notifiers=(),
    hooks=None,
    is_root=True,
    now=datetime(2026, 8, 23, 2, 30),
):
    cfg = parse_config(__import__("tomllib").loads(config_text) if config_text else {})
    env = env or FakeRebootEnv()
    store = StateStore(tmp_path)
    reboot = RebootController(
        config=cfg.reboot,
        window=Window(cfg.maintenance.window_start, cfg.maintenance.window_end),
        now_local=lambda: now,
        boot_id_reader=lambda: env.boot_id,
        kernel_reader=lambda: env.kernel,
        users_logged_in=lambda: env.users,
        rebooter=env.reboot,
        resume_path_ok=lambda: env.resume_ok,
        sleeper=lambda s: None,
    )
    engine = CycleEngine(
        store=store,
        provider=provider,
        config=cfg,
        hooks=hooks or FakeHooks(),
        health=health or FakeHealth(),
        notifiers=list(notifiers),
        reboot=reboot,
        hostname="web01.example.com",
        is_root=lambda: is_root,
        now_iso=lambda: "2026-08-23T02:30:00Z",
        now_local=lambda: now,
        sleeper=lambda s: None,
        logger=_quiet_logger(),
    )
    return engine, store, env


def _quiet_logger():
    import logging

    logging.getLogger("patchcycle-test").handlers.clear()
    logging.getLogger("patchcycle-test").addHandler(logging.NullHandler())
    return logging.getLogger("patchcycle-test")


class TestHappyPaths:
    def test_updates_no_reboot_completes(self, tmp_path):
        engine, store, env = make_engine(tmp_path, ScriptedProvider())
        code = engine.run()
        assert code == 0
        assert store.load() is None  # archived
        history = store.history()
        assert len(history) == 1
        cycle = history[0]
        assert cycle.state is State.COMPLETED
        assert cycle.outcome == "success"
        assert cycle.packages_updated == 2
        assert cycle.updates_applied is True
        assert cycle.verify_passed is True

    def test_no_updates_cycle(self, tmp_path):
        provider = ScriptedProvider(updates=[])
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 0
        assert store.history()[0].outcome == "no_updates"
        assert "apply" not in provider.calls

    def test_full_reboot_cycle_with_kernel_transition(self, tmp_path):
        provider = ScriptedProvider(
            reboot=RebootStatus(True, ("sentinel:linux-image",)),
            apply_result=ApplyResult(
                ok=True,
                packages_updated=2,
                upgraded=tuple(UPDATES),
                kernel_update=True,
                expected_kernel="6.8.0-60-generic",
            ),
        )
        engine, store, env = make_engine(tmp_path, provider)
        env.kernel = "6.8.0-55-generic"
        # The fake reboot flips the boot id AND boots the new kernel.
        original_reboot = env.reboot

        def reboot_and_new_kernel():
            original_reboot()
            env.kernel = "6.8.0-60-generic"

        env.reboot = reboot_and_new_kernel
        engine.reboot.rebooter = env.reboot
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.outcome == "success"
        assert cycle.reboot_required is True
        assert cycle.boot_id_before == "boot-aaa"
        assert cycle.kernel_before == "6.8.0-55-generic"
        assert cycle.kernel_after == "6.8.0-60-generic"
        assert env.reboot_calls == 1
        transitions = [(t["from"], t["to"]) for t in cycle.transitions]
        assert ("REBOOTING", "POST_REBOOT") in transitions

    def test_resume_continues_interrupted_reboot_cycle(self, tmp_path):
        """The classic path: run() issues reboot (process 'dies'); resume()
        after boot completes the cycle."""
        provider = ScriptedProvider(reboot=RebootStatus(True, ("kernel",)))
        engine, store, env = make_engine(tmp_path, provider)

        # Simulate: cycle persisted right before the reboot command kills us.
        cycle = CycleState(
            run_id="run-x",
            state=State.REBOOTING,
            hostname="web01.example.com",
            updates_applied=True,
            packages_updated=2,
            reboot_required=True,
            boot_id_before="boot-aaa",
            kernel_before="6.8.0-55-generic",
            provider="apt",
            reboot_initiated_at="2026-08-23T02:14:55Z",
        )
        store.save(cycle)
        env.reboot()  # the machine actually rebooted between processes

        engine2, _, _ = make_engine(tmp_path, provider, env=env)
        assert engine2.resume() == 0
        final = store.history()[0]
        assert final.state is State.COMPLETED
        assert final.run_id == "run-x"  # same cycle, not a new one
        assert "apply" not in provider.calls  # completed work is not repeated


class TestPolicies:
    def test_reboot_never_defers_and_exits_6(self, tmp_path):
        provider = ScriptedProvider(reboot=RebootStatus(True, ("kernel",)))
        engine, store, env = make_engine(
            tmp_path, provider, config_text='[reboot]\npolicy = "never"'
        )
        assert engine.run() == 6
        cycle = store.history()[0]
        assert cycle.outcome == "manual_reboot_required"
        assert env.reboot_calls == 0

    def test_users_logged_in_blocks_reboot(self, tmp_path):
        provider = ScriptedProvider(reboot=RebootStatus(True, ("kernel",)))
        engine, store, env = make_engine(tmp_path, provider)
        env.users = True
        assert engine.run() == 2
        assert store.history()[0].outcome == "blocked"
        assert env.reboot_calls == 0

    def test_window_too_small_blocks_upgrade(self, tmp_path):
        # Window 02:00-02:10, now 02:30 local -> outside window entirely.
        engine, store, env = make_engine(
            tmp_path,
            ScriptedProvider(),
            config_text='[maintenance]\nwindow_start = "02:00"\nwindow_end = "02:10"',
            now=datetime(2026, 8, 23, 2, 30),
        )
        assert engine.run() == 2
        cycle = store.history()[0]
        assert cycle.outcome == "blocked"
        assert cycle.updates_applied is False

    def test_existing_pending_report_only(self, tmp_path):
        provider = ScriptedProvider(
            updates=[],
            preflight=PreflightResult(ok=True, pre_existing_reboot=True),
            reboot=RebootStatus(True, ("pre-existing",)),
        )
        engine, store, env = make_engine(
            tmp_path, provider, config_text='[reboot]\nexisting_pending = "report_only"'
        )
        assert engine.run() == 6
        cycle = store.history()[0]
        assert cycle.pre_existing_reboot is True
        assert env.reboot_calls == 0

    def test_dry_run_changes_nothing(self, tmp_path):
        provider = ScriptedProvider(reboot=RebootStatus(True, ("kernel",)))
        engine, store, env = make_engine(tmp_path, provider)
        plan = engine.dry_run()
        assert plan["provider"] == "apt"
        assert len(plan["updates"]) == 2
        assert plan["reboot_required"] is True
        assert store.load() is None  # no state written
        assert store.history() == []
        assert env.reboot_calls == 0
        assert "apply" not in provider.calls


class TestFailures:
    def test_preflight_lock_block(self, tmp_path):
        provider = ScriptedProvider(
            preflight=PreflightResult(ok=False, kind="pm-locked", detail="held by pid 99")
        )
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 2
        cycle = store.history()[0]
        assert cycle.state is State.FAILED
        assert cycle.error["kind"] == "pm-locked"
        assert cycle.outcome == "blocked"

    def test_apply_failure_fails_cycle_and_notifies(self, tmp_path):
        notifier = FakeNotifier()
        hooks = FakeHooks()
        provider = ScriptedProvider(
            apply_result=ApplyResult(ok=False, detail="dpkg error exit 100")
        )
        engine, store, _ = make_engine(
            tmp_path,
            provider,
            notifiers=[notifier],
            hooks=hooks,
            config_text='[hooks]\non_failure = [["/usr/local/bin/on-fail"]]',
        )
        assert engine.run() == 1
        cycle = store.history()[0]
        assert cycle.state is State.FAILED
        assert cycle.error["kind"] == "pm-failed"
        assert cycle.error["stage"] == "UPGRADING"
        assert cycle.updates_applied is False
        assert len(notifier.deliveries) == 1
        assert notifier.deliveries[0].outcome == "failed"
        assert "on_failure" in hooks.ran

    def test_verify_failure(self, tmp_path):
        provider = ScriptedProvider(
            updates=[],
            verify=VerifyResult(consistent=False, outstanding=3, detail="broken deps"),
        )
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 1
        assert store.history()[0].error["kind"] == "verify-failed"

    def test_critical_health_failure_marks_failed_but_packages_ok(self, tmp_path):
        from patchcycle.models import HealthResult

        health = FakeHealth(
            [
                HealthResult("service", "nginx", ok=True, critical=True),
                HealthResult("service", "mariadb", ok=False, critical=True, detail="inactive"),
            ]
        )
        engine, store, _ = make_engine(tmp_path, ScriptedProvider(), health=health)
        assert engine.run() == 1
        cycle = store.history()[0]
        assert cycle.outcome == "failed"
        assert cycle.updates_applied is True  # update succeeded; health failed
        assert cycle.error["kind"] == "health-check-failed"

    def test_notification_failure_keeps_maintenance_outcome(self, tmp_path):
        notifier = FakeNotifier(fail=True)
        engine, store, _ = make_engine(tmp_path, ScriptedProvider(), notifiers=[notifier])
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.outcome == "success"
        assert cycle.notification_status == {"fake": "failed:smtp refused"}

    def test_kernel_mismatch_after_reboot(self, tmp_path):
        provider = ScriptedProvider(
            reboot=RebootStatus(True, ("kernel",)),
            apply_result=ApplyResult(
                ok=True, packages_updated=1, kernel_update=True, expected_kernel="6.8.0-60-generic"
            ),
        )
        engine, store, env = make_engine(tmp_path, provider)
        # Reboot happens but the OLD kernel boots (pinned bootloader).
        assert engine.run() == 1
        cycle = store.history()[0]
        assert cycle.error["kind"] == "reboot-failed"
        assert "kernel mismatch" in cycle.error["message"]

    def test_missing_resume_path_refuses_reboot(self, tmp_path):
        provider = ScriptedProvider(reboot=RebootStatus(True, ("kernel",)))
        engine, store, env = make_engine(tmp_path, provider)
        env.resume_ok = False
        assert engine.run() == 1
        assert env.reboot_calls == 0
        assert "resume" in store.history()[0].error["message"].lower()


class TestRecovery:
    def test_same_boot_crash_before_upgrade_restarts_precheck(self, tmp_path):
        provider = ScriptedProvider(updates=[])
        engine, store, _ = make_engine(tmp_path, provider)
        store.save(
            CycleState(run_id="run-old", state=State.REFRESHING, hostname="web01.example.com")
        )
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.run_id == "run-old"  # FR-S1: same cycle continues
        assert any(t["to"] == "PRECHECK" for t in cycle.transitions)

    def test_unexpected_reboot_mid_cycle_fails_safely(self, tmp_path):
        provider = ScriptedProvider()
        engine, store, env = make_engine(tmp_path, provider)
        store.save(CycleState(run_id="run-y", state=State.UPGRADING, hostname="web01.example.com"))
        env.reboot()  # machine rebooted underneath the cycle (FR-S6)
        assert engine.resume() == 1
        cycle = store.history()[0]
        assert cycle.state is State.FAILED
        assert cycle.error["kind"] == "unexpected-reboot"
        assert "apply" not in provider.calls  # never blindly continue

    def test_reboot_command_issued_but_no_reboot(self, tmp_path):
        """FR-S7: REBOOTING persisted, same boot id on resume."""
        provider = ScriptedProvider()
        engine, store, env = make_engine(tmp_path, provider)
        store.save(
            CycleState(
                run_id="run-z",
                state=State.REBOOTING,
                hostname="h",
                boot_id_before="boot-aaa",
                reboot_attempts=3,
                provider="apt",
                updates_applied=True,
            )
        )
        # env.boot_id is still boot-aaa -> reboot never happened; budget used.
        assert engine.resume() == 1
        cycle = store.history()[0]
        assert cycle.error["kind"] == "reboot-failed"

    def test_terminal_state_resume_is_noop(self, tmp_path):
        provider = ScriptedProvider()
        engine, store, _ = make_engine(tmp_path, provider)
        cycle = CycleState(run_id="run-done", state=State.COMPLETED, hostname="h")
        store.archive(cycle)
        assert engine.resume() == 0

    def test_idle_resume_is_noop(self, tmp_path):
        engine, store, _ = make_engine(tmp_path, ScriptedProvider())
        assert engine.resume() == 0
