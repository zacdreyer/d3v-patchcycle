"""Tests for the safe subprocess wrapper (threat-model T1/T2/T3)."""

from __future__ import annotations

import sys
import time

import pytest

from patchcycle.subproc import CommandTimeout, run_argv


class TestRunArgv:
    def test_captures_output_and_exit_code(self):
        result = run_argv([sys.executable, "-c", "print('hi')"], timeout=10)
        assert result.exit_code == 0
        assert result.stdout.strip() == "hi"

    def test_nonzero_exit_is_result_not_exception(self):
        result = run_argv([sys.executable, "-c", "import sys; sys.exit(42)"], timeout=10)
        assert result.exit_code == 42

    def test_stderr_captured(self):
        result = run_argv(
            [sys.executable, "-c", "import sys; sys.stderr.write('oops')"], timeout=10
        )
        assert "oops" in result.stderr

    def test_timeout_raises_and_kills(self):
        start = time.monotonic()
        with pytest.raises(CommandTimeout):
            run_argv([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
        assert time.monotonic() - start < 20  # did not wait for the sleep

    def test_scrubbed_environment_by_default(self, monkeypatch):
        monkeypatch.setenv("PATCHCYCLE_SECRET_MARKER", "leak-me-not")
        monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
        result = run_argv(
            [
                sys.executable,
                "-c",
                "import os, sys; sys.exit(0 if os.environ.get('PATCHCYCLE_SECRET_MARKER') "
                "is None and os.environ.get('LD_PRELOAD') is None else 9)",
            ],
            timeout=10,
        )
        assert result.exit_code == 0

    def test_extra_env_is_additive_and_scoped(self):
        result = run_argv(
            [
                sys.executable,
                "-c",
                "import os, sys; sys.exit(0 if os.environ.get('PATCHCYCLE_X') == '1' else 9)",
            ],
            timeout=10,
            extra_env={"PATCHCYCLE_X": "1"},
        )
        assert result.exit_code == 0
        import os

        assert os.environ.get("PATCHCYCLE_X") is None  # not leaked to parent

    def test_missing_binary_raises_oserror(self):
        with pytest.raises(OSError):
            run_argv(["/nonexistent/binary-xyz"], timeout=5)
