# ADR-0004: Explicit persistent state machine as the architectural core

Status: Accepted — 2026-08-23

## Context

The tool must survive reboots, crashes, and power loss mid-cycle, and must
never repeat or skip maintenance steps incorrectly. Ad-hoc flag files and
"check what happened heuristically" designs are the classic source of
duplicate upgrades, missed reboots, and stranded cycles.

## Decision

The `CycleEngine` drives an explicit state machine (14 states, terminal
COMPLETED/FAILED) whose every transition is persisted atomically **before**
the state's action executes. The full spec is
`docs/specifications/state-machine.md`; invariants are asserted in code and
tested.

## Alternatives considered

- **Implicit state via filesystem probes** (infer position from system
  state): rejected — ambiguous in exactly the failure cases that matter
  (crash during UPGRADING is indistinguishable from "not started" without
  recorded facts).
- **Event sourcing / append-only journal:** strictly more recoverable but
  disproportionate complexity for one cycle at a time; the `transitions`
  array inside the state document plus append-only history captures the audit
  value.
- **SQLite state:** rejected for V1 (minimal-dependency, human-inspectable
  state was prioritised); the schema-version gate keeps migration open.

## Consequences

- Crash recovery is deterministic: the persisted state is authoritative and
  each state's re-entry behaviour is specified (failure-recovery.md).
- One active cycle per host is a structural property.
- Cost: every stage author must think about persistence and idempotency —
  enforced by the engine API (a stage cannot run without a successful state
  write) and by failure-injection tests.
