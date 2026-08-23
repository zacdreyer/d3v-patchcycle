# d3v-patchcycle

D3V PatchCycle is a lightweight, stateful server maintenance utility that automates package updates, safely handles required reboots, resumes maintenance after restart, verifies system health, and reports the final result.

> A failed maintenance cycle that clearly reports the problem is preferable to
> an automated maintenance cycle that guesses, forces, hides or silently
> repairs its way past an unsafe condition.

## ⚠️ Under active development

**D3V PatchCycle is pre-release software (v1.0.0-rc1) under active
development.** The core is feature-complete and fully tested, but it has not
yet cut a stable release. Do not deploy to production systems yet — the V1.0
release gate is a green nightly [L4 VM reboot run](.github/workflows/vm-reboot.yml)
that performs a real reboot and verifies resume end-to-end.

## Development roadmap

Built with Spec-Driven Development (SDD) and strict Test-Driven Development
(TDD: RED → GREEN → REFACTOR → sync docs). Current phase status:

| Phase | Scope | Status |
|---|---|---|
| **0** | Research — every native mechanism verified against official docs | ✅ Complete |
| **1** | Specification — full SDD doc set + 9 ADRs | ✅ Complete |
| **2** | Core framework — config, OS detection, state machine, locking, CLI | ✅ Complete |
| **3** | APT provider — Ubuntu/Debian updates, reboot detection, recovery | ✅ Complete |
| **4** | Scheduling, reboot & resume — systemd installer, L4 VM harness | ✅ Complete |
| **5** | Notifications — SMTP + webhook, delivery-failure semantics | ✅ Complete |
| **6** | Health checks & hooks — dry-run classification, policy matrix | ✅ Complete |
| **7** | Hardening — security sweep (T1–T14), fuzz/perf passes, coverage | ✅ Complete |
| **8** | Additional providers — DNF (RHEL/Rocky/Alma/Fedora) → Zypper → APK → Pacman → macOS | ⏳ Post-V1 |

**Current:** `1.0.0-rc1` — 380+ tests, ~92% branch coverage, mypy `--strict`
clean, ruff clean, zero runtime dependencies (Python 3.11+ stdlib only).
Details: [implementation plan](docs/development/implementation-plan.md).

## What it does

1. Detects the host OS (os-release spec; never guesses from `uname` alone).
2. Selects the correct tested update provider (unsupported OSes fail safely).
3. Pre-flight checks (privileges, package-manager health, locks, disk, window).
4. Discovers and installs updates unattended (`safe` strategy by default).
5. Detects reboot requirements (sentinel + kernel-not-running).
6. Persists complete state atomically **before** rebooting.
7. Reboots only when policy permits; verifies a real reboot via boot ID.
8. Resumes automatically after boot; verifies kernel transition.
9. Verifies package state; runs health checks (services, HTTP, TCP, commands).
10. Reports the final result by email/webhook; delivery failure never rewrites
    the maintenance outcome.
11. Recovers conservatively from crashes, power loss, and unexpected reboots.

## Supported platforms (V1)

Ubuntu Server 22.04/24.04 LTS and Debian 12/13 (APT, systemd). Detection of
other operating systems fails safely with a clear message — support is only
claimed for providers with passing integration tests.

## Quick start (development)

```bash
pip install -e .[dev]
pytest                 # test suite
ruff check src tests   # lint
mypy --strict -p patchcycle  # types
```

See the [operations guide](docs/operations/operations.md) for installation,
scheduling, and troubleshooting on a target server.

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
