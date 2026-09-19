"""Timeout termination must cover the whole package-manager process group."""

import os
import signal
import subprocess

import pytest

from patchcycle.subproc import CommandTimeout, run_argv


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_timeout_signals_group_and_allows_two_minute_grace(monkeypatch):
    class Process:
        pid = 12345
        calls = []

        def communicate(self, timeout):
            self.calls.append(timeout)
            if len(self.calls) < 3:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return "", ""

        def terminate(self):
            pass

        def kill(self):
            pass

    proc = Process()
    signals = []
    monkeypatch.setattr("patchcycle.subproc.subprocess.Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    with pytest.raises(CommandTimeout):
        run_argv(["/fixture"], timeout=1)
    assert signals == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    assert proc.calls == [1, 120, 10]
