# agent-memory.md — D3V PatchCycle

Token-efficient cold-start memory for engineering agents. Update at each
phase boundary; archive detail into docs/, don't grow this file.

## Product

Stateful server maintenance utility: detect OS → unattended updates via
native package manager → safe reboot → auto-resume → verify → health checks →
notify admin. Local-only, no daemon, no listeners, no DB. Governing
principle: *a clearly-reported failure beats a guessed/forced success.*

## Status (2026-08-23)

- Phase 0/1 ✅, Phase 2 (core framework) ✅, Phase 3 (APT provider) ✅,
  **Phase 4 (scheduling/reboot/resume) ✅** — 274 tests, 91.1% coverage.
- **Next: Phase 5 — notifications** (`report.py` snapshot formats,
  `notify/smtp.py`, `notify/webhook.py`, retry/backoff, redaction tests).
- Installer exists: `install`/`uninstall [--purge]`/`schedule` commands,
  systemd unit rendering (timer Persistent=true, hardened oneshot services,
  always-on resume unit), idempotent, `systemd-analyze calendar`/`verify`
  validation. FR-S15 seam: `_resume_unit_enabled` uses the installer's
  systemd call so names can never drift.
- L4 VM harness scripted: tests/vm/reboot_harness.sh + nightly workflow
  .github/workflows/vm-reboot.yml (KVM, real reboot, webhook report check).
- APT provider registered for debian/ubuntu(+id_like). `run`/`updates` now
  work on real Debian/Ubuntu hosts.

## Phase 3 implementation notes

- `subproc.run_argv`: argv-only (string → TypeError), scrubbed env
  (PATH/LANG/LC_ALL only + explicit extra_env), timeout terminate→kill.
- `AptProvider` seams: `runner`, `search_paths`, `state_root` (prefix for
  /var/run, /var/lib/dpkg), `timer_active`, plus `configure(config)` which
  adopts `[updates]`/`[package_manager]` settings (base-class no-op default).
- Provider registry has test savepoints: `registry_savepoint/restore`.
- Simulation parsing: `Inst pkg [from] (to ...)` + kept-back block;
  security pocket via `apt-cache policy` origin lines matching `-security`.
- Kernel expectation: newest `linux-image-*` dpkg-query entry, meta packages
  excluded; `expected_kernel` compared post-reboot (FR-S13).
- L3: tests/integration/test_apt_container.py (mark `container`, skips
  without docker); CI job `container-integration` matrix.
- Engine fix: PreflightError(kind=...) preserves pm-locked/interrupted-
  transaction kinds into state error.kind.

## Phase 2 implementation notes

- Layout: src layout, console script `d3v-patchcycle = patchcycle.cli:main`.
- Engine: `_drive()` loop over `_HANDLERS`; transitions persisted BEFORE
  actions; COMPLETED finalized by driver (no handler); NOTIFYING → COMPLETED
  only (spec §1 rule 4 amended: delivery failure never fails a cycle).
- Recovery arcs REFRESHING/DISCOVERING/UPGRADING → PRECHECK exist in the
  transition table (FR-S1/S2; PRECHECK re-runs provider preflight which
  catches interrupted dpkg).
- boot_id is recorded at PRECHECK (unexpected-reboot detection, FR-S6) and
  re-recorded in REBOOTING (ADR-0008).
- `run(scheduled=…, force=…)` gates on maintenance.enabled.
- CLI: `--config` accepted before/after subcommand; `run --dry-run`,
  `run --force`, `run --scheduled` (timer uses this in Phase 4 units).
- Lock: flock POSIX / msvcrt Windows (dev only); holder pid recorded.
- Windows dev quirks handled: msvcrt byte-lock release is async (tests use
  bounded retry); config `_abs_path` accepts drive letters for dev fixtures.
- mypy 2.3.1 needed force-reinstall of librt on this machine.

## Locked decisions (ADRs in docs/decisions/)

1. Python 3.11+ stdlib-only runtime (tomllib, freedesktop_os_release). Dev
   deps: pytest, ruff, mypy, coverage.
2. TOML config `/etc/d3v-patchcycle/config.toml` (0600, strict schema,
   unknown keys = error).
3. systemd timer + oneshot service + always-on resume service; no cron.
4. Explicit persistent state machine (14 states); state persisted atomically
   BEFORE each state's action runs.
5. Explicit provider registry (ID/ID_LIKE); support only when
   integration-tested (support-honesty rule).
6. State: `/var/lib/d3v-patchcycle/state.json`, JSON schema v1, temp+fsync+
   replace+fsync; corrupt → quarantine + refuse.
7. Notifications: SMTP + webhook V1; delivery failure ≠ cycle failure; report
   always persisted to history.
8. Reboot proof via boot_id comparison; expected-kernel verification; refuse
   reboot if resume unit missing.
9. dpkg conffile default confdef+confold; force/allow-* flags prohibited
   (static source-scan test enforces).

## Key architecture facts

- Entry: CLI `d3v-patchcycle {run,resume,status,updates,detect,config-check,
  history,version,install,uninstall}`; exit codes 0..6 per product spec §8.
- Engine depends on protocols (UpdateProvider, SchedulerBackend, Notifier);
  composition root in cli.py; no globals; inject clock/paths/command runner.
- Concurrency: systemd (no restart while active) + flock
  `/run/d3v-patchcycle/lock` (auto-release on death; no stale locks).
- APT provider: `apt-get` only (never `apt`), `DEBIAN_FRONTEND=noninteractive`,
  `LANG=C`/`LC_ALL=C`, scrubbed env, argv arrays, absolute paths from fixed
  search path. Lock wait default 15m → BLOCKED; never kill PM, never delete
  locks. Reboot probe: `/var/run/reboot-required` + kernel-not-running check.
- Reboot defaults: `when_required`, no reboot with users logged in,
  `existing_pending=reboot_first` (policy-gated).
- Window semantics: gate START of disruptive actions only; never interrupt a
  dpkg transaction for the clock; verify/notify are not window-restricted.

## Commands

- Dev env: `python -m venv .venv; .venv\Scripts\pip install -e .[dev]`
  (Windows) / `pip install -e .[dev]` (Linux).
- Tests: `pytest` (fast) / `pytest --cov` (gate ≥90%).
- Lint/format: `ruff check src tests`; `ruff format --check src tests`.
- Types: `mypy --strict -p patchcycle`.
- CI must never run real apt/dnf on the runner (guard step in ci.yml).

## Known risks / watch items

- Ubuntu `apt-daily*.timer` catch-up at boot collides with resume service →
  lock waits; operations doc recommends operator choice, never auto-modify.
- Ubuntu 24.04+ needrestart auto-restarts services during upgrades — by
  design; don't fight it.
- Debian 12 ships Python 3.11 = floor; CI must include 3.11.
- dnf `check-update` exit 100 means "updates available" (inverted vs
  apt-get's 100=error) — per-provider parsers, Phase 8.

## Doc map

docs/README.md (index) → requirements/product-specification.md,
architecture/system-architecture.md, specifications/{state-machine,
provider-contract,configuration,failure-recovery}.md, security/threat-model.md,
testing/test-strategy.md, operations/operations.md,
development/implementation-plan.md, research/phase-0-research.md
