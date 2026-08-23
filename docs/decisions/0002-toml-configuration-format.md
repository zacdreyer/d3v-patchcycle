# ADR-0002: TOML configuration format via stdlib tomllib

Status: Accepted — 2026-08-23

## Context

The project prefers a human-readable config format, and TOML specifically if
it avoids a runtime dependency. Requirements: strict schema validation,
durations/times/enums, nested notifier/health sections, secret indirection.

## Decision

Configuration is TOML at `/etc/d3v-patchcycle/config.toml`, parsed with
Python 3.11's stdlib `tomllib`. Strict schema: unknown keys are errors.

## Alternatives considered

- **YAML:** requires PyYAML/ruamel (runtime dependency, supply-chain surface),
  and YAML's implicit typing ("Norway problem": `day = no` → boolean) is
  actively dangerous for a safety-policy file.
- **INI (configparser):** too weakly structured for nested health-check
  tables and arrays of argv.
- **JSON:** not human-writable-friendly (no comments), rejected for an
  operator-facing file.

## Consequences

- Zero added dependencies (satisfies minimal-dependency mandate).
- TOML's array-of-tables maps cleanly to `[[health.service]]` etc.
- Strict-schema validation is implemented in `config.py` with actionable
  error messages (configuration.md §5).
- Downside accepted: `tomllib` is read-only; the installer renders config
  from templates rather than rewriting TOML programmatically (which also
  guarantees it never rewrites admin comments).
