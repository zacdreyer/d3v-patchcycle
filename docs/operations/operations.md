# D3V PatchCycle — Operations Guide

> **Release candidate 1.0.0rc2 (2026-09-08): local acceptance passed.**
> Audit fixes, seven-distribution integration tests and normal/power-loss VM
> recovery are verified. Stable approval requires final-commit CI and target-host
> staging. Follow the [readiness checklist](../development/production-readiness.md).

Status: Release candidate 1.0.0rc2; deployment acceptance pending
Date: 2026-09-08

> The **[README](../../README.md)** contains the full install / configure /
> schedule / use / upgrade / uninstall guide. This document covers the
> operational detail: unattended-upgrades coexistence, reading results,
> troubleshooting, and manual recovery.

---

## 1. Installation

```bash
sudo d3v-patchcycle install
```

What it does (idempotent; validated by `systemd-analyze calendar` + `verify`):

1. Requires root, obtains the maintenance execution lock, refuses a pending
   cycle, and verifies systemd calendar/unit validation.
2. Uses the existing application entrypoint installed by pip into the chosen
   virtual environment. Install the wheel before running this command.
3. Creates `/etc/d3v-patchcycle/config.toml` (0600 root:root) **only if
   absent** — an existing config is never overwritten; the shipped default
   is written to `config.toml.new` for comparison.
4. Creates `/var/lib/d3v-patchcycle/` (0700) with `history/`. File logging
   creates the configured parent directory on first use and uses a root-owned
   private file (0600). Existing writable parents or non-private files are refused.
5. Renders and installs `d3v-patchcycle.timer`, `d3v-patchcycle.service`
   (hardened oneshot, `EnvironmentFile=-/etc/d3v-patchcycle/environment`),
   and the always-on `d3v-patchcycle-resume.service` from `[maintenance]`
   config; enables the resume service and the timer (`enable --now`).
6. Prints next steps (`config-check`, `run --dry-run`).

`schedule = "manual"` installs both services and removes an existing timer.
Custom `--config` paths are included in both service commands. Configuration,
state and executable paths must have trusted ownership and permissions; symlinks
and writable ancestors are refused where they could redirect privileged I/O.
Uninstall also obtains the execution lock and refuses unresolved cycle state.

Uninstall:

```bash
sudo d3v-patchcycle uninstall          # keeps config, state, logs, history
sudo d3v-patchcycle uninstall --purge  # also removes active config and state/history
```

Purge retains the installed Python environment, external log files, the optional
environment file and `config.toml.new`. Remove those explicitly only after
reviewing the retained operational evidence and secrets. Upgrade only when no
cycle is pending; schema-2 state must not be handed to an older binary.
Purge refuses unrelated files, invalid history or unpaired reports in a custom
state directory. Inspect and relocate such files deliberately before retrying;
do not use an existing system/application directory as PatchCycle state storage.

## 2. Day-to-day commands

| Command | Purpose | Exit codes |
|---|---|---|
| `d3v-patchcycle status` | Current cycle state, last outcome, pending reboot, next scheduled run | 0 |
| `d3v-patchcycle run` | Run a maintenance cycle now | 0/1/2/3/4/5/6 |
| `d3v-patchcycle run --dry-run` | Full plan without changes | 0/2/4/5 |
| `d3v-patchcycle updates` | List applicable updates (no changes) | 0 |
| `d3v-patchcycle detect` | Show detected OS/provider/support verdict | 0/5 |
| `d3v-patchcycle config-check` | Validate config, schedule, notifiers, hooks | 0/4 |
| `d3v-patchcycle history` | Recent cycles with outcomes | 0 |
| `d3v-patchcycle version` | Version + schema version | 0 |
| `d3v-patchcycle resume` | Boot-time continuation (used by resume service; `--force` for manual) | 0/1/3 |

## 3. Scheduling

Configured in `/etc/d3v-patchcycle/config.toml`, applied with
`sudo d3v-patchcycle install` (re-renders the timer):

```toml
[maintenance]
schedule = "weekly"
day = "sunday"
time = "02:00"
random_delay = "5m"      # spreads fleet load on mirrors
```

