"""CycleEngine: drives the persistent maintenance state machine.

Spec: docs/specifications/state-machine.md. Rules enforced here:

- a state transition is persisted atomically BEFORE the state's action runs
  (state-machine.md principle 1, invariant 4);
- transitions not in the table are refused (invariant via states.py);
- completed work is never repeated on resume (invariant 3, guarded by
  ``updates_applied``);
- terminal cycles are archived to history before state resets to IDLE
  (invariant 5).
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from patchcycle.config import Config
from patchcycle.errors import (
    PackageManagerError,
    PackageManagerLockedError,
    PatchCycleError,
    PreflightError,
    RebootError,
    StateError,
    VerificationError,
)
from patchcycle.logging_setup import collect_config_secrets, redact_text
from patchcycle.models import (
    ErrorInfo,
    HealthResult,
    OsIdentity,
    Outcome,
    PreflightResult,
    ReportData,
    UpdateInfo,
)
from patchcycle.notify.base import Notifier
from patchcycle.providers.base import UpdateProvider
from patchcycle.reboot import RebootAction, RebootController
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State, is_legal_transition, resume_target
from patchcycle.window import Window


class HookRunnerLike(Protocol):
    def run_all(self, hooks: tuple[tuple[str, ...], ...], point: str) -> list[Any]: ...
    def should_abort(self, results: list[Any], point: str) -> bool: ...


class HealthRunnerLike(Protocol):
    def run(self, checks: Any) -> list[HealthResult]: ...


@dataclass
class CycleEngine:
    store: StateStore
    provider: UpdateProvider
    config: Config
    hooks: HookRunnerLike
    health: HealthRunnerLike
    notifiers: list[Notifier]
    reboot: RebootController
    hostname: str
    is_root: Callable[[], bool]
    now_iso: Callable[[], str]
    now_local: Callable[[], datetime]
    sleeper: Callable[[float], None]
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger]

    # ------------------------------------------------------------------ run

    def run(self, *, scheduled: bool = False, force: bool = False) -> int:
        """Start or recover a maintenance cycle. Returns the process exit code."""
        if not self.config.maintenance.enabled and not force:
            if scheduled:
                self.logger.info("maintenance disabled in config; scheduled run skipped")
                return 0
            self.logger.error("maintenance disabled in config; use --force for a manual run")
            return 2
        try:
            existing = self.store.load()
        except StateError as exc:
            self.logger.error("state load failed: %s", exc)
            return exc.exit_code

        if existing is not None and existing.state not in (State.COMPLETED, State.FAILED):
            return self._recover_same_boot(existing)

        if existing is not None:
            self.store.archive(existing)

        return self._drive(self._new_cycle())

    def resume(self) -> int:
        """Boot-time continuation. No-op when no cycle is pending."""
        try:
            cycle = self.store.load()
        except StateError as exc:
            self.logger.error("state load failed: %s", exc)
            return exc.exit_code
        if cycle is None:
            self.logger.info("no maintenance cycle pending; nothing to resume")
            return 0
        if cycle.state in (State.COMPLETED, State.FAILED):
            self.store.archive(cycle)
            return 0
        if self._is_stale(cycle):
            return self._diagnose_and_fail(cycle, "stale-cycle")

        if cycle.boot_id_after:
            return self._recover_same_boot(cycle)

        target = resume_target(cycle.state, boot_changed=self._boot_changed(cycle))
        if target is None:
            if cycle.state is State.REBOOTING:
                return self._reboot_never_happened(cycle)
            return self._diagnose_and_fail(cycle, "unexpected-reboot")
        return self._drive(cycle, start_at=target)

    # ------------------------------------------------------------- dry run

    def dry_run(self) -> dict[str, Any]:
        """Read-only plan (FR-18): no state writes, no apply, no reboot."""
        from patchcycle.hooks import classify_hooks

        pre = self.provider.preflight()
        if not pre.ok:
            raise PreflightError(pre.detail or pre.kind, kind=pre.kind or None)
        refresh = self.provider.refresh()
        if not refresh.ok:
            raise PackageManagerError(refresh.detail or "index refresh failed")
        updates = self.provider.list_updates(self.config.updates.strategy)
        reboot = self.provider.reboot_required()
        pending = sum(not update.held for update in updates)
        estimates = self.config.maintenance.estimates
        estimated_duration = estimates.refresh_s
        if pending:
            estimated_duration += max(estimates.upgrade_min_s, estimates.per_package_s * pending)
        if reboot.required or self.config.reboot.policy == "always":
            estimated_duration += estimates.reboot_verify_s
        return {
            "provider": self.provider.name,
            "os": self.provider.os_identity.pretty_name,
            "preflight_ok": pre.ok,
            "preflight_detail": pre.detail,
            "refresh_ok": refresh.ok,
            "updates": [u.__dict__ for u in updates],
            "packages_pending": len([u for u in updates if not u.held]),
            "estimated_duration_s": estimated_duration,
            "reboot_required": reboot.required,
            "reboot_reasons": list(reboot.reasons),
            "strategy": self.config.updates.strategy,
            "reboot_policy": self.config.reboot.policy,
            "health_checks": [
                {"kind": c.kind, "name": c.name, "critical": c.critical}
                for c in self.config.health_checks
            ],
            "hooks": classify_hooks(),
            "notifications": {
                "email": self.config.notifications.email.enabled,
                "webhook": self.config.notifications.webhook.enabled,
            },
        }

    # -------------------------------------------------------------- engine

    def _new_cycle(self) -> CycleState:
        cycle = CycleState(
            run_id=str(uuid.uuid4()),
            state=State.IDLE,
            hostname=self.hostname,
            started_at=self.now_iso(),
            provider=self.provider.name,
        )
        return cycle

    def _drive(self, cycle: CycleState, start_at: State | None = None) -> int:
        self._log_context(cycle)
        state = start_at or (State.PRECHECK if cycle.state is State.IDLE else cycle.state)
        if cycle.state is State.IDLE or state != cycle.state:
            cycle = self._transition(cycle, state)
        try:
            while cycle.state not in (State.COMPLETED, State.FAILED):
                previous_state = cycle.state
                self._log_context(cycle)
                cycle = self._step(cycle)
                if previous_state is State.REBOOTING and cycle.state is State.REBOOTING:
                    # systemctl queues shutdown asynchronously. Leave the durable
                    # cycle pending; only a subsequent boot may verify it.
                    return 0
        except PatchCycleError as exc:
            return self._fail(cycle, exc, stage=cycle.state.value)
        except Exception as exc:  # defensive: unexpected internal error
            self.logger.exception("internal error in state %s", cycle.state)
            return self._fail(
                cycle,
                PatchCycleError(f"internal error: {exc}"),
                stage=cycle.state.value,
                kind="internal",
            )
        # Entered COMPLETED: finalize outcome and archive (state-machine §4.12).
        outcome = self._final_outcome(cycle)
        cycle = replace(cycle, outcome=outcome.value, updated_at=self.now_iso())
        self.store.save(cycle)
        self.store.archive(cycle)
        self.logger.info("cycle %s completed: %s", cycle.run_id, outcome.value)
        return self._exit_code(cycle)

    def _step(self, cycle: CycleState) -> CycleState:
        handler = _HANDLERS[cycle.state]
        return handler(self, cycle)

    # ----------------------------------------------------------- handlers

    def _do_precheck(self, cycle: CycleState) -> CycleState:
        if not self.is_root():
            raise PreflightError("PatchCycle must run as root")
        self._check_disk_space()
        os_id: OsIdentity = self.provider.os_identity
        cycle = replace(
            cycle,
            os={
                "id": os_id.os_id,
                "version_id": os_id.version_id,
                "pretty_name": os_id.pretty_name,
                "codename": os_id.codename,
                "arch": os_id.arch,
                "init": os_id.init,
            },
            kernel_before=os_id.kernel,
            # Boot identity is captured at cycle start so an unexpected reboot
            # mid-cycle is detectable (FR-S6); REBOOTING re-records it.
            boot_id_before=self.reboot.boot_id_reader(),
        )
        pre = self._preflight_with_lock_wait()
        if (
            not pre.ok
            and pre.kind == "interrupted-transaction"
            and self.config.updates.repair_interrupted
        ):
            if not self._window().allows_disruptive(
                self.now_local(), self._upgrade_estimate(cycle)
            ):
                raise PreflightError(
                    "insufficient maintenance window for interrupted-transaction repair"
                )
            pre = self.provider.repair_interrupted()
        cycle = replace(cycle, pre_existing_reboot=pre.pre_existing_reboot)
        if not pre.ok:
            if pre.kind == "pm-locked":
                raise PackageManagerLockedError(pre.detail or "package manager locked")
            raise PreflightError(pre.detail or pre.kind, kind=pre.kind or None)
        if (
            pre.pre_existing_reboot
            and not cycle.initial_reboot_completed
            and self.config.reboot.existing_pending == "reboot_first"
        ):
            cycle = replace(cycle, reboot_before_updates=True, reboot_required=True)
            return self._transition(cycle, State.REBOOT_PENDING)
        return self._transition(cycle, State.REFRESHING)

    @staticmethod
    def _check_disk_space() -> None:
        targets = [("/", 512 * 1024 * 1024)]
        if os.path.ismount("/boot"):
            targets.append(("/boot", 128 * 1024 * 1024))
        for path, required in targets:
            if shutil.disk_usage(path).free < required:
                raise PreflightError(
                    f"insufficient free disk space on {path}: need {required} bytes",
                    kind="disk-space",
                )

    def _preflight_with_lock_wait(self) -> PreflightResult:
        deadline = self.config.package_manager.lock_timeout_s
        waited = 0.0
        while True:
            result = self.provider.preflight()
            if result.ok or result.kind != "pm-locked":
                return result
            if waited >= deadline:
                return result
            interval = float(self.config.package_manager.lock_poll_interval_s)
            self.logger.info("package manager locked; waiting %ss", interval)
            self.sleeper(interval)
            waited += interval

    def _do_refreshing(self, cycle: CycleState) -> CycleState:
        result = self.provider.refresh()
        if not result.ok:
            raise PackageManagerError(f"index refresh failed: {result.detail}")
        for warning in result.warnings:
            self.logger.warning("refresh warning: %s", warning)
        cycle = replace(
            cycle,
            warnings=tuple(
                dict.fromkeys(
                    (*cycle.warnings, *(self._redact(warning) for warning in result.warnings))
                )
            ),
        )
        return self._transition(cycle, State.DISCOVERING_UPDATES)

    def _do_discovering(self, cycle: CycleState) -> CycleState:
        updates = self.provider.list_updates(self.config.updates.strategy)
        applicable = [u for u in updates if not u.held]
        held = [u for u in updates if u.held]
        cycle = replace(
            cycle,
            updates_available=tuple(u.__dict__ for u in updates),
            packages_pending=len(applicable),
        )
        self.logger.info("updates: %d applicable, %d held-back", len(applicable), len(held))
        if held:
            self.logger.info("held back: %s", ", ".join(u.name for u in held))
        if not applicable:
            return self._transition(cycle, State.CHECKING_REBOOT)
        return self._transition(cycle, State.UPGRADING)

    def _do_upgrading(self, cycle: CycleState) -> CycleState:
        if cycle.updates_applied:
            # Invariant 3: completed work is never repeated.
            return self._transition(cycle, State.CHECKING_REBOOT)
        estimate = self._upgrade_estimate(cycle)
        if not self._window().allows_disruptive(self.now_local(), estimate):
            raise PreflightError(
                f"insufficient maintenance window remaining for upgrade "
                f"(estimated {estimate}s); cycle deferred to next window"
            )
        self._run_hooks(cycle, "before_upgrade", self.config.hooks.before_upgrade)
        pre = self._preflight_with_lock_wait()
        if not pre.ok:
            if pre.kind == "pm-locked":
                raise PackageManagerLockedError(pre.detail)
            raise PreflightError(pre.detail or pre.kind, kind=pre.kind or None)
        if not self._window().allows_disruptive(self.now_local(), estimate):
            raise PreflightError("maintenance window expired while preparing the upgrade")
        result = self.provider.apply_updates(
            self.config.updates.strategy,
            [
                self._update_from_dict(u)
                for u in cycle.updates_available
                if not u.get("held", False)
            ],
        )
        if not result.ok:
            raise PackageManagerError(f"package manager failure: {result.detail}")
        cycle = replace(
            cycle,
            updates_applied=True,
            packages_updated=result.packages_updated,
            kernel_update=result.kernel_update,
            expected_kernel=result.expected_kernel,
        )
        self.store.save(cycle)
        self._run_hooks(cycle, "after_upgrade", self.config.hooks.after_upgrade)
        return self._transition(cycle, State.CHECKING_REBOOT)

    def _do_checking_reboot(self, cycle: CycleState) -> CycleState:
        status = self.provider.reboot_required()
        cycle = replace(
            cycle,
            reboot_required=status.required,
            reboot_reasons=status.reasons,
        )
        if cycle.pre_existing_reboot and self.config.reboot.existing_pending == "report_only":
            # Downgrades reboot behaviour only (state-machine.md §4.5).
            cycle = replace(cycle, outcome=Outcome.MANUAL_REBOOT_REQUIRED.value)
            return self._transition(cycle, State.VERIFYING)
        if status.required or self.config.reboot.policy == "always":
            return self._transition(cycle, State.REBOOT_PENDING)
        return self._transition(cycle, State.VERIFYING)

    def _do_reboot_pending(self, cycle: CycleState) -> CycleState:
        decision = self.reboot.evaluate(
            reboot_required=cycle.reboot_required,
            estimate_s=self.config.maintenance.estimates.reboot_verify_s,
        )
        if decision.action is RebootAction.REBOOT:
            return self._transition(cycle, State.REBOOTING)
        if decision.action is RebootAction.DEFER_MANUAL:
            if "window" in decision.reason or "users" in decision.reason:
                raise PreflightError(f"reboot blocked: {decision.reason}")
            cycle = replace(cycle, outcome=Outcome.MANUAL_REBOOT_REQUIRED.value)
            return self._transition(cycle, State.VERIFYING)
        return self._transition(cycle, State.VERIFYING)

    def _do_rebooting(self, cycle: CycleState) -> CycleState:
        self._run_hooks(cycle, "before_reboot", self.config.hooks.before_reboot)
        decision = self.reboot.evaluate(
            reboot_required=cycle.reboot_required,
            estimate_s=self.config.maintenance.estimates.reboot_verify_s,
        )
        if decision.action is not RebootAction.REBOOT:
            raise PreflightError(f"reboot blocked before command: {decision.reason}")
        cycle = self.reboot.prepare_and_reboot(
            cycle,
            now_iso=self.now_iso,
            persist=self.store.save,
            estimate_s=self.config.maintenance.estimates.reboot_verify_s,
        )
        # Production: the machine goes down here and the resume service
        # continues. Tests/simulation: rebooter returns after flipping the
        # boot id, and we continue in-process through the same code path.
        if not self._boot_changed(cycle):
            return cycle
        return self._transition(cycle, State.POST_REBOOT)

    def _do_post_reboot(self, cycle: CycleState) -> CycleState:
        boot_after = self.reboot.verify_rebooted(cycle)
        cycle = replace(cycle, kernel_after=self.reboot.kernel_reader(), boot_id_after=boot_after)
        self.store.save(cycle)
        self._run_hooks(cycle, "after_reboot", self.config.hooks.after_reboot)
        if cycle.reboot_before_updates:
            cycle = replace(
                cycle,
                reboot_before_updates=False,
                initial_reboot_completed=True,
                boot_id_before=self.reboot.boot_id_reader(),
                boot_id_after=None,
                reboot_attempts=0,
                reboot_initiated_at=None,
            )
            return self._transition(cycle, State.PRECHECK)
        return self._transition(cycle, State.VERIFYING)

    def _do_verifying(self, cycle: CycleState) -> CycleState:
        result = self.provider.verify()
        cycle = replace(
            cycle,
            verify_passed=result.consistent and result.outstanding == 0,
            outstanding_updates=result.outstanding,
        )
        self.store.save(cycle)
        if not result.consistent:
            raise VerificationError(f"package state inconsistent: {result.detail}")
        if result.outstanding:
            raise VerificationError(f"{result.outstanding} applicable updates remain")
        return self._transition(cycle, State.HEALTH_CHECKING)

    def _do_health_checking(self, cycle: CycleState) -> CycleState:
        results = [
            replace(result, detail=self._redact(result.detail), name=self._redact(result.name))
            for result in self.health.run(self.config.health_checks)
        ]
        cycle = replace(
            cycle,
            health_results=tuple(r.__dict__ for r in results),
        )
        critical_failures = [r for r in results if r.critical and not r.ok]
        if critical_failures:
            cycle = replace(
                cycle,
                error=ErrorInfo(
                    kind="health-check-failed",
                    message="critical health checks failed: "
                    + ", ".join(r.name for r in critical_failures),
                    stage=State.HEALTH_CHECKING.value,
                    manual_intervention=True,
                ).__dict__,
            )
        return self._transition(cycle, State.NOTIFYING)

    def _do_notifying(self, cycle: CycleState) -> CycleState:
        cycle = replace(cycle, outcome=self._final_outcome(cycle).value)
        self.store.save(cycle)
        report = self._build_report(cycle)
        body = render_report(report)
        self.store.save_report(cycle.run_id, body)
        status: dict[str, str] = dict(cycle.notification_status)
        for notifier in self.notifiers:
            if status.get(notifier.name) == "sent":
                continue
            status[notifier.name] = self._deliver_with_retries(notifier, report, body)
            cycle = replace(cycle, notification_status=dict(status))
            self.store.save(cycle)
        cycle = replace(cycle, notification_status=status)
        self.store.save_report(cycle.run_id, render_report(self._build_report(cycle)))
        return self._transition(cycle, State.COMPLETED)

    # ----------------------------------------------------------- helpers

    def _redact(self, value: str) -> str:
        return redact_text(value, collect_config_secrets(self.config))

    def _log_context(self, cycle: CycleState) -> None:
        base = self.logger.logger if isinstance(self.logger, logging.LoggerAdapter) else self.logger
        self.logger = logging.LoggerAdapter(
            base, {"run_id": cycle.run_id, "state": cycle.state.value}
        )

    def _transition(self, cycle: CycleState, dst: State) -> CycleState:
        self._log_context(cycle)
        if not is_legal_transition(cycle.state, dst):
            raise StateError(f"illegal transition {cycle.state} -> {dst}")
        self.logger.info("state %s -> %s", cycle.state.value, dst.value)
        cycle = cycle.record_transition(cycle.state, dst, at=self.now_iso())
        cycle = replace(cycle, state=dst, updated_at=self.now_iso())
        self.store.save(cycle)
        return cycle

    def _fail(
        self,
        cycle: CycleState,
        exc: PatchCycleError,
        *,
        stage: str,
        kind: str | None = None,
        outcome: Outcome | None = None,
    ) -> int:
        error = ErrorInfo(
            kind=kind or exc.error_kind,
            message=self._redact(str(exc)),
            stage=stage,
            manual_intervention=exc.manual_intervention,
        )
        self.logger.error("cycle failed at %s: %s", stage, error.message)
        # A handler may have persisted completed work before a later action
        # failed. Preserve those facts rather than archiving its stale input.
        saved = self.store.load()
        if saved is not None and saved.run_id == cycle.run_id:
            cycle = saved
        if is_legal_transition(cycle.state, State.FAILED):
            cycle = self._transition(cycle, State.FAILED)
        else:
            cycle = replace(cycle, state=State.FAILED)
            self.store.save(cycle)
        final_outcome = outcome or (
            Outcome.BLOCKED if exc.exit_code in (2, 4, 5) else Outcome.FAILED
        )
        cycle = replace(cycle, error=error.__dict__, outcome=final_outcome.value)
        self.store.save(cycle)
        try:
            self._run_hooks(cycle, "on_failure", self.config.hooks.on_failure)
        except Exception:
            self.logger.error("on_failure hook failed; preserving original cycle failure")
        cycle = self._notify_best_effort(cycle)
        self.store.archive(cycle)
        return exc.exit_code

    def _recover_same_boot(self, cycle: CycleState) -> int:
        """Crash recovery for a non-terminal cycle found by run() (FR-S1/S2)."""
        if self._is_stale(cycle):
            return self._diagnose_and_fail(cycle, "stale-cycle")
        if self._boot_changed(cycle):
            if cycle.state in (State.REBOOTING, State.POST_REBOOT) and not cycle.boot_id_after:
                return self._drive(cycle, start_at=State.POST_REBOOT)
            return self._diagnose_and_fail(cycle, "unexpected-reboot")
        if cycle.state is State.REBOOTING:
            return self._reboot_never_happened(cycle)
        if cycle.state in (
            State.CHECKING_REBOOT,
            State.REBOOT_PENDING,
            State.POST_REBOOT,
            State.VERIFYING,
            State.HEALTH_CHECKING,
            State.NOTIFYING,
        ):
            # Same boot: repeat policy checks or post-upgrade verification
            # without restarting the completed package transaction.
            return self._drive(cycle)
        # FR-S1/S2: early states restart cleanly at PRECHECK with the same
        # run_id. PRECHECK re-runs the provider preflight, which catches an
        # interrupted dpkg transaction (FR-S2) before any upgrade is
        # re-entered; apt operations are idempotent, so re-running early
        # stages converges safely.
        self.logger.warning(
            "recovering interrupted cycle %s from state %s", cycle.run_id, cycle.state
        )
        return self._drive(cycle, start_at=State.PRECHECK)

    def _is_stale(self, cycle: CycleState) -> bool:
        timestamp = cycle.updated_at or cycle.started_at
        if not timestamp:
            return False
        try:
            elapsed = datetime.fromisoformat(self.now_iso()) - datetime.fromisoformat(timestamp)
            return elapsed.total_seconds() > 7 * 86400
        except (TypeError, ValueError):
            return True

    def _diagnose_and_fail(self, cycle: CycleState, kind: str) -> int:
        details = []
        for diagnostic in (self.provider.preflight, self.provider.verify):
            try:
                details.append(str(diagnostic()))
            except Exception as exc:
                details.append(f"diagnostic failed: {type(exc).__name__}")
        return self._fail(
            cycle,
            PatchCycleError(
                f"{kind} during {cycle.state}; read-only diagnostics: {'; '.join(details)}"
            ),
            stage=cycle.state.value,
            kind=kind,
        )

    def _reboot_never_happened(self, cycle: CycleState) -> int:
        try:
            elapsed = (
                datetime.fromisoformat(self.now_iso())
                - datetime.fromisoformat(cycle.reboot_initiated_at or self.now_iso())
            ).total_seconds()
        except (TypeError, ValueError):
            elapsed = 600
        if cycle.reboot_attempts < self.reboot.max_attempts and elapsed < 600:
            self.logger.warning(
                "reboot attempt %d did not take effect; retrying", cycle.reboot_attempts
            )
            self.sleeper(max(0, 30 - elapsed))
            return self._drive(cycle, start_at=State.REBOOTING)
        return self._fail(
            cycle,
            RebootError(
                f"reboot command issued {cycle.reboot_attempts}x but the machine "
                "never rebooted (FR-S7); manual reboot required"
            ),
            stage=State.REBOOTING.value,
        )

    def _boot_changed(self, cycle: CycleState) -> bool:
        # boot_id_before is recorded at cycle start (PRECHECK) and re-recorded
        # before rebooting; a mismatch means the machine rebooted outside the
        # expected window (FR-S6) or the ordered reboot happened (ADR-0008).
        baseline = cycle.boot_id_after or cycle.boot_id_before
        if baseline is None:
            return False
        return self.reboot.boot_id_reader() != baseline

    def _window(self) -> Window:
        return Window(self.config.maintenance.window_start, self.config.maintenance.window_end)

    def _upgrade_estimate(self, cycle: CycleState) -> int:
        est = self.config.maintenance.estimates
        return max(est.upgrade_min_s, est.per_package_s * max(cycle.packages_pending, 1))

    def _run_hooks(self, cycle: CycleState, point: str, hooks: tuple[tuple[str, ...], ...]) -> None:
        if not hooks:
            return
        results = self.hooks.run_all(hooks, point)
        for result in results:
            log = self.logger.info if result.ok else self.logger.error
            log("hook %s %s: exit=%s", point, result.hook, result.exit_code)
        if self.hooks.should_abort(results, point):
            raise PreflightError(f"hook at {point} failed with failure_policy=abort")

    def _deliver_with_retries(self, notifier: Notifier, report: ReportData, body: str) -> str:
        for attempt, delay in enumerate((5.0, 30.0, 120.0), start=1):
            try:
                result = notifier.deliver(report, body)
            except Exception as exc:
                result = f"failed:{type(exc).__name__}"
            if result == "sent":
                return result
            self.logger.warning("notifier %s attempt %d failed: %s", notifier.name, attempt, result)
            if attempt < 3:
                self.sleeper(delay)
        return result

    def _notify_best_effort(self, cycle: CycleState) -> CycleState:
        """Notify on failure; never raises. Returns the updated cycle."""
        report = self._build_report(cycle)
        body = render_report(report)
        self.store.save_report(cycle.run_id, body)
        status: dict[str, str] = dict(cycle.notification_status)
        for notifier in self.notifiers:
            try:
                status[notifier.name] = self._deliver_with_retries(notifier, report, body)
            except Exception as exc:  # notification must never crash a failure path
                self.logger.error("notifier %s raised: %s", notifier.name, type(exc).__name__)
                status[notifier.name] = f"failed:{type(exc).__name__}"
        cycle = replace(cycle, notification_status=status)
        self.store.save(cycle)
        self.store.save_report(cycle.run_id, render_report(self._build_report(cycle)))
        return cycle

    def _build_report(self, cycle: CycleState) -> ReportData:
        health = tuple(
            HealthResult(**r) if isinstance(r, dict) else r for r in cycle.health_results
        )
        error = ErrorInfo(**cycle.error) if cycle.error else None
        return ReportData(
            run_id=cycle.run_id,
            hostname=cycle.hostname,
            os_pretty_name=cycle.os.get("pretty_name", ""),
            outcome=Outcome(cycle.outcome) if cycle.outcome else Outcome.FAILED,
            packages_available=cycle.packages_pending,
            packages_upgraded=cycle.packages_updated,
            packages_held=sum(bool(u.get("held", False)) for u in cycle.updates_available),
            kernel_update=cycle.kernel_update,
            reboot_required=cycle.reboot_required,
            reboot_completed=cycle.kernel_after is not None,
            kernel_before=cycle.kernel_before,
            kernel_after=cycle.kernel_after or "",
            outstanding_updates=cycle.outstanding_updates,
            health_results=health,
            failed_services=sum(1 for r in health if not r.ok and r.kind == "service"),
            error=error,
            notification_status=cycle.notification_status,
            warnings=cycle.warnings,
        )

    def _final_outcome(self, cycle: CycleState) -> Outcome:
        if cycle.error and cycle.error.get("kind") == "health-check-failed":
            return Outcome.FAILED
        if cycle.outcome == Outcome.MANUAL_REBOOT_REQUIRED.value:
            return Outcome.MANUAL_REBOOT_REQUIRED
        if cycle.warnings or any(not r.get("ok", False) for r in cycle.health_results):
            return Outcome.SUCCESS_WITH_WARNINGS
        if not cycle.updates_applied and cycle.packages_pending == 0:
            return Outcome.NO_UPDATES
        return Outcome.SUCCESS

    @staticmethod
    def _exit_code(cycle: CycleState) -> int:
        outcome = cycle.outcome
        if outcome == Outcome.MANUAL_REBOOT_REQUIRED.value:
            return 6
        if outcome == Outcome.FAILED.value:
            return 1
        if outcome == Outcome.BLOCKED.value:
            return 2
        return 0

    @staticmethod
    def _update_from_dict(data: dict[str, Any]) -> UpdateInfo:
        from patchcycle.models import UpdateInfo as _UpdateInfo

        return _UpdateInfo(**data)


def render_report(report: ReportData) -> str:
    """Re-export of the canonical renderer (report.py)."""
    from patchcycle.report import render_report as _render

    return _render(report)


_HANDLERS: dict[State, Callable[[CycleEngine, CycleState], CycleState]] = {
    State.PRECHECK: CycleEngine._do_precheck,
    State.REFRESHING: CycleEngine._do_refreshing,
    State.DISCOVERING_UPDATES: CycleEngine._do_discovering,
    State.UPGRADING: CycleEngine._do_upgrading,
    State.CHECKING_REBOOT: CycleEngine._do_checking_reboot,
    State.REBOOT_PENDING: CycleEngine._do_reboot_pending,
    State.REBOOTING: CycleEngine._do_rebooting,
    State.POST_REBOOT: CycleEngine._do_post_reboot,
    State.VERIFYING: CycleEngine._do_verifying,
    State.HEALTH_CHECKING: CycleEngine._do_health_checking,
    State.NOTIFYING: CycleEngine._do_notifying,
}
