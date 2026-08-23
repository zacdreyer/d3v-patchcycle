# ADR-0005: Provider/scheduler/notifier registry abstractions

Status: Accepted — 2026-08-23

## Context

OS-specific behaviour must be isolated; the core engine must contain no
APT/DNF/macOS logic. Breadth must never be claimed without tested depth.

## Decision

Three registry abstractions with constructor-injected implementations:

- `UpdateProvider` (contract: `docs/specifications/provider-contract.md`)
- `SchedulerBackend` (systemd V1; launchd future)
- `Notifier` (smtp, webhook V1)

Selection is an **explicit mapping table** from os-release `ID`/`ID_LIKE`.
A provider is registered only when its integration tests pass on the claimed
platforms ("support honesty rule").

## Alternatives considered

- **Plugin/entry-point auto-discovery:** rejected — import-time side effects
  and third-party plugin trust are out of scope for a root-running tool;
  an in-tree explicit registry is auditable.
- **Per-OS subclasses of a base engine:** rejected — inheritance would leak
  OS specifics into the engine; composition keeps the engine OS-agnostic.

## Consequences

- Unsupported OS → deterministic, tested failure message; detection never
  implies support.
- New platforms are additive work with a defined gate (tests + docs),
  protecting V1 quality.
- The engine is fully unit-testable against in-memory fake providers.
