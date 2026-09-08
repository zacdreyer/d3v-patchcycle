# Configuration Specification

Status: Approved baseline for V1
Date: 2026-08-23
Implements: FR-05, FR-09, FR-10, FR-13, FR-19, FR-21; NFR-04

---

## 1. File

- Path: `/etc/d3v-patchcycle/config.toml`
- Format: TOML, parsed with Python stdlib `tomllib` (no runtime dependency).
- Permissions: root:root, mode `0600` (may reference secrets). Installer
  enforces; every load re-checks ownership/mode and refuses world-readable
  files.
- **Strict schema:** unknown keys are a validation error (exit 4). Typos in
  safety policy must never be silently ignored.
- Validation happens before any maintenance action and via
  `d3v-patchcycle config-check` (which also resolves schedule expressions and
  reports notifier/secret readiness without delivering anything).

## 2. Reference example

```toml
[maintenance]
enabled = true
schedule = "weekly"          # daily | weekly | monthly | manual
day = "sunday"               # weekly: day name; monthly: 1..28
time = "02:00"               # local time, HH:MM
random_delay = "5m"          # splay for timer firing
# window_start = "02:00"     # optional maintenance window (local HH:MM)
# window_end = "05:00"       # start > end means overnight

[updates]
strategy = "safe"            # security | safe | full
# repair_interrupted = false # allow dpkg --configure -a recovery (default false)

[updates.config_files]
policy = "keep_existing"     # keep_existing | take_package

[reboot]
policy = "when_required"     # when_required | always | never | notify_only
existing_pending = "reboot_first"   # reboot_first | continue_then_reboot | report_only
allow_if_users_logged_in = false
user_wait_timeout = "30m"

[package_manager]
lock_timeout = "15m"         # wait for apt/dpkg locks before aborting
lock_poll_interval = "15s"
refresh_timeout = "10m"
upgrade_timeout = "60m"

[maintenance.estimates]      # time budgets for window planning
refresh = "5m"
per_package = "30s"
upgrade_min = "10m"
reboot_verify = "15m"

[notifications.email]
enabled = true
to = ["admin@example.com"]
from = "patchcycle@web01.example.com"
smtp_host = "localhost"
smtp_port = 25
# smtp_starttls = true
# smtp_username = "patchcycle"
# smtp_password_env = "PATCHCYCLE_SMTP_PASSWORD"   # secret via environment

[notifications.webhook]
enabled = false
url = "https://hooks.example.internal/patchcycle"
# headers = { "Authorization" = "env:PATCHCYCLE_WEBHOOK_TOKEN" }
timeout = "15s"

[hooks]
before_upgrade = []          # argv arrays; e.g. ["/usr/local/bin/pre", "--flag"]
before_reboot = []
after_reboot = []
after_upgrade = []
on_failure = []
timeout = "5m"
failure_policy = "abort"     # abort | continue  (before_reboot default: abort)

[[health.service]]
name = "nginx"
critical = true

[[health.http]]
url = "http://127.0.0.1/health"
expected_status = 200
timeout = "10s"
critical = false

# [[health.tcp]]  host = "127.0.0.1", port = 5432, timeout = "5s"
# [[health.command]] argv = ["/usr/local/bin/check.sh"], timeout = "30s"

[logging]
level = "info"               # debug | info | warning | error
# file = "/var/log/d3v-patchcycle/patchcycle.jsonl"

[paths]
state_dir = "/var/lib/d3v-patchcycle"
lock_file = "/run/d3v-patchcycle/lock"
```

## 3. Option reference

### `[maintenance]`

| Key | Type | Default | Behaviour |
|---|---|---|---|
| `enabled` | bool | `true` | `false` → scheduled runs exit 0 with "disabled" log; manual `run` still allowed with `--force` |
| `schedule` | enum | `weekly` | `daily`/`weekly`/`monthly` map to `OnCalendar`; `manual` → no timer installed |
| `day` | string/int | `sunday` | weekly: weekday name (validated); monthly: 1–28 (no month-end ambiguity) |
| `time` | `HH:MM` | `02:00` | Local time; systemd timer interprets in local TZ |
| `random_delay` | duration | `5m` | `RandomizedDelaySec`; max 1h |
| `window_start`/`window_end` | `HH:MM` | unset | Disruptive-action window; overnight supported; unset = no window restriction |
| `estimates.*` | duration | see table above | Window-planning budgets (state-machine.md §5) |

### `[updates]`

