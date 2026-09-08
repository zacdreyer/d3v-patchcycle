"""Tests for the hook runner (configuration.md §hooks; threat-model T8)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from patchcycle.config import HooksConfig
from patchcycle.hooks import HookRunner, validate_hook_paths


def hook_cfg(**overrides) -> HooksConfig:
    base = {"timeout_s": 10, "failure_policy": "abort"}
    base.update(overrides)
    return HooksConfig(**base)


def py_hook(code: str) -> tuple[str, ...]:
    return (str(Path(sys.executable).resolve()), "-c", code)


class TestExecution:
    def test_successful_hook(self):
        runner = HookRunner(hook_cfg())
        results = runner.run_all((py_hook("print('hello')"),), "before_upgrade")
        assert len(results) == 1
        assert results[0].ok is True
        assert results[0].exit_code == 0
        assert results[0].hook == str(Path(sys.executable).resolve())

    def test_failing_hook_captured(self):
        runner = HookRunner(hook_cfg())
        results = runner.run_all((py_hook("import sys; sys.exit(3)"),), "before_upgrade")
        assert results[0].ok is False
        assert results[0].exit_code == 3

    def test_hook_timeout(self):
        runner = HookRunner(hook_cfg(timeout_s=1))
        results = runner.run_all((py_hook("import time; time.sleep(30)"),), "before_upgrade")
        assert results[0].ok is False
        assert results[0].timed_out is True

    def test_missing_hook_binary(self):
        runner = HookRunner(hook_cfg())
        results = runner.run_all((("/nonexistent/hook",),), "before_upgrade")
        assert results[0].ok is False
        assert "nonexistent" in results[0].stderr_tail or results[0].exit_code == -1

    def test_environment_is_scrubbed(self, monkeypatch):
        """T3: parent env secrets must not leak into hook processes."""
        monkeypatch.setenv("PATCHCYCLE_TEST_SECRET", "should-not-leak")
        monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
        runner = HookRunner(hook_cfg())
        results = runner.run_all(
            (
                py_hook(
                    "import os, sys; sys.exit(0 if os.environ.get('PATCHCYCLE_TEST_SECRET') "
                    "is None and os.environ.get('LD_PRELOAD') is None else 7)"
                ),
            ),
            "before_upgrade",
        )
        assert results[0].exit_code == 0

    def test_no_shell_interpretation(self, tmp_path):
        """T1: shell metacharacters in argv must not execute a shell."""
        marker = tmp_path / "pwned"
        runner = HookRunner(hook_cfg())
        results = runner.run_all(
            (py_hook(f"open(r'{marker}', 'w').write('no')") + ("; touch /tmp/pwned",),),
            "before_upgrade",
        )
        # The extra "shell-looking" argument is just an argv element.
        assert results[0].exit_code == 0
        if os.name == "posix":
            assert not Path("/tmp/pwned").exists()


class TestFailurePolicy:
    def test_abort_policy(self):
        runner = HookRunner(hook_cfg(failure_policy="abort"))
        failed = runner.run_all((py_hook("import sys; sys.exit(1)"),), "before_reboot")
        assert runner.should_abort(failed, "before_reboot") is True

    def test_continue_policy(self):
        runner = HookRunner(hook_cfg(failure_policy="continue"))
        failed = runner.run_all((py_hook("import sys; sys.exit(1)"),), "before_upgrade")
        assert runner.should_abort(failed, "before_upgrade") is False

    def test_after_hooks_default_to_continue(self):
        runner = HookRunner(hook_cfg(failure_policy="abort"))
        failed = runner.run_all((py_hook("import sys; sys.exit(1)"),), "after_upgrade")
        assert runner.should_abort(failed, "after_upgrade") is False

    def test_all_passing_never_aborts(self):
        runner = HookRunner(hook_cfg())
        ok = runner.run_all((py_hook("pass"),), "before_reboot")
        assert runner.should_abort(ok, "before_reboot") is False


class TestPathValidation:
    def test_nonexistent_hook_flagged(self):
        problems = validate_hook_paths((("/definitely/not/here",),))
        if os.name == "posix":
            assert any("definitely" in p for p in problems)
        else:
            assert problems == []  # POSIX-only enforcement

    def test_non_root_owned_flagged_on_posix(self, tmp_path):
        if os.name != "posix":
            return
        if os.geteuid() == 0:
            # Running as root (e.g. CI container): chown to a non-root uid.
            hook = tmp_path / "hook.sh"
            hook.write_text("#!/bin/sh\n")
            os.chown(hook, 65534, 65534)  # nobody
        else:
            hook = tmp_path / "hook.sh"
            hook.write_text("#!/bin/sh\n")
        problems = validate_hook_paths(((str(hook),),))
        assert problems  # owned by non-root → flagged
