"""Tests for the single-instance execution lock (architecture §7)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from patchcycle.errors import LockHeldError
from patchcycle.lock import ExecutionLock


class TestExecutionLock:
    def test_acquire_and_release(self, tmp_path):
        lock_path = tmp_path / "lock"
        with ExecutionLock(lock_path):
            assert lock_path.exists()
        # Re-acquiring after release works.
        with ExecutionLock(lock_path):
            pass

    def test_second_instance_fails_with_exit_3(self, tmp_path):
        lock_path = tmp_path / "lock"
        with ExecutionLock(lock_path):
            with pytest.raises(LockHeldError) as excinfo:
                ExecutionLock(lock_path).acquire()
            assert excinfo.value.exit_code == 3

    def test_lock_released_on_process_death(self, tmp_path):
        """Kernel releases flock when the holder dies: stale locks impossible."""
        lock_path = tmp_path / "lock"
        holder_src = """
import sys, time
sys.path.insert(0, r"{src}")
from patchcycle.lock import ExecutionLock
from pathlib import Path
lock = ExecutionLock(Path(r"{lock}"))
lock.acquire()
print("HELD", flush=True)
time.sleep(30)
""".format(
            src=str(__import__("pathlib").Path(__file__).parents[1] / "src"),
            lock=str(lock_path),
        )
        proc = subprocess.Popen(  # noqa: S603 - spawning the test interpreter
            [sys.executable, "-c", holder_src],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == "HELD"
            with pytest.raises(LockHeldError):
                ExecutionLock(lock_path).acquire()
        finally:
            proc.kill()
            proc.wait(timeout=10)
        # After the holder was killed, the lock is free again. Windows
        # releases byte-range locks of terminated processes asynchronously,
        # so allow a bounded grace period (POSIX flock is deterministic).
        deadline = __import__("time").monotonic() + 5
        while True:
            try:
                with ExecutionLock(lock_path):
                    pass
                break
            except LockHeldError:
                if __import__("time").monotonic() > deadline:
                    raise
                __import__("time").sleep(0.05)

    def test_lock_file_records_holder_pid(self, tmp_path):
        lock_path = tmp_path / "lock"
        with ExecutionLock(lock_path):
            pass
        # Read after release: Windows denies reading a locked byte range
        # from another handle (production targets use POSIX flock).
        content = lock_path.read_text()
        assert str(os.getpid()) in content
