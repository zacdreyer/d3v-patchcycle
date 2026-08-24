# d3v-patchcycle

D3V PatchCycle is a lightweight, stateful server maintenance utility that
automates unattended OS/package updates, safely handles required reboots,
resumes maintenance after restart, verifies system health, and reports the
final result to an administrator.

> A failed maintenance cycle that clearly reports the problem is preferable to
> an automated maintenance cycle that guesses, forces, hides or silently
> repairs its way past an unsafe condition.

## ⚠️ Under active development

**D3V PatchCycle is pre-release software (v1.0.0-rc1).** The codebase is
feature-complete and heavily tested (390+ tests, ~92% branch coverage, mypy
`--strict` + ruff clean, T1–T14 security sweep), but a stable release is only
cut after the full gated pipeline passes — including a **real VM reboot**
end-to-end run (the release pipeline refuses to publish unless it passes).
Treat rc1 as a release candidate: test it on non-production systems first.

## What it does

1. Detects the host OS (os-release spec; never guesses from `uname` alone).
2. Selects the correct tested update provider (unsupported OSes fail safely).
3. Pre-flight checks (privileges, PM health, locks, disk, maintenance window).
4. Discovers and installs updates unattended (`safe` strategy by default).
5. Detects reboot requirements (sentinel file + kernel-not-running).
6. Persists complete state atomically **before** rebooting.
7. Reboots only when policy permits; proves a real reboot via boot ID.
8. Resumes automatically after boot; verifies the expected kernel is running.
9. Verifies package state; runs health checks (services, HTTP, TCP, command).
10. Reports the result by email/webhook; delivery failure never rewrites the
    maintenance outcome.
11. Recovers conservatively from crashes, power loss, and unexpected reboots —
    never kills package managers, never deletes lock files, never forces.

## Supported platforms

| Family | Platforms | Provider |
|---|---|---|
| Debian | Ubuntu Server 22.04/24.04 LTS, Debian 12/13 | APT |
| RHEL | RHEL 9, Rocky Linux 9, AlmaLinux 9, Fedora 41 | DNF |

All targets are systemd-based. Other operating systems are detected and fail
safely (`Unsupported operating system: …; no maintenance actions were
performed`) — support is only claimed for providers with passing integration
tests. Zypper (SUSE/openSUSE), APK (Alpine), Pacman (Arch), and macOS are
planned (Phase 8 remainder).

---

## Installation

### Requirements

- A supported Linux system (see table) with **systemd**
- **Python 3.11+** (`python3 --version`)
- root (PatchCycle manages packages and reboots)

### Option A — pip (recommended)

```bash
# From a downloaded release wheel/zip on this repo's Releases page:
sudo python3 -m pip install ./d3v_patchcycle-1.0.0rc1-py3-none-any.whl

# (Once published to PyPI: sudo python3 -m pip install d3v-patchcycle)
```

### Option B — from source

```bash
git clone https://github.com/<org>/d3v-patchcycle.git
cd d3v-patchcycle
sudo python3 -m pip install .
```

### Install host integration (systemd units + config + state dirs)

```bash
sudo d3v-patchcycle install
```

This is idempotent and safe to re-run. It:

- writes `/etc/d3v-patchcycle/config.toml` (0600 root:root) **only if absent**
  (an existing config is never overwritten; the new default lands in
  `config.toml.new` for you to merge);
- creates `/var/lib/d3v-patchcycle/` (0700) + `history/`;
- installs + validates (`systemd-analyze`) three units:
  - `d3v-patchcycle.timer` — the schedule,
  - `d3v-patchcycle.service` — the run,
  - `d3v-patchcycle-resume.service` — post-reboot continuation (always on);
- enables the resume service and the timer (`enable --now`).

Verify:

```bash
d3v-patchcycle config-check   # validate configuration
d3v-patchcycle detect         # confirm OS + provider support
d3v-patchcycle run --dry-run  # see the full plan, change nothing
```

---

## Configuration

Everything lives in **`/etc/d3v-patchcycle/config.toml`** (TOML, strict schema
— unknown keys are rejected with a "did you mean" hint). Validate any change
with `d3v-patchcycle config-check` **before** it takes effect.

