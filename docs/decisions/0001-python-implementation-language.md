# ADR-0001: Python 3.11+ as implementation language

Status: Accepted — 2026-08-23

## Context

The project's initial direction proposes Python 3 but requires confirmation
through the SDD process. The tool must: parse os-release and TOML, drive
subprocesses safely, do atomic file operations, speak SMTP/HTTP, integrate
with journald, run on minimal servers, and be auditable by sysadmins.

## Decision

Implement in Python, minimum version **3.11**, standard library only at
runtime.

## Alternatives considered

- **Go/Rust (single static binary):** attractive distribution story, but
  raises contribution barrier, complicates TDD velocity for this team, and
  the binary-distribution benefit is modest for a root-owned server tool
  installed via system Python.
- **Shell:** rejected — state machines, structured logging, strict parsing
  and testability are exactly shell's weak points; injection risk profile is
  worse.
- **Python < 3.11:** rejected — `tomllib` (3.11) is what makes TOML
  zero-dependency (ADR-0002).

## Consequences

- Zero runtime dependencies; install footprint is a directory + entrypoint.
- `platform.freedesktop_os_release()` (3.10+) gives a spec-compliant
  os-release parser for free.
- Availability confirmed on all V1 targets: Ubuntu 24.04 (3.12), Debian 13
  (3.13), Debian 12 (3.11). Debian 12's 3.11 is exactly the floor — CI tests
  3.11 explicitly.
- Dev tooling (pytest/ruff/mypy) is dev-only and never lands on servers.
