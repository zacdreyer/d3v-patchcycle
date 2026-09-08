"""Maintenance-cycle states and the legal transition table.

Spec: docs/specifications/state-machine.md §2-3. The table here is the single
source of truth; the engine refuses any transition not listed.
"""

from __future__ import annotations

import enum


class State(enum.StrEnum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    REFRESHING = "REFRESHING"
    DISCOVERING_UPDATES = "DISCOVERING_UPDATES"
    UPGRADING = "UPGRADING"
    CHECKING_REBOOT = "CHECKING_REBOOT"
    REBOOT_PENDING = "REBOOT_PENDING"
    REBOOTING = "REBOOTING"
    POST_REBOOT = "POST_REBOOT"
    VERIFYING = "VERIFYING"
    HEALTH_CHECKING = "HEALTH_CHECKING"
    NOTIFYING = "NOTIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_STATES = frozenset({State.COMPLETED, State.FAILED})

#: States the boot-resume service may re-enter after a verified reboot.
_POST_REBOOT_RESUMABLE = frozenset(
    {State.POST_REBOOT, State.VERIFYING, State.HEALTH_CHECKING, State.NOTIFYING}
)

_TRANSITIONS: dict[State, frozenset[State]] = {
    State.IDLE: frozenset({State.PRECHECK}),
    State.PRECHECK: frozenset({State.REFRESHING, State.REBOOT_PENDING, State.FAILED}),
    # Recovery arcs to PRECHECK (FR-S1/S2): a crashed early-stage cycle
    # restarts cleanly at PRECHECK with the same run_id; PRECHECK re-runs
    # provider preflight, which catches interrupted package transactions
    # before any upgrade is re-entered.
    State.REFRESHING: frozenset({State.DISCOVERING_UPDATES, State.PRECHECK, State.FAILED}),
    State.DISCOVERING_UPDATES: frozenset(
        {State.UPGRADING, State.CHECKING_REBOOT, State.PRECHECK, State.FAILED}
    ),
    State.UPGRADING: frozenset({State.CHECKING_REBOOT, State.PRECHECK, State.FAILED}),
    State.CHECKING_REBOOT: frozenset({State.REBOOT_PENDING, State.VERIFYING, State.FAILED}),
    State.REBOOT_PENDING: frozenset({State.REBOOTING, State.VERIFYING, State.FAILED}),
    # REBOOTING -> POST_REBOOT is only ever crossed by the resume service
    # after a verified boot-id change (ADR-0008).
    State.REBOOTING: frozenset({State.POST_REBOOT, State.FAILED}),
    State.POST_REBOOT: frozenset({State.VERIFYING, State.PRECHECK, State.FAILED}),
    State.VERIFYING: frozenset({State.HEALTH_CHECKING, State.FAILED}),
    # Health failures are reported, not hidden: always proceed to notify.
    State.HEALTH_CHECKING: frozenset({State.NOTIFYING, State.FAILED}),
    # Delivery failure never rewrites maintenance truth (state-machine.md
    # §4.11): the only arc out of NOTIFYING is COMPLETED; delivery failures
    # are recorded in notification_status, not as a FAILED cycle.
    State.NOTIFYING: frozenset({State.COMPLETED}),
    State.COMPLETED: frozenset(),
    State.FAILED: frozenset(),
}


def is_legal_transition(src: State, dst: State) -> bool:
    """True iff the transition table permits src -> dst."""
    return dst in _TRANSITIONS[src]


def resume_target(persisted: State, *, boot_changed: bool) -> State | None:
    """Resolve where a boot-resume should continue, or None for failure paths.

    - REBOOTING + new boot           -> POST_REBOOT (ADR-0008)
    - post-reboot states + new boot  -> re-enter themselves (idempotent)
    - REBOOTING + same boot          -> None (FR-S7: reboot never happened)
    - early states + changed boot    -> None (FR-S6: unexpected reboot)
    - terminal states                -> None (never resumed)
    """
    if persisted in TERMINAL_STATES or persisted is State.IDLE:
        return None
    if persisted is State.REBOOTING:
        return State.POST_REBOOT if boot_changed else None
    if boot_changed and persisted in _POST_REBOOT_RESUMABLE:
        return persisted
    return None
