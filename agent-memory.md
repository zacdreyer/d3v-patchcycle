# agent-memory.md — D3V PatchCycle

Token-efficient cold-start memory for engineering agents. Update at each
phase boundary; archive detail into docs/, don't grow this file.

## Product

Stateful server maintenance utility: detect OS → unattended updates via
native package manager → safe reboot → auto-resume → verify → health checks →
notify admin. Local-only, no daemon, no listeners, no DB. Governing
principle: *a clearly-reported failure beats a guessed/forced success.*

## Status (2026-08-23)

- Phase 0 (research) ✅, Phase 1 (specification) ✅. **Next: Phase 2 — core
  framework (scaffolding, config, osdetect, state store, state machine,
  lock, logging, window, CLI).**
- Repo: single `main` branch; docs-only so far; no code, no CI yet.

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

- Dev setup (Phase 2): `python -m venv .venv && pip install -e .[dev]`
- Tests: `pytest`; lint: `ruff check && ruff format --check`; types:
  `mypy --strict src/patchcycle`
- CI must never run real apt/dnf on the runner (guard test).

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
