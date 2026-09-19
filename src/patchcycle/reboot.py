"""Reboot policy evaluation and reboot verification (ADR-0008).

The controller never assumes a reboot happened: it compares the kernel boot
ID before/after and verifies an expected kernel transition. The actual
``systemctl reboot`` invocation is the injected ``rebooter`` callable; the
resume-unit existence check is the injected ``resume_path_ok`` callable
(FR-S15 fail-safe).
"""

from __future__ import annotations

import enum
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from patchcycle.config import RebootConfig
from patchcycle.errors import RebootError
from patchcycle.state_store import CycleState
from patchcycle.window import Window


class RebootAction(enum.StrEnum):
    REBOOT = "reboot"
    DEFER_MANUAL = "defer_manual"  # policy forbids; admin must reboot (exit 6)
    SKIP = "skip"  # not required and policy != always


@dataclass(frozen=True)
class RebootDecision:
    action: RebootAction
    reason: str = ""


@dataclass
class RebootController:
    config: RebootConfig
    window: Window
    now_local: Callable[[], datetime]
    boot_id_reader: Callable[[], str]
    kernel_reader: Callable[[], str]
    users_logged_in: Callable[[], bool]
    rebooter: Callable[[], None]
    resume_path_ok: Callable[[], bool]
    sleeper: Callable[[float], None]
    max_attempts: int = 3

    def evaluate(self, *, reboot_required: bool, estimate_s: int) -> RebootDecision:
        """Apply [reboot] policy, session rules, and the maintenance window."""
        policy = self.config.policy
        if policy in ("never", "notify_only"):
            if reboot_required or policy == "always":
                return RebootDecision(RebootAction.DEFER_MANUAL, f"policy={policy}")
            return RebootDecision(RebootAction.SKIP, f"policy={policy}")
        if policy == "when_required" and not reboot_required:
            return RebootDecision(RebootAction.SKIP, "no reboot required")
        if not self.config.allow_if_users_logged_in and self.users_logged_in():
            waited = 0
            while self.users_logged_in() and waited < self.config.user_wait_timeout_s:
                delay = min(30, self.config.user_wait_timeout_s - waited)
                self.sleeper(delay)
                waited += delay
            if self.users_logged_in():
                return RebootDecision(
                    RebootAction.DEFER_MANUAL, "users logged in and policy forbids reboot"
                )
        if not self.window.allows_disruptive(self.now_local(), estimate_s):
            return RebootDecision(
                RebootAction.DEFER_MANUAL, "insufficient maintenance window remaining"
            )
        return RebootDecision(RebootAction.REBOOT)

    def prepare_and_reboot(
        self,
        cycle: CycleState,
        *,
        now_iso: Callable[[], str],
        persist: Callable[[CycleState], None],
        estimate_s: int = 0,
    ) -> CycleState:
        """Persist pre-reboot facts, verify the resume path, then reboot.

        Returns the updated cycle (callers persist it via the engine). Raises
        RebootError if the resume path is missing (FR-S15) or the reboot
        command fails after the retry budget (FR-S7).
        """
        from dataclasses import replace

        while cycle.reboot_attempts < self.max_attempts:
            if cycle.reboot_attempts:
                decision = self.evaluate(
                    reboot_required=cycle.reboot_required, estimate_s=estimate_s
                )
                if decision.action is not RebootAction.REBOOT:
                    raise RebootError(f"reboot retry blocked: {decision.reason}")
            if not self.resume_path_ok():
                raise RebootError(
                    "boot-resume service is not installed/enabled; refusing to reboot "
                    "without a recovery path (FR-S15). Run: d3v-patchcycle install"
                )
            cycle = replace(
                cycle,
                boot_id_before=self.boot_id_reader(),
                boot_id_after=None,
                kernel_before=self.kernel_reader(),
                reboot_initiated_at=cycle.reboot_initiated_at or now_iso(),
                reboot_attempts=cycle.reboot_attempts + 1,
            )
            if not cycle.boot_id_before:
                raise RebootError("cannot establish boot identity; refusing reboot")
            persist(cycle)
            try:
                self.rebooter()
                return cycle
            except (OSError, subprocess.SubprocessError):
                if cycle.reboot_attempts < self.max_attempts:
                    self.sleeper(30)
        raise RebootError("reboot command retry budget exhausted; manual intervention required")

    def verify_rebooted(self, cycle: CycleState) -> str:
        """Confirm a genuine reboot occurred; returns the current boot ID.

        Raises RebootError when the boot ID did not change (FR-S7) or the
        expected kernel is not running (FR-S13).
        """
        current = self.boot_id_reader()
        if not cycle.boot_id_before or not current:
            raise RebootError("missing boot identity: cannot verify reboot")
        if current == cycle.boot_id_before:
            raise RebootError("boot ID unchanged: the requested reboot did not occur (FR-S7)")
        running_kernel = self.kernel_reader()
        if cycle.expected_kernel and running_kernel != cycle.expected_kernel:
            raise RebootError(
                f"kernel mismatch after reboot: expected {cycle.expected_kernel}, "
                f"running {running_kernel} (FR-S13). Bootloader/boot-space issue "
                "likely; PatchCycle does not modify boot configuration."
            )
        return current
