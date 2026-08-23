"""Tests for the state machine transition table (docs/specifications/state-machine.md)."""

from __future__ import annotations

import pytest

from patchcycle.states import (
    TERMINAL_STATES,
    State,
    is_legal_transition,
    resume_target,
)


class TestTransitionTable:
    def test_happy_path_chain(self):
        chain = [
            (State.IDLE, State.PRECHECK),
            (State.PRECHECK, State.REFRESHING),
            (State.REFRESHING, State.DISCOVERING_UPDATES),
            (State.DISCOVERING_UPDATES, State.UPGRADING),
            (State.UPGRADING, State.CHECKING_REBOOT),
            (State.CHECKING_REBOOT, State.REBOOT_PENDING),
            (State.REBOOT_PENDING, State.REBOOTING),
            (State.REBOOTING, State.POST_REBOOT),
            (State.POST_REBOOT, State.VERIFYING),
            (State.VERIFYING, State.HEALTH_CHECKING),
            (State.HEALTH_CHECKING, State.NOTIFYING),
            (State.NOTIFYING, State.COMPLETED),
        ]
        for src, dst in chain:
            assert is_legal_transition(src, dst), f"{src} -> {dst} must be legal"

    def test_no_updates_skips_upgrading(self):
        assert is_legal_transition(State.DISCOVERING_UPDATES, State.CHECKING_REBOOT)

    def test_reboot_not_required_goes_straight_to_verify(self):
        assert is_legal_transition(State.CHECKING_REBOOT, State.VERIFYING)

    def test_reboot_policy_never_goes_to_verify(self):
        assert is_legal_transition(State.REBOOT_PENDING, State.VERIFYING)

    def test_every_non_terminal_state_can_fail(self):
        # NOTIFYING is the documented exception (state-machine.md §4.11):
        # notification delivery failure must not convert a finished
        # maintenance cycle into a FAILED one.
        for state in State:
            if state not in TERMINAL_STATES and state not in (State.IDLE, State.NOTIFYING):
                assert is_legal_transition(state, State.FAILED), state

    def test_terminal_states_are_terminal(self):
        for terminal in TERMINAL_STATES:
            for other in State:
                assert not is_legal_transition(terminal, other), terminal

    @pytest.mark.parametrize(
        "src,dst",
        [
            (State.IDLE, State.UPGRADING),
            (State.PRECHECK, State.UPGRADING),
            (State.UPGRADING, State.REFRESHING),
            (State.REBOOTING, State.UPGRADING),
            (State.POST_REBOOT, State.REBOOTING),
            (State.NOTIFYING, State.FAILED),
        ],
    )
    def test_illegal_transitions_rejected(self, src, dst):
        assert not is_legal_transition(src, dst)

    def test_health_checking_goes_to_notifying_even_on_failure(self):
        # Health failures are reported, not hidden (state-machine.md §4.10).
        assert is_legal_transition(State.HEALTH_CHECKING, State.NOTIFYING)

    def test_notifying_completes_even_when_delivery_fails(self):
        assert is_legal_transition(State.NOTIFYING, State.COMPLETED)


class TestResumeTargets:
    def test_rebooting_with_new_boot_resumes_post_reboot(self):
        assert resume_target(State.REBOOTING, boot_changed=True) == State.POST_REBOOT

    def test_rebooting_same_boot_is_not_a_resume(self):
        # FR-S7: reboot did not happen — handled by the failure path, not resume.
        assert resume_target(State.REBOOTING, boot_changed=False) is None

    @pytest.mark.parametrize(
        "state",
        [State.POST_REBOOT, State.VERIFYING, State.HEALTH_CHECKING, State.NOTIFYING],
    )
    def test_post_reboot_states_reenter_themselves(self, state):
        assert resume_target(state, boot_changed=True) == state

    def test_early_states_with_changed_boot_are_unexpected_reboot(self):
        # FR-S6: machine rebooted underneath a cycle — not a normal resume.
        assert resume_target(State.UPGRADING, boot_changed=True) is None

    def test_terminal_states_never_resume(self):
        assert resume_target(State.COMPLETED, boot_changed=True) is None
        assert resume_target(State.FAILED, boot_changed=True) is None