Notes:

- The timer uses `Persistent=true`: a server **off** at schedule time runs
  maintenance at next boot. Combine with a maintenance window
  (`window_start`/`window_end`) if boot-time patching must be constrained —
  disruptive actions outside the window are deferred and reported.
- `systemctl list-timers d3v-patchcycle.timer` shows the next run.

## 4. Coexistence with unattended-upgrades (Ubuntu/Debian)

Ubuntu's `apt-daily.timer`/`apt-daily-upgrade.timer` run `unattended-upgrade`
daily and catch up at boot — the most common cause of PatchCycle's
"package manager locked" (exit 2). PatchCycle **never modifies** that
configuration. Operator decision (document, don't enforce):

- Option A (recommended): let PatchCycle own patching; disable
  unattended-upgrades' apply step:
  `APT::Periodic::Unattended-Upgrade "0";` in
  `/etc/apt/apt.conf.d/20auto-upgrades`.
- Option B: keep both; PatchCycle waits up to `lock_timeout` and reports
  BLOCKED when it loses. Safe but noisy.

Since Ubuntu 24.04, `needrestart` restarts affected services automatically
during any upgrade — including PatchCycle's. This is compatible by design;
exclude critical services via `/etc/needrestart/conf.d/` if unexpected
restarts are unacceptable.

## 5. Reading results

- **Report**: emailed/webhooked per cycle; also at
  `/var/lib/d3v-patchcycle/history/<run_id>.report.txt`.
- **Logs**: `journalctl -u d3v-patchcycle.service -u d3v-patchcycle-resume.service`
  (all records carry `run_id`); optional JSONL file per `[logging].file`.
- **State**: `d3v-patchcycle status` (human) or
  `/var/lib/d3v-patchcycle/state.json` (machine).
- Exit-code meanings are in the product specification §8.

## 6. Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| exit 2, `pm-locked` | another apt/dpkg process is active | see §4; find holder: `sudo fuser -v /var/lib/dpkg/lock-frontend` |
| exit 2, `interrupted-transaction` | dpkg half-configured packages | run `sudo dpkg --configure -a` manually, or set `repair_interrupted = true` |
| exit 3 | a cycle is running right now | `d3v-patchcycle status`; wait or investigate the PID |
| exit 5 | unsupported OS | nothing was changed; check `detect` output |
| exit 6 | updates done, reboot required but policy forbids | reboot manually in your window |
| `kernel-mismatch` failure | new kernel installed but not running after reboot | check bootloader, /boot space, kexec; PatchCycle does not touch boot config |
| `state-corrupt` failure | state file failed integrity checks | quarantined copy preserved; review, then delete quarantine to reset |
| `stale-cycle` failure | a cycle sat unfinished > 7 days | investigate cause; state is archived, next run starts fresh |
| `notification_status: failed` | cycle fine, delivery broken | check SMTP/webhook settings; report preserved in history |

## 7. Manual recovery procedure (worst case)

1. `d3v-patchcycle status` — what does it think happened?
2. `sudo dpkg --audit` — package system consistent?
3. `journalctl -u 'd3v-patchcycle*' --since "-2h"` — full trail by run_id.
4. If dpkg is dirty: `sudo dpkg --configure -a`, then
   `sudo apt-get install -f` — **by an admin, deliberately**.
5. If state is untrustworthy: move `/var/lib/d3v-patchcycle/state.json`
   aside (do not delete history), fix the package system, then
   `d3v-patchcycle run` fresh.
6. PatchCycle itself never performs step 4 autonomously unless
   `repair_interrupted = true` — and even then only `dpkg --configure -a`.

## 8. Security operations checklist

- `config.toml` is root:root 0600 (enforced at load).
- Secrets via `/etc/d3v-patchcycle/environment` (0600) + `*_env` references.
- Hooks are absolute paths, root-owned, not group/world-writable (enforced).
- Review `[updates].strategy`, `[reboot].policy`, and hook lists after any
  config change: `d3v-patchcycle config-check`.
