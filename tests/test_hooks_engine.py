"""Engine-level hook failure-policy integration (configuration.md §hooks)."""

from __future__ import annotations

from tests.test_engine import ScriptedProvider, make_engine

from patchcycle.models import HookResult


class RealPolicyHooks:
    """Hooks facade that fails a chosen point; policy from the engine config."""

    def __init__(self, fail_point: str | None, failure_policy: str = "abort"):
        self.fail_point = fail_point
        self.ran: list[str] = []
        from patchcycle.config import HooksConfig
        from patchcycle.hooks import HookRunner

        self._runner = HookRunner(HooksConfig(failure_policy=failure_policy))

    def run_all(self, hooks, point):
        self.ran.append(point)
        if point == self.fail_point:
            return [HookResult(hook=hooks[0][0], argv=hooks[0], exit_code=1, ok=False)]
        return []

    def should_abort(self, results, point):
        return self._runner.should_abort(results, point)


class TestHookFailurePaths:
    def test_before_upgrade_hook_failure_aborts_before_any_change(self, tmp_path):
        provider = ScriptedProvider()
        hooks = RealPolicyHooks("before_upgrade")
        engine, store, env = make_engine(
            tmp_path,
            provider,
            hooks=hooks,
            config_text='[hooks]\nbefore_upgrade = [["/usr/local/bin/pre"]]',
        )
        assert engine.run() == 2  # BLOCKED
        cycle = store.history()[0]
        assert cycle.outcome == "blocked"
        assert "hook" in cycle.error["message"].lower()
        # No package operation happened after the hook failure.
        assert "apply" not in provider.calls
        assert env.reboot_calls == 0

    def test_before_reboot_hook_failure_aborts_the_reboot(self, tmp_path):
        from patchcycle.models import RebootStatus

        provider = ScriptedProvider(reboot=RebootStatus(True, ("kernel",)))
        hooks = RealPolicyHooks("before_reboot")
        engine, store, env = make_engine(
            tmp_path,
            provider,
            hooks=hooks,
            config_text='[hooks]\nbefore_reboot = [["/usr/local/bin/pre-reboot"]]',
        )
        assert engine.run() == 2
        cycle = store.history()[0]
        assert cycle.outcome == "blocked"
        # Updates WERE applied; only the reboot was vetoed.
        assert cycle.updates_applied is True
        assert env.reboot_calls == 0

    def test_after_upgrade_hook_failure_continues(self, tmp_path):
        provider = ScriptedProvider()
        hooks = RealPolicyHooks("after_upgrade")
        engine, store, _ = make_engine(
            tmp_path,
            provider,
            hooks=hooks,
            config_text='[hooks]\nafter_upgrade = [["/usr/local/bin/post"]]',
        )
        assert engine.run() == 0  # after_* defaults to continue
        assert store.history()[0].outcome == "success"
        assert "after_upgrade" in hooks.ran

    def test_continue_policy_allows_failing_before_hook(self, tmp_path):
        provider = ScriptedProvider()
        hooks = RealPolicyHooks("before_upgrade", failure_policy="continue")
        engine, store, _ = make_engine(
            tmp_path,
            provider,
            hooks=hooks,
            config_text='[hooks]\nfailure_policy = "continue"\n'
            'before_upgrade = [["/usr/local/bin/pre"]]',
        )
        assert engine.run() == 0
        assert "before_upgrade" in hooks.ran
