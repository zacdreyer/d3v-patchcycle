# D3V PatchCycle — Product Specification

Status: Approved baseline for V1 specification
Date: 2026-08-23
Governing principle: *A failed maintenance cycle that clearly reports the
problem is preferable to one that guesses, forces, hides, or silently repairs
its way past an unsafe condition.*

---

## 1. Purpose

D3V PatchCycle is a lightweight, stateful server maintenance utility that
automatically detects the host operating system, performs unattended
operating-system/package updates through the native package manager, safely
handles required reboots, resumes interrupted maintenance after restart,
verifies system health, and reports the final result to an administrator.

It orchestrates native OS mechanisms; it does not replace them.

## 2. Goals

1. Detect the host OS without administrator configuration and select the
   correct update provider; fail safely and clearly on unsupported systems.
2. Perform fully unattended package updates with conservative defaults.
3. Detect reboot requirements; persist complete state before rebooting;
   verify that a reboot genuinely occurred; resume the cycle after boot.
4. Recover safely and report accurately after crashes, power failures, failed
   upgrades, and unexpected reboots.
5. Distinguish "packages updated" from "server is healthy" via configurable
   post-maintenance health checks.
6. Notify the administrator of the final result with enough detail to act on.
7. Maintain detailed structured logs correlatable by a per-cycle `run_id`.
8. Never perform destructive recovery actions to force an update to continue.

## 3. Non-goals (V1)

- Web UI, REST API, GUI, central management server, fleet console.
- Listening on any TCP port; remote command execution; agent daemon.
- Databases or cloud service dependencies.
- Distribution release upgrades (e.g. `do-release-upgrade`).
- Replacing `unattended-upgrades`/`dnf-automatic` internals — PatchCycle
  orchestrates `apt-get`/native tools directly with its own policy layer.
- macOS, SUSE/openSUSE, Alpine, Arch, DNF-family **implementation** in V1
  (architecture must not preclude them; DNF-family is strongly preferred
  immediately after the APT provider is proven — see implementation plan).

## 4. Target users

System administrators operating one to several hundred Linux servers who want
predictable, auditable, unattended patching with safe reboot handling, without
adopting a fleet-management platform.

## 5. Supported platforms

### V1 (fully supported, integration-tested)

| Platform | Versions | Provider | Init/scheduler |
|---|---|---|---|
| Ubuntu Server | 22.04 LTS, 24.04 LTS | APT | systemd |
| Debian | 12 (bookworm), 13 (trixie) | APT | systemd |

### V1.1 target (strongly preferred fast-follow)

| Platform | Versions | Provider |
|---|---|---|
| RHEL / Rocky / AlmaLinux | 9.x | DNF |
| Fedora | current, current-1 | DNF |

### Architecture-ready (no support claim until implemented and tested)

SUSE/openSUSE (Zypper), Alpine (APK), Arch (Pacman), macOS
(`softwareupdate` + Homebrew as separate providers, `launchd` scheduling).

**Support honesty rule:** detection of an OS never implies support. Only
providers with passing integration tests may declare support; everything else
exits with `Unsupported operating system: <PRETTY_NAME>` and performs no
maintenance actions.

## 6. Functional requirements

Each requirement carries an ID for traceability into tests.

### Detection & providers

- **FR-01** Detect OS family, distribution, version, codename, architecture,
  kernel, and init system from `/etc/os-release` (per freedesktop spec),
  `platform`, and runtime probes. Never infer the package manager from
  `uname` alone.
- **FR-02** Select exactly one update provider via an explicit
  ID/ID_LIKE mapping. Unknown or unmapped systems fail safe (FR-01 output is
  included in the error).
- **FR-03** Isolate all OS-specific behaviour behind the provider contract in
  `docs/specifications/provider-contract.md`. The core engine contains no
  APT/DNF/macOS-specific logic.

### Maintenance cycle

- **FR-04** Execute the maintenance cycle as the persistent state machine in
  `docs/specifications/state-machine.md`. Every state transition is persisted
  atomically before the corresponding action executes.
- **FR-05** Pre-flight checks before any system change: privileges, OS/provider
  support, config validity, state storage writability, package-manager health
  (no unresolved interrupted transaction), package-manager lock availability
  (with bounded wait), disk space (`/`, `/boot` where relevant), notification
  config validity, maintenance-window sanity.
- **FR-06** Refresh package indexes, discover available updates, and report
  them before installing anything.
- **FR-07** Install updates unattended per the configured strategy
  (`security` | `safe` | `full`, default `safe`) with provider-appropriate
  semantics. Never use dangerous force flags (see ADR-0009 and threat model).
- **FR-08** Detect reboot requirement via the provider's native mechanism
  (APT: `/var/run/reboot-required` + kernel check; DNF: `needs-restarting -r`).
