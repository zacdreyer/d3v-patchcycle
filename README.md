# d3v-patchcycle

D3V PatchCycle is a lightweight, stateful server maintenance utility that automates package updates, safely handles required reboots, resumes maintenance after restart, verifies system health, and reports the final result.

> A failed maintenance cycle that clearly reports the problem is preferable to
> an automated maintenance cycle that guesses, forces, hides or silently
> repairs its way past an unsafe condition.

## Status

Specification phase complete (Phase 0 research + Phase 1 SDD). Implementation
proceeds in TDD phases per the
[implementation plan](docs/development/implementation-plan.md).

## Documentation

- [Documentation index](docs/README.md) — full SDD structure
- [Product specification](docs/requirements/product-specification.md)
- [System architecture](docs/architecture/system-architecture.md)
- [State machine specification](docs/specifications/state-machine.md)
- [Provider contract](docs/specifications/provider-contract.md)
- [Configuration specification](docs/specifications/configuration.md)
- [Failure & recovery specification](docs/specifications/failure-recovery.md)
- [Security threat model](docs/security/threat-model.md)
- [Test strategy & TDD workflow](docs/testing/test-strategy.md)
- [Operations guide](docs/operations/operations.md)
- [Architecture decision records](docs/decisions/README.md)
- [Research findings](docs/research/phase-0-research.md)
- [Agent memory](agent-memory.md) — token-efficient cold-start summary

## Supported platforms (V1)

Ubuntu Server 22.04/24.04 LTS and Debian 12/13 (APT, systemd). Detection of
other operating systems fails safely with a clear message — support is only
claimed for providers with passing integration tests.
