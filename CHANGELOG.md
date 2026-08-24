# Changelog

All notable changes to D3V PatchCycle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.0.0-rc1] — 2026-08-24

First release candidate. Feature-complete for V1; the stable 1.0.0 tag is cut
only after the gated release pipeline passes, including the L4 real-VM reboot
harness.

### Added

- **Core engine**: persistent 14-state maintenance state machine with atomic,
  schema-versioned state; crash/power-loss recovery; conservative
  unexpected-reboot and corrupt-state handling.
- **OS detection**: os-release (freedesktop spec) parser with provider
  registry; unsupported operating systems fail safely with a clear message.
- **APT provider**: Ubuntu 22.04/24.04, Debian 12/13 — refresh, discovery
  (simulation + security-pocket classification), unattended apply
  (security/safe/full), conffile policy (keep_existing/take_package),
  reboot detection (sentinel + kernel-not-running), verify.
- **DNF provider**: RHEL 9, Rocky 9, AlmaLinux 9, Fedora 41 — check-update
  exit-code inversion handled, needs-restarting reboot probe with kernel
  fallback, updateinfo security classification, optional `--allowerasing`.
- **Reboot handling**: boot-ID proof of a real reboot, expected-kernel
  verification, resume-unit fail-safe (refuses to reboot without a recovery
  path), bounded reboot retry.
- **Scheduling**: systemd timer (Persistent catch-up) + hardened oneshot
  service + always-on resume service; human-readable daily/weekly/monthly/
  manual schedules; maintenance windows with fit-estimates.
- **Notifications**: SMTP email (STARTTLS, env-indirected credentials) and a
  generic webhook (env-indirected headers, https-only off-loopback);
  per-notifier retry; delivery failure never changes the maintenance outcome.
- **Health checks**: systemd services, failed-units, HTTP, TCP, command —
  reported distinctly from update results; criticality controls the outcome.
- **Hooks**: argv-only hooks with timeouts and per-point failure policy; never
  shell-interpolated; skipped in dry-run.
- **CLI**: run / run --dry-run / updates / detect / config-check / status /
  history / schedule / version / install / uninstall [--purge] / resume.
- **Security**: T1–T14 threat-model mitigations tested — argv-only subprocess,
  fixed-path binary resolution, scrubbed child environments, strict config
  schema, secret redaction, state symlink/corruption refusal, log-injection
  safety, duplicate-execution and duplicate-notification prevention.

### Notes

- Zero runtime dependencies (Python 3.11+ standard library only).
- `run --dry-run` reports OS, provider, applicable updates, reboot
  requirement, health checks, hooks (skipped), and notification config without
  changing anything.
