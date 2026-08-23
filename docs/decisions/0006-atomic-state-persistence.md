# ADR-0006: Atomic, schema-versioned JSON state persistence

Status: Accepted — 2026-08-23

## Context

State must survive power loss at any instant and never be half-written.
Corruption must be detected, never silently "healed".

## Decision

- Format: single JSON document, `schema_version: 1` (state-machine.md §6).
- Write protocol: temp file in same directory (0600, `O_NOFOLLOW` checks) →
  `fsync(file)` → `os.replace` → `fsync(dir)`.
- Read protocol: ownership/mode/symlink checks; parse failure or schema
  violation → quarantine (`*.corrupt.<ts>`) and refuse maintenance (FR-S8).
- Completed cycles archive to `history/<run_id>.json`; `state.json` returns
  to IDLE.
- Unknown newer `schema_version` → refuse to mutate (no silent downgrade).

## Alternatives considered

- **SQLite:** crash-safe by design but adds an inspectability cost and a
  binary format for a single small document; JSON + rename gives equivalent
  atomicity for this workload.
- **Multiple flag files:** rejected — non-atomic multi-file state is exactly
  the inconsistency this ADR eliminates.

## Consequences

- A crash can lose at most the in-flight transition, never corrupt prior
  state; fsync ordering makes power loss safe on journaling filesystems.
- Human-inspectable state aids the operations runbook.
- Migrations are explicit: `schema_version` bump + migration function + tests
  (planned, none exist at v1).