### Complete reference

```toml
# ---------------- scheduling ----------------
[maintenance]
enabled = true            # false = scheduled runs skip; manual `run` needs --force
schedule = "weekly"       # daily | weekly | monthly | manual
day = "sunday"            # weekly: weekday name or 1-7; monthly: 1-28
time = "02:00"            # local time HH:MM (24h)
random_delay = "5m"       # splay to avoid mirror stampedes (max 1h)
# window_start = "02:00"  # optional window: disruptive actions only start if
# window_end   = "05:00"  # they fit inside (overnight windows supported)

# time budgets used to decide whether an action fits the remaining window
[maintenance.estimates]
refresh = "5m"
per_package = "30s"
upgrade_min = "10m"
reboot_verify = "15m"

# ---------------- update policy ----------------
[updates]
strategy = "safe"           # security | safe | full  (default safe = conservative)
repair_interrupted = false  # allow `dpkg --configure -a` recovery (never removes)

[updates.config_files]
policy = "keep_existing"  # keep_existing (dpkg confdef+confold) | take_package (risky)

[updates.dnf]             # DNF-family only
allow_erasing = false     # permit --allowerasing on `full` (default off)

# ---------------- reboot policy ----------------
[reboot]
policy = "when_required"  # when_required | always | never | notify_only
existing_pending = "reboot_first"  # reboot_first | continue_then_reboot | report_only
allow_if_users_logged_in = false   # block reboot while sessions exist
user_wait_timeout = "30m"

# ---------------- package-manager waits ----------------
[package_manager]
lock_timeout = "15m"      # wait for apt/dnf locks before aborting (never kills)
lock_poll_interval = "15s"
refresh_timeout = "10m"
upgrade_timeout = "60m"

# ---------------- notifications ----------------
[notifications.email]
enabled = true
to = ["admin@example.com"]
from = "patchcycle@web01.example.com"
smtp_host = "localhost"
smtp_port = 25
smtp_starttls = true                  # strongly recommended with credentials
smtp_username = "patchcycle"
smtp_password_env = "PATCHCYCLE_SMTP_PASSWORD"   # secret NAME, value from env

[notifications.webhook]
enabled = false
url = "https://hooks.example.internal/patchcycle"   # https required off-loopback
headers = { Authorization = "env:PATCHCYCLE_WEBHOOK_TOKEN" }
timeout = "15s"

# ---------------- hooks (argv arrays, never shell) ----------------
[hooks]
before_upgrade = [["/usr/local/bin/pre-upgrade"]]
before_reboot  = [["/usr/local/bin/pre-reboot"]]
after_reboot   = []
after_upgrade  = []
on_failure     = [["/usr/local/bin/on-fail"]]
timeout = "5m"
failure_policy = "abort"    # abort | continue (before_* default abort)

# ---------------- health checks ----------------
[[health.service]]
name = "nginx"
critical = true

[[health.http]]
url = "http://127.0.0.1/health"
expected_status = 200
timeout = "10s"
critical = false

# [[health.tcp]]     host = "127.0.0.1", port = 5432
# [[health.command]] argv = ["/usr/local/bin/check.sh"]

# ---------------- logging ----------------
[logging]
level = "info"            # debug | info | warning | error
# file = "/var/log/d3v-patchcycle/patchcycle.jsonl"   # optional JSONL

# ---------------- paths (advanced) ----------------
# [paths]
# state_dir = "/var/lib/d3v-patchcycle"
# lock_file = "/run/d3v-patchcycle/lock"
```

### Secrets (do this)

Never put passwords in the config. Reference environment variables and put the
values in `/etc/d3v-patchcycle/environment` (root:root **0600**), which the
systemd units load via `EnvironmentFile=-`:

```bash
sudo install -m 0600 /dev/null /etc/d3v-patchcycle/environment
echo 'PATCHCYCLE_SMTP_PASSWORD=your-secret' | sudo tee -a /etc/d3v-patchcycle/environment
sudo systemctl daemon-reload
```

### Scheduling

