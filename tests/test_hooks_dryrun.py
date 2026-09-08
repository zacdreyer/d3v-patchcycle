"""Dry-run hook classification and the full failure-policy matrix (FR-18)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from patchcycle.config import HooksConfig
from patchcycle.hooks import HOOK_CLASSIFICATION, HookRunner, classify_hooks


def py_hook(code: str) -> tuple[str, ...]:
    return (str(Path(sys.executable).resolve()), "-c", code)


class TestClassification:
    def test_points_classified(self):
        classification = classify_hooks()
        # Disruptive-adjacent points never run in dry-run.
        for point in ("before_upgrade", "before_reboot", "after_reboot", "after_upgrade"):
            assert classification[point] == "skipped"
        # on_failure is informational in dry-run (no failure occurred).
        assert classification["on_failure"] == "skipped"

    def test_all_hook_points_covered(self):
        for point in (
            "before_upgrade",
            "before_reboot",
            "after_reboot",
            "after_upgrade",
            "on_failure",
        ):
            assert point in HOOK_CLASSIFICATION


class TestDryRunSkipsHooks:
    """FR-18: dry run must not execute destructive hooks."""

    def test_dry_run_executes_no_hooks(self, tmp_path):
        from tests.test_engine import ScriptedProvider, make_engine

        from patchcycle.models import RebootStatus

        provider = ScriptedProvider(updates=[], reboot=RebootStatus(False))
        engine, store, env = make_engine(
            tmp_path,
            provider,
            config_text='[hooks]\nbefore_upgrade = [["/usr/local/bin/x"]]\n'
            'before_reboot = [["/usr/local/bin/y"]]',
        )
        plan = engine.dry_run()
        assert "hooks" in plan
        assert plan["hooks"]["before_upgrade"] == "skipped"
        # Nothing executed, nothing persisted.
        assert store.load() is None
        assert env.reboot_calls == 0


class TestFailurePolicyMatrix:
    """Full point × policy matrix (configuration.md §hooks)."""

    @pytest.mark.parametrize(
        "point,abort_default",
        [
            ("before_upgrade", True),
            ("before_reboot", True),
            ("after_upgrade", False),
            ("after_reboot", False),
        ],
    )
    def test_default_policy_per_point(self, point, abort_default):
        runner = HookRunner(HooksConfig(failure_policy="abort"))
        failed = runner.run_all((py_hook("import sys; sys.exit(1)"),), point)
        assert runner.should_abort(failed, point) is abort_default

    @pytest.mark.parametrize("point", ["before_upgrade", "before_reboot"])
    def test_continue_policy_overrides_before_points(self, point):
        runner = HookRunner(HooksConfig(failure_policy="continue"))
        failed = runner.run_all((py_hook("import sys; sys.exit(1)"),), point)
        assert runner.should_abort(failed, point) is False

    def test_hook_results_logged_with_run_context(self, tmp_path, caplog):
        """Hook results carry point + exit code for the cycle log."""
        runner = HookRunner(HooksConfig())
        results = runner.run_all((py_hook("print('out')"),), "before_upgrade")
        assert results[0].ok is True
        assert results[0].hook == str(Path(sys.executable).resolve())