| Key | Type | Default | Behaviour |
|---|---|---|---|
| `strategy` | enum | `safe` | Provider-mapped (provider-contract.md §2); `security` = security pocket/origin only; `full` = dist-upgrade semantics |
| `repair_interrupted` | bool | `false` | Permit `dpkg --configure -a` recovery of interrupted transactions (never removals) |
| `config_files.policy` | enum | `keep_existing` | `keep_existing` → dpkg `confdef`+`confold`; `take_package` → `confdef`+`confnew` (risky, ADR-0009; `config-check` warns) |
| `dnf.allow_erasing` | bool | `false` | DNF only: permit `--allowerasing` on `full` strategy (contract §3; `config-check` warns) |

### `[reboot]`

| Key | Type | Default | Behaviour |
|---|---|---|---|
| `policy` | enum | `when_required` | `never`/`notify_only` → finish cycle, flag `MANUAL_REBOOT_REQUIRED`, exit 6 |
| `existing_pending` | enum | `reboot_first` | Policy for reboots already pending at cycle start; `reboot_first` is subject to `policy` — with `policy=never` it degrades to report |
| `allow_if_users_logged_in` | bool | `false` | Sessions detected via `loginctl` (fallback `who`) block reboot… |
| `user_wait_timeout` | duration | `30m` | …for up to this long, then BLOCKED + notify |

### `[package_manager]`

| Key | Type | Default | Behaviour |
|---|---|---|---|
| `lock_timeout` | duration | `15m` | Max wait for apt/dpkg locks; expiry → BLOCKED, notify, exit 2 |
| `lock_poll_interval` | duration | `15s` | Poll interval while waiting |
| `refresh_timeout` | duration | `10m` | Hard cap on index refresh |
| `upgrade_timeout` | duration | `60m` | Hard cap on apply step (timeout handling: provider-contract.md §2) |

### `[notifications.*]`

- `email`: `enabled`, `to` (list, required when enabled), `from`,
  `smtp_host` (default `localhost`), `smtp_port` (default 25),
  `smtp_starttls` (default false; config-check warns when credentials are
  configured without TLS), `smtp_username`, `smtp_password_env` (name of an
  environment variable holding the password — the value is never read from
  config or written to logs/state). Plaintext `smtp_password` is accepted for
  compatibility but always triggers a config-check warning.
- `webhook`: `enabled`, `url` (must be `https://` unless host is
  localhost/loopback), `headers` map (values prefixed `env:` are resolved
  from the environment), `timeout`.
- Delivery semantics: all enabled notifiers attempted; per-notifier status
  recorded; delivery failure never changes the maintenance outcome
  (FR-14).

### `[hooks]`

- Five hook points: `before_upgrade`, `before_reboot`, `after_reboot`,
  `after_upgrade`, `on_failure`. Each is a list of **argv arrays**
  (TOML array of arrays) — never shell strings.
- Validation: every hook path must be absolute, exist at config-check,
  root-owned, not group/world-writable.
- `timeout` per hook run (default 5m); exit code, stdout/stderr tail, and
  duration logged with `run_id`.
- `failure_policy`: `abort` (default for `before_reboot` and
  `before_upgrade`) blocks the cycle (→ FAILED, notify); `continue` logs and
  proceeds. `after_*` hooks default to `continue`.

### `[health.*]` checks

| Check | Keys | Semantics |
|---|---|---|
| `service` | `name`, `timeout`, `critical` | Bounded `systemctl is-active --quiet <name>` (name validated: `[a-zA-Z0-9:_.@-]+`, suffix optional) |
| `http` | `url`, `expected_status`, `timeout`, `critical` | GET via stdlib urllib; status must equal expected |
| `tcp` | `host`, `port`, `timeout`, `critical` | connect succeeds |
| `command` | `argv`, `timeout`, `critical` | exit 0; no shell; same path validation as hooks |

Plus implicit systemd check: parse `systemctl list-units --failed --output=json`
with a 10-second timeout. Probe errors are failed checks, not an empty success.
Health failures are reported distinctly from
update failures; `critical=true` failure → cycle outcome FAILED.

### `[logging]`

| Key | Type | Default | Behaviour |
|---|---|---|---|
| `level` | enum | `info` | Python logging level |
| `file` | path | unset | JSONL private persistent log (0600); trusted parent created on first use |

### `[paths]`

Advanced override of state/lock locations. Values must be absolute; the
installer and loader enforce permissions. Changing these on an installed
system requires re-running `install`.

## 4. Duration & time grammar

- Durations: `<int>[s|m|h]` (e.g. `90s`, `15m`, `2h`), or integer seconds.
  Rejected: negatives, zero for timeouts, mixed units.
- Times: `HH:MM` 24-hour local. Days: full lowercase English names or
  ISO weekday numbers 1–7 (weekly); 1–28 (monthly).

## 5. Validation errors

Every invalid option fails with: key path, expected type/values, actual
value, and a one-line fix hint. Example:

```
config error: updates.strategy: expected one of [security, safe, full], got "safee".
Hint: did you mean "safe"?
No maintenance actions were performed. (exit 4)
```
