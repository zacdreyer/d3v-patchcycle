"""Safe subprocess execution (threat-model T1/T2/T3).

Hard rules enforced here:
- argv lists only — ``shell=True`` is impossible through this wrapper;
- scrubbed child environment by default (fixed PATH, C locale; nothing from
  the ambient environment leaks in, including LD_* and proxy variables);
- timeouts raise CommandTimeout after terminate→kill.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from patchcycle.errors import PatchCycleError

FIXED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class CommandTimeout(PatchCycleError):
    """A command exceeded its timeout and was terminated."""

    error_kind = "command-timeout"
    manual_intervention = False


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {"PATH": FIXED_PATH, "LANG": "C", "LC_ALL": "C"}
    if extra:
        env.update(extra)
    return env


def run_argv(
    argv: list[str] | tuple[str, ...],
    *,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> CommandResult:
    """Run argv safely; return a result for any exit code.

    Raises:
        CommandTimeout: the command exceeded ``timeout``.
        OSError: the binary could not be executed at all.
    """
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence, never a string (T1)")
    proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False, scrubbed env
        list(argv),
        shell=False,
        env=scrubbed_env(extra_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_tree(proc, force=False)
        try:
            proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc, force=True)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                # A descendant may have detached while retaining inherited pipes.
                # Do not let pipe drainage turn a bounded command into a hang.
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()
        raise CommandTimeout(f"command timed out after {timeout}s: {argv[0]}") from None
    return CommandResult(proc.returncode, stdout or "", stderr or "")


def _terminate_tree(proc: subprocess.Popen[str], *, force: bool) -> None:
    with contextlib.suppress(ProcessLookupError):
        if os.name == "posix":
            # getattr keeps Windows type checking compatible with POSIX-only symbols.
            getattr(os, "killpg")(proc.pid, getattr(signal, "SIGKILL") if force else signal.SIGTERM)  # noqa: B009
        elif force:
            proc.kill()
        else:
            proc.terminate()


def wait_for_lock_release(
    probe: Callable[[], bool],
    *,
    timeout_s: float,
    poll_s: float,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll a lock probe until free or timeout. Returns True if still busy.

    Never kills the holder, never deletes the lock (product mandate §14).
    """
    deadline = clock() + timeout_s
    while probe():
        if clock() >= deadline:
            return True
        sleeper(min(poll_s, max(deadline - clock(), 0)))
    return False
