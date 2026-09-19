"""Administrator-defined hooks (configuration.md §[hooks]; threat-model T8).

Hooks are argv arrays executed with a scrubbed environment and a timeout —
never shell strings. Paths must be absolute; ownership/permission validation
of the hook files themselves happens at config-check/install time.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from patchcycle.config import HooksConfig
from patchcycle.models import HookResult
from patchcycle.subproc import CommandTimeout, run_argv

#: Minimal, deterministic child environment (T3).
_HOOK_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}

#: FR-18 dry-run classification: every hook point is treated as potentially
#: disruptive and is never executed in a dry run; the plan reports this.
HOOK_CLASSIFICATION: dict[str, str] = {
    "before_upgrade": "skipped",
    "before_reboot": "skipped",
    "after_reboot": "skipped",
    "after_upgrade": "skipped",
    "on_failure": "skipped",
}


def classify_hooks() -> dict[str, str]:
    """Dry-run classification for every hook point (FR-18)."""
    return dict(HOOK_CLASSIFICATION)


class HookRunner:
    def __init__(
        self,
        config: HooksConfig,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self._sleeper = sleeper

    def run_all(self, hooks: tuple[tuple[str, ...], ...], point: str) -> list[HookResult]:
        """Run every hook for a point; returns results in order."""
        return [self._run_one(argv) for argv in hooks]

    def should_abort(self, results: list[HookResult], point: str) -> bool:
        """Failure policy: before_* hooks abort by default, after_* continue."""
        failed = any(not r.ok for r in results)
        if not failed:
            return False
        if point.startswith("after_") or point == "on_failure":
            return self.config.failure_policy == "abort" and point == "on_failure"
        return self.config.failure_policy == "abort"

    def _run_one(self, argv: tuple[str, ...]) -> HookResult:
        start = time.monotonic()
        try:
            problems = validate_hook_paths((argv,))
            if problems:
                raise OSError("; ".join(problems))
            proc = run_argv(
                list(argv),
                extra_env=_HOOK_ENV,
                timeout=self.config.timeout_s,
            )
        except CommandTimeout:
            return HookResult(
                hook=argv[0],
                argv=argv,
                exit_code=-1,
                ok=False,
                timed_out=True,
                duration_s=time.monotonic() - start,
                stderr_tail=f"hook timed out after {self.config.timeout_s}s",
            )
        except OSError as exc:
            return HookResult(
                hook=argv[0],
                argv=argv,
                exit_code=-1,
                ok=False,
                duration_s=time.monotonic() - start,
                stderr_tail=str(exc),
            )
        return HookResult(
            hook=argv[0],
            argv=argv,
            exit_code=proc.exit_code,
            ok=proc.exit_code == 0,
            duration_s=time.monotonic() - start,
            stderr_tail=(proc.stderr or "")[-2000:],
        )


def validate_hook_paths(hooks: tuple[tuple[str, ...], ...]) -> list[str]:
    """Return problems for hook paths (root-owned, not group/world-writable).

    POSIX-only enforcement; returns empty list elsewhere.
    """
    if os.name != "posix":
        return []
    return _validate_hook_paths_posix(hooks)


def _validate_hook_paths_posix(
    hooks: tuple[tuple[str, ...], ...],
) -> list[str]:  # pragma: no cover - exercised in Linux CI (L3)
    import stat
    from pathlib import Path

    from patchcycle.secure_io import check_ancestors

    problems: list[str] = []
    for argv in hooks:
        path = argv[0]
        try:
            check_ancestors(Path(path))
            st = os.lstat(path)
        except OSError as exc:
            problems.append(f"{path}: {exc}")
            continue
        if st.st_uid != 0:
            problems.append(f"{path}: not owned by root (uid={st.st_uid})")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            problems.append(f"{path}: group/world-writable hooks are forbidden")
        if not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
            problems.append(f"{path}: hook must be an executable regular file")
    return problems
