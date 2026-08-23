# Phased Implementation Plan

Status: Active
Date: 2026-08-23

Phases are gated: a phase is done when its exit criteria pass in CI and the
docs are synchronized. All code is TDD (RED → GREEN → REFACTOR → SYNC).

---

## Phase 0 — Research ✅ COMPLETE (2026-08-23)

Output: `docs/research/phase-0-research.md` — os-release, apt-get/dpkg/
debconf, unattended-upgrades & reboot sentinel, systemd timers, DNF
needs-restarting, boot-id, Python 3.11 floor. All verified against current
official documentation.

## Phase 1 — Specification ✅ COMPLETE (2026-08-23)

Output: full SDD set under `docs/` (requirements, architecture, state
machine, provider contract, configuration, failure/recovery, threat model,
test strategy, operations) + 9 ADRs + this plan. No production code written.

## Phase 2 — Core framework ✅ COMPLETE (2026-08-23)

Delivered via TDD (190 tests, 90.4% coverage, ruff/mypy-strict clean):

1. ✅ Scaffolding: `pyproject.toml` (src layout, console script, zero runtime
   deps), ruff + mypy-strict + pytest config, GitHub Actions CI (lint/types/
   format, unit matrix 3.11–3.14 × ubuntu/windows, package build, pip-audit,
   destructive-command guard).
2. ✅ `models.py`, `config.py` — strict TOML schema with typo hints,
   duration/time grammar, secret indirection, warnings for risky options.
3. ✅ `osdetect.py` (os-release spec parser incl. quoting/escapes/duplicates;
   /etc precedence, never combined) + provider registry with support-honesty
   failure. **Note:** provider registry is empty until Phase 3 registers APT —
   `run`/`resume`/`updates` fail safely with exit 5.
4. ✅ `state_store.py` (atomic temp+fsync+replace+dir-fsync; quarantine on
   corruption; schema-version refusal) + `states.py` (transition table +
   recovery arcs) + `engine.py` — full cycle driver incl. simulated-reboot
   L2 path, crash recovery (FR-S1/S2/S6/S7), policies, window gating,
   maintenance.enabled gating.
5. ✅ `lock.py` (flock/msvcrt, stale-proof), `logging_setup.py` (JSONL +
   redaction filter), `window.py` (overnight windows), `reboot.py`
   (policy/gates/boot-id/expected-kernel), `hooks.py`, `health.py`,
   `notify/base.py`.
6. ✅ `cli.py` — all V1 commands except install/uninstall (Phase 4);
   `run --dry-run`/`updates`/`detect`/`config-check`/`status`/`history`/
   `version`/`resume`; `--config` before or after subcommand.

Exit criteria met: engine completes and recovers a full simulated cycle
including simulated reboot in L2 tests; mypy strict clean; coverage gate ≥90%.
**Deviation note:** Phase-2 scope items "systemd installer" moved wholly to
Phase 4 as planned; `install`/`uninstall` commands intentionally absent until
then.

## Phase 3 — APT provider (V1 platform support)

Scope: `providers/apt.py` per provider-contract.md §2 — binary resolution,
preflight (dpkg audit, lock probe, pre-existing reboot snapshot,
unattended-upgrades warning), refresh, list (simulation parsing + security
pocket classification via `apt-cache policy`), apply (strategy mapping,
conffile policy, prohibited-flag static test, timeout handling), reboot
probe (sentinel + kernel check), verify. L3 container matrix
(ubuntu 22.04/24.04, debian 12/13).

Exit criteria: full cycle against a real apt in containers; failure-injection
rows FR-S1–S3, S10 green.

## Phase 4 — Scheduling, reboot & resume

Scope: `installer.py` + unit templates (timer/service/resume + hardening),
`reboot.py` (policy gates, users-logged-in via loginctl/who, boot-id capture,
resume-unit verification fail-safe), `resume` command, window gating of
disruptive actions, existing-pending policy.

Exit criteria: L2 simulated-reboot cycle green; install/uninstall idempotency
tests green; `systemd-analyze` validation in CI where available; VM harness
(L4) scripted (may run nightly, not per-PR).

## Phase 5 — Notifications

Scope: `report.py` (spec-fixed report formats), `notify/smtp.py`,
`notify/webhook.py`, retry/backoff, `notification_status` semantics,
canary-secret redaction tests.

Exit criteria: success/failure report snapshots match product-spec examples;
FR-S11 green.

## Phase 6 — Health checks & hooks

Scope: `health.py` (service/http/tcp/command + failed-units implicit check,
criticality rollup), `hooks.py` (argv validation, timeouts, failure
policies), dry-run hook classification.

Exit criteria: FR-S12 green; T8/T1 hook security tests green.

## Phase 7 — Hardening

Scope: L4 VM reboot tests in nightly CI (real reboot + power-cut), coverage
gaps to gate, fuzz-ish config/parser robustness passes, security test sweep
(T1–T14), performance sanity (no regression on 1000+ package lists),
documentation final sync, V1.0 tag.

Exit criteria: Definition of Done (product-specification.md §9) demonstrably
met on Ubuntu 24.04 and Debian 13 VMs.

## Phase 8 — Additional providers (post-V1, ordered)

1. **DNF** (RHEL 9 / Rocky / Alma / Fedora) — V1.1; provider-contract.md §3
   notes exist; requires strategy-mapping ADR section + container matrix.
2. Zypper / APK / Pacman — each needs its own risk analysis (e.g. Pacman
   partial-upgrade policy) before registration.
3. macOS (`softwareupdate` + separate Homebrew provider, launchd scheduler
   backend) — requires the scheduler abstraction's second implementation.

## Cross-phase rules

- Every phase: spec updated first, tests before code, docs synced in the
  same change, ADR for any decision that revises an existing one.
- CI must never run destructive commands on the runner host (guard test).
- `agent-memory.md` updated at each phase boundary.
