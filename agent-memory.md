# agent-memory.md — D3V PatchCycle

Token-efficient cold-start memory for engineering agents. Update at each
phase boundary; archive detail into docs/, don't grow this file.

## Product

Stateful server maintenance utility: detect OS → unattended updates via
native package manager → safe reboot → auto-resume → verify → health checks →
notify admin. Local-only, no daemon, no listeners, no DB. Governing
principle: *a clearly-reported failure beats a guessed/forced success.*

**Pre-release working tree: 1.0.0rc2, state schema v2. NOT production ready.** Local audit remediation and acceptance tests passed. Final artifacts,
committed live CI and target-host staging govern stable release approval.

## Current status (2026-09-09)

- CI follow-up (2026-09-19): live rc2 Linux CI/release runs failed 11 hook/
  command-health tests because the hosted Python tool-cache ancestry is
  untrusted. Tests now use a validated system-Python fixture for those child
  commands; application/pytest remain on the matrix interpreter. Production
  trust checks are unchanged. Reproduced 11 failures with non-root-owned
  interpreter ancestry in a disposable container; after the fix, Python 3.13
  passed 602 tests with 91.63% coverage and static/doc checks passed. Live
  gates on this follow-up commit remain pending.
- User authorized execution through production readiness, including code, tests,
  SDD/TDD and memory changes. Continue the existing audit; do not restart it.
- Baseline is `1c98c9e`. Candidate **1.0.0rc2 / schema 2** is prepared on
  `release/1.0.0rc2-readiness`; CI/release gates run on release-branch pushes.
  No stable release, tag, deployment or live GitHub CI result has been verified.
- The source audit reviewed all production modules and found 25 initial grouped
  issues, followed by additional native/systemd failures. Implemented fixes cover
  durable reboot facts/retries/verified boot identity; reboot-first and recovery;
  corruption refusal, strict state and protected files; clean/custom installation
  and uninstall locking; native PM locks/versionlocks/version ordering; truthful
  durable reports; TLS/redirect/secret protections; process-group timeouts.
- Further fixes: package scripts require SUID/SGID permission changes, so both
  systemd units explicitly set RestrictSUIDSGID=false. A real guest reproduced
  the blocked chmod before the fix. Failed security metadata/audit probes now
  fail closed; partial refresh needs evidence of a successful repo and warnings
  survive into reports. Reboot retries recheck policy/window after hooks.
- Scope: Ubuntu 22.04/24.04, Debian 12/13, Rocky 9, AlmaLinux 9, Fedora 44,
  x86_64/systemd. Unlisted versions and built-in ID_LIKE fallbacks are refused.
  RHEL/CentOS are not claimed without direct acceptance evidence.
- Final Linux Python 3.11/3.12/3.13/3.14: 601 passed each, 91.58% coverage.
  Windows Python 3.13: 587 passed, 14 POSIX skips, 90.59%. Ruff, strict mypy,
  CI guard, local documentation links and workflow YAML checks passed.
- Final L3: 11 tests across all seven platforms. Separate final strategy runs:
  APT 8 passed / DNF 3 passed, including actual security and full upgrades,
  native holds/versionlocks, APT native locks and interrupted repair.
- Final L4 normal run: 911ba2fc-475d-4205-a317-80dc205c5218. Wheel-installed
  actual upgrade, SUID restoration, reboot/resume, health and webhook passed.
  Power-cut run: 8a154872-610d-4da5-a885-da0b320d9160. Lost power during
  postinst; correctly failed unexpected-reboot at UPGRADING, no silent repair.
  Both proved durable JSON/text archive, persisted delivery success, IDLE,
  idempotent install and history-preserving uninstall/reinstall. Evidence:
  docs/development/evidence/delivery-*. Earlier final-* files are historical.
- Additional fixes: native DNF5 advisory parsing, exact APT security candidate
  pinning, strict nested state, truthful unperformed checks, private webhook
  display, and purge refusal for unrelated or invalid state files.
- Final wheel/sdist/portable ZIP passed build, twine, checksum and clean install
  checks. Wheel modules match workspace bytes; source hashes and dependency
  audit (no known vulnerabilities) are in the evidence index. Artifacts are in
  dist/. Runtime has no third-party dependencies.
- Preserve pre-existing untracked `repro-ci.sh`. Disposable validation container
  `patchcycle-readiness-audit` mounts this workspace read-only at /source;
  /candidate is the final writable source snapshot. No host PM/reboot commands were run.
- Remaining work is tracked in docs/development/production-readiness.md and
  docs/development/code-audit.md. Do not label staging or live CI verified.

The notes below preserve historical context. Locked decisions and architecture
state requirements; the audit must verify enforcement. The checklist overrides
old completion claims.

## Historical Phase 3 implementation notes

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

## Historical Phase 2 implementation notes

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
6. State: `/var/lib/d3v-patchcycle/state.json`, JSON schema v2 (ADR-0010), temp+fsync+
   replace+fsync; corrupt → quarantine + refuse.
7. Notifications: SMTP + webhook V1; delivery failure ≠ cycle failure; report
   always persisted to history.
8. Reboot proof via boot_id comparison; expected-kernel verification; refuse
   reboot if resume unit missing.
9. dpkg conffile default confdef+confold; force/allow-* flags prohibited
   (static source-scan test enforces).

## Intended architecture and policies (audit compliance)

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