```bash
d3v-patchcycle schedule           # show the effective schedule (OnCalendar)
sudo d3v-patchcycle install       # (re)apply after editing [maintenance]
systemctl list-timers d3v-patchcycle.timer   # next run
```

`Persistent=true` means a server that was **off** at schedule time catches up
at next boot — combine with a maintenance window if boot-time patching must be
constrained.

---

## Usage

| Command | What it does |
|---|---|
| `d3v-patchcycle run` | Run a full maintenance cycle now |
| `d3v-patchcycle run --dry-run` | Full plan; no changes, no state writes |
| `d3v-patchcycle run --force` | Manual run even when `maintenance.enabled=false` |
| `d3v-patchcycle updates` | List applicable updates (no changes) |
| `d3v-patchcycle status` | Current/last cycle state |
| `d3v-patchcycle history` | Recent cycles with outcomes |
| `d3v-patchcycle detect` | Show detected OS + provider support |
| `d3v-patchcycle config-check` | Validate config, schedule, notifiers, hooks |
| `d3v-patchcycle schedule` | Show effective schedule |
| `d3v-patchcycle version` | Version + state schema version |

Exit codes: `0` ok · `1` failed · `2` blocked (pre-flight) · `3` another
instance running · `4` config invalid · `5` unsupported OS · `6` reboot
required but policy forbids.

Read results:

- **Report**: emailed/webhooked each cycle; also
  `/var/lib/d3v-patchcycle/history/<run_id>.report.txt`
- **Logs**: `journalctl -u d3v-patchcycle.service -u d3v-patchcycle-resume.service`
  (every record carries a `run_id`)
- **State**: `d3v-patchcycle status`

## Update PatchCycle itself

```bash
sudo python3 -m pip install --upgrade d3v-patchcycle
sudo d3v-patchcycle install      # re-render units if the defaults changed
```

Your config, state, history, and logs are preserved across upgrades.

## Uninstall

```bash
sudo d3v-patchcycle uninstall           # removes units; keeps config/state/history
sudo d3v-patchcycle uninstall --purge   # also removes config, state, history, logs
sudo python3 -m pip uninstall d3v-patchcycle
```

---

## How it stays safe

- **Persistent state machine** — every transition is written atomically before
  the action runs; a crash at any point resumes at the correct state.
- **Reboot is proven, not assumed** — the kernel boot ID is recorded before
  reboot and must differ after; an expected new kernel must actually be
  running, else the cycle fails and tells you.
- **Conservative by default** — `safe` strategy, no reboot with users logged
  in, no force flags, never kills apt/dnf, never deletes lock files.
- **Honest reporting** — a health-check failure is reported distinctly from a
  package failure; a notification failure never flips a good cycle to failed.

Full detail: [docs/](docs/README.md).

## Documentation

- [Product specification](docs/requirements/product-specification.md)
- [System architecture](docs/architecture/system-architecture.md)
- [State machine specification](docs/specifications/state-machine.md)
- [Provider contract](docs/specifications/provider-contract.md)
- [Configuration specification](docs/specifications/configuration.md)
- [Failure & recovery specification](docs/specifications/failure-recovery.md)
- [Security threat model](docs/security/threat-model.md)
- [Test strategy & TDD](docs/testing/test-strategy.md)
- [Operations guide](docs/operations/operations.md)
- [Architecture decision records](docs/decisions/README.md)
- [Agent memory](agent-memory.md) — token-efficient cold-start summary

## Development roadmap

Built with Spec-Driven Development + strict TDD. Phases 0–7 complete
(`v1.0.0-rc1`); Phase 8 in progress (DNF ✅; Zypper/APK/Pacman/macOS planned).
See the [implementation plan](docs/development/implementation-plan.md).

## Development

```bash
pip install -e .[dev]
pytest                          # test suite
ruff check src tests            # lint
mypy --strict -p patchcycle     # types
pytest -m container tests/integration   # L3 container matrix (needs Docker)
```

Releases are gated: quality → unit matrix → L3 containers → **L4 real-VM
reboot**; a tag only publishes after all pass (see
[.github/workflows/release.yml](.github/workflows/release.yml)).

## License

MIT
