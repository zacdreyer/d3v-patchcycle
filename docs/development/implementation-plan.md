# Phased Implementation Plan

> **Readiness update (2026-09-08): NOT production ready.** Earlier completion,
> integration-test and feature claims below are historical or intended behavior,
> not current proof of specification compliance. Audit remediation is underway.
> Follow the [production-readiness checklist](production-readiness.md) for audit,
> remediation, meaningful release gates and staging acceptance. A green L4 run
> alone is insufficient. Runtime fixes and evidence are recorded in the audit.

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

## Phase 3 — APT provider ✅ COMPLETE (2026-08-23)

Delivered via TDD (238 tests total, 90.9% coverage):

- `subproc.py`: safe subprocess wrapper — argv-only (strings rejected),
  scrubbed env, timeout terminate→kill; `wait_for_lock_release` poller.
- `providers/apt.py`: full contract implementation — binary resolution from
  fixed search path (T2), preflight (dpkg --audit, lock probes, pre-existing
  reboot snapshot, unattended-upgrades timer warning), refresh (partial-repo
  warnings), list_updates (simulation parsing + security-pocket
  classification via apt-cache policy), apply (safe/security/full mapping,
  conffile policy, kernel-update + expected-kernel detection), reboot probe
  (sentinel + kernel-not-running, conservative on probe error), verify
  (check + audit + outstanding count). Prohibited-flag static test (ADR-0009).
- Provider registered for debian/ubuntu (+ID_LIKE debian); engine L2
  integration tests drive full cycles through the real provider.
- L3 container matrix scripted (ubuntu 22.04/24.04, debian 12/13) with a CI
  job; runs where Docker is available, skips locally.
- Bug found by TDD: preflight failure kinds were flattened by the engine;
  `PreflightError(kind=...)` now preserves `interrupted-transaction` etc.

Exit criteria: L1/L2 green incl. failure-injection rows FR-S1–S3, S10; L3
suite committed and CI-wired (first green CI run pending).

## Phase 4 — Scheduling, reboot & resume ✅ COMPLETE (2026-08-23)

Delivered via TDD (274 tests, 91.1% coverage):

- `installer.py`: schedule → OnCalendar translation (daily/weekly/monthly/
  manual, weekday normalisation), unit rendering (timer with Persistent=true
  + RandomizedDelaySec; oneshot service with threat-model §5 hardening;
  always-on resume service), idempotent install (content-diff writes),
  config preservation (`config.toml.new` reference), `systemd-analyze
  calendar`/`verify` validation, uninstall with `--purge` option, no-systemd
  fail-safe, `resume_unit_enabled()` FR-S15 seam now shared by the reboot
  controller wiring.
- CLI: `install`, `uninstall [--purge]`, `schedule` commands wired.
- L4 harness: `tests/vm/reboot_harness.sh` (QEMU + cloud-init Debian 13,
  real reboot, webhook report verification, boot-id proof) + nightly
  `vm-reboot.yml` workflow (never per-PR).

## Phase 5 — Notifications ✅ COMPLETE (2026-08-23)

- `notify/smtp.py` (stdlib smtplib, env-indirected credentials, explicit
  quit/close), `notify/webhook.py` (stdlib urllib JSON POST, env: header
  indirection, Bearer prefixing, https-only off loopback), `report.py`
  (canonical spec-§23 renderer). CLI `_build_notifiers` wires enabled
  notifiers. FR-S11 semantics tested: delivery failures return `failed:*`,
  never raise, never leak secrets, never change outcomes.
- Note: full-suite runs intermittently hang on this Windows dev host
  (socket teardown under load); per-file runs are green and CI (Linux) is
  unaffected. Watch item: if CI shows the same, investigate ThreadingTCPServer
  teardown further.

## Phase 6 — Health checks & hooks ✅ COMPLETE (2026-08-23)

- `HOOK_CLASSIFICATION` / `classify_hooks()`: every hook point is classified
  "skipped" in dry-run (FR-18); `dry_run()` plan now reports hooks, health
  checks, and enabled notifiers.
- Failure-policy matrix tested: before_upgrade/before_reboot default to abort
  (veto the upgrade/reboot), after_* default to continue, `failure_policy =
  "continue"` overrides before_*; engine-level tests prove a before_upgrade
  hook failure blocks before any package op and a before_reboot failure vetoes
  the reboot with updates already applied.
- Typing: engine notifiers typed as the `Notifier` ABC (mypy-strict clean).

## Phase 7 — Hardening ✅ COMPLETE (2026-08-23)

- Security sweep `tests/test_security.py` mapping T1–T14: injection (argv-only,
  hostile package names inert), PATH hijacking, environment scrubbing,
  malicious config, secret redaction incl. state-schema secret absence,
  state tamper quarantine, log-injection single-record, duplicate-notification
  prevention.
- `AptProvider._PACKAGE_NAME_RE` validates package names before argv
  construction (defence in depth; hostile names dropped, never executed).
- Robustness: seeded fuzz of os-release/simulation/policy parsers (never
  crash); config rejects pathological nesting; 1000-package simulation and
  policy classification within performance bounds.
- Coverage closed to 91.7% (gate ≥90%); lock POSIX branches, engine edges,
  state-store error paths, apply-path name handling covered.
- Full suite green on this host (intermittent Windows socket-teardown hang
  did not recur after test-server hardening in Phase 5).
- L4 VM harness runs nightly in CI; first green nightly is the V1.0 release
  gate.

## V1.0 release (gate)

Releases are gated on proof, not assertion
(`.github/workflows/release.yml`): quality → unit matrix (3.11–3.14) → L3
container matrix (apt + dnf images) → **L4 real-VM reboot harness** → only
then does a `v*` tag publish a release (wheel + sdist + portable zip +
SHA256SUMS). Run `workflow_dispatch gate_only=true` to verify
release-readiness without publishing. Tag `v1.0.0` only after a green gate.

## Phase 8 — Additional providers (post-V1)

1. **DNF** (RHEL 9 / Rocky / Alma / Fedora) ✅ — provider-contract §3 written;
   `providers/dnf.py` implemented (check-update exit-100=updates inversion
   handled per-provider, needs-restarting reboot probe, kernel fallback,
   updateinfo security classification, `--allowerasing` gated on
   `[updates.dnf] allow_erasing`); 33 unit + engine integration tests; L3
   container matrix extended (rockylinux:9, almalinux:9, fedora:41).
2. Zypper / APK / Pacman — each needs its own risk analysis (e.g. Pacman
   partial-upgrade policy) before registration.
3. macOS (`softwareupdate` + separate Homebrew provider, launchd scheduler
   backend) — requires the scheduler abstraction's second implementation.

## Cross-phase rules

- Every phase: spec updated first, tests before code, docs synced in the
  same change, ADR for any decision that revises an existing one.
- CI must never run destructive commands on the runner host (guard test).
- `agent-memory.md` updated at each phase boundary.