- **FR-09** Apply the configured policy for a reboot that was already pending
  when the cycle started: `reboot_first` (default) | `continue_then_reboot` |
  `report_only`.
- **FR-10** Reboot only when `reboot.policy` permits, within reboot rules
  (users-logged-in constraint, maintenance-window rules), after: state
  persisted, logs flushed, boot ID and kernel recorded, resume mechanism
  verified present, pre-reboot hooks succeeded.
- **FR-11** After boot, the resume service detects a pending cycle, verifies a
  real reboot occurred (boot ID changed), verifies expected kernel state where
  applicable, and resumes at the correct state — never repeating completed
  upgrade operations.
- **FR-12** Verify package state after completion (provider `verify()`:
  e.g. `apt-get check`, `dpkg --audit`, outstanding-update count).
- **FR-13** Run configured health checks (systemd services, failed units,
  HTTP, TCP, command) and report their results distinctly from update results.
- **FR-14** Send a final report to all enabled notification providers
  (V1: SMTP email, generic webhook). Notification failure must not convert a
  successful update into a failed one; it is logged and reported as
  `notification_status: failed` in state/history.
- **FR-15** Enforce single execution: application flock plus systemd
  no-restart-while-active semantics; a second concurrent run exits with code 3.

### CLI

- **FR-16** Commands: `status`, `run`, `run --dry-run`, `updates`, `detect`,
  `config-check`, `history`, `version`, `install`, `uninstall`, `resume`.
- **FR-17** `resume` is the boot-time continuation entrypoint invoked by the
  resume service; it refuses to run interactively unless `--force` is given,
  and even then only if no cycle is active (guarded internal command, not a
  casual admin interface).
- **FR-18** `--dry-run` performs detection, pre-flight (read-only parts),
  refresh (allowed; index refresh does not alter installed state), update
  listing, reboot-requirement probe, and reports the proposed plan. It must
  not install/remove packages, alter state files, reboot, or run hooks
  classified as destructive.

### Scheduling & installation

- **FR-19** `install` sets up: application files, default config (never
  overwriting an existing config), state/log directories with secure
  permissions, systemd service + timer (from `[maintenance]` schedule) +
  boot-resume service. Idempotent; validates the result
  (`systemd-analyze verify`, `systemd-analyze calendar`, config check).
- **FR-20** `uninstall` stops/disables units, removes units and application
  files, and preserves config, state, logs, and history unless `--purge`.
- **FR-21** Scheduling config is human-readable (`daily`/`weekly`/`monthly`/
  `manual` + day + time), validated at config-check and install time; invalid
  schedules fail clearly. Non-systemd systems fail safe with a clear message
  (a scheduler abstraction exists for future `launchd` support).

## 7. Non-functional requirements

- **NFR-01** Python 3.11+; zero runtime dependencies beyond the standard
  library. Dev/test dependencies (pytest, ruff, mypy) are justified in the
  architecture doc and never installed on target servers.
- **NFR-02** Deterministic, idempotent operations: re-running any stage after
  interruption converges to the same outcome; repeated installs create no
  duplicate units/schedules/state.
- **NFR-03** Structured logging: journald primary on Linux; optional JSONL
  file. Every record carries `run_id`, `state`, `stage`. No secrets in logs.
- **NFR-04** All privileged file writes are atomic (temp + fsync + rename) and
  permission-enforced (config 0600, state 0600, state dir 0700).
- **NFR-05** All subprocess calls use argv arrays, `shell=False`, absolute
  resolved binary paths, and a scrubbed environment.
- **NFR-06** Type hints throughout; ruff + mypy clean in CI; focused modules
  with dependency injection for testability.
- **NFR-07** The application is a short-lived process activated by a timer or
  boot service; it never stays resident to wait for schedules.
- **NFR-08** Conservative defaults: strategy `safe`, reboot `when_required`,
  no reboot with users logged in, `existing_pending=reboot_first` (subject to
  reboot policy), notification on failure always attempted.

## 8. Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (or nothing to do) |
| 1 | Maintenance failed (see state/report) |
| 2 | Blocked by pre-flight check — no update action taken |
| 3 | Another PatchCycle instance holds the execution lock |
| 4 | Configuration invalid |
| 5 | Unsupported operating system / provider unavailable |
| 6 | Reboot required but policy prohibits; manual reboot needed |

## 9. Definition of done (V1)

A supported server can: be scheduled → start unattended → detect OS → select
provider → pre-flight → discover updates → install updates → detect reboot
requirement → persist state → reboot → resume automatically → verify reboot →
verify package state → run health checks → notify administrator → mark the
cycle COMPLETED; and for every failure in that chain, recover or report
accurately per `docs/specifications/failure-recovery.md`, with the full path
covered by automated tests including at least one real VM reboot test.
