# ADR-0003: systemd timers and services instead of cron

Status: Accepted — 2026-08-23

## Context

Scheduling and boot-resume need a native mechanism. The mandate explicitly
forbids dynamically modifying cron entries and prefers systemd timers on
systemd systems.

## Decision

- Scheduling: `d3v-patchcycle.timer` (calendar timer, `Persistent=true`,
  configurable `RandomizedDelaySec`) activating a oneshot
  `d3v-patchcycle.service`.
- Boot resume: always-enabled oneshot `d3v-patchcycle-resume.service`
  (`WantedBy=multi-user.target`) that no-ops when no cycle is pending.
- Non-systemd Linux: fail safe with a clear message (a `SchedulerBackend`
  abstraction exists; a cron backend is deliberately **not** planned; launchd
  backend is the designed macOS future).

## Alternatives considered

- **cron/@reboot:** forbidden by mandate; also inferior: no dependency
  ordering, no trivial "don't start if already running", no structured logs,
  brittle to edit programmatically.
- **Persistent daemon:** rejected (NFR-07) — resident processes add failure
  modes without adding capability.
- **Dynamic transient timers/units:** rejected — mutable unit state is harder
  to audit than three static, inspectable units.

## Consequences

- Missed runs while powered off are caught up (`Persistent=true`) — verified
  against systemd.timer(5); operators constrain this with maintenance windows
  (documented in operations.md §3).
- Duplicate-run prevention comes free at the systemd layer (active unit is
  not re-started) in addition to the application flock.
- The resume path is inspectable (`systemctl status`) and testable, and its
  existence is verified before any reboot is issued (FR-S15 fail-safe).
- All V1 target platforms (Ubuntu 22.04/24.04, Debian 12/13) are
  systemd-native, so no V1 capability is lost.
