# Phase 0 — Research Findings

Status: Complete (2026-08-23)
Purpose: ground every native mechanism D3V PatchCycle will orchestrate in current
official documentation. No infrastructure behaviour in this project is designed
from assumption.

---

## 1. OS identification: `/etc/os-release`

Source: `os-release(5)` man page (systemd 257, Debian trixie), verified 2026-08-23.

Key findings:

- `/etc/os-release` and `/usr/lib/os-release` contain newline-separated,
  shell-compatible variable assignments. No shell features beyond variable
  assignment are supported; values may be quoted, escapes follow shell style.
- **Precedence:** applications must read `/etc/os-release` first and use it
  exclusively if present; fall back to `/usr/lib/os-release` only if the former
  is missing. The two files must **never be combined**.
- Relevant fields:
  - `ID=` — machine-readable OS identifier (e.g. `ubuntu`, `debian`, `rhel`,
    `fedora`, `rocky`, `almalinux`, `sles`, `opensuse-leap`, `alpine`, `arch`).
  - `ID_LIKE=` — space-separated list of closely related OS IDs, closest first.
    Used as fallback when `ID` is unrecognised (e.g. `ID=ubuntu` has
    `ID_LIKE=debian`; `ID=rocky` has `ID_LIKE="rhel centos fedora"`).
  - `VERSION_ID=` — machine-readable version (`24.04`, `13`, `9.4`).
  - `PRETTY_NAME=` — human presentation string.
  - `VERSION_CODENAME=` — release codename (`noble`, `trixie`).
  - `ID` defaults to `linux`, `NAME` to `Linux`, `PRETTY_NAME` to `"Linux"`
    if unset. `VERSION_ID` may be unset on rolling releases — must not be
    relied upon.
  - Repeated keys: later entries win (shell semantics). Unknown fields must be
    ignored.
- **Python stdlib:** `platform.freedesktop_os_release()` (Python ≥ 3.10)
  implements the full spec including quoting and precedence. This removes the
  need for a hand-rolled parser and any dependency.
- Container note: runtimes may expose host identity at `/run/host/os-release`.
  PatchCycle runs on hosts, not containers, but detection code must not be
  confused by container environments; provider preflight must verify it can
  actually drive the real package manager.

**Consequence for design:** `uname` alone is never used to select a provider.
Provider selection is driven by an explicit ID/ID_LIKE mapping table.
Unknown IDs fail safely with "Unsupported operating system".

## 2. APT / dpkg automation semantics (Debian 13 / apt 3.0)

Source: `apt-get(8)` man page (apt 3.0.3, Debian trixie), verified 2026-08-23;
`debconf(7)` (debconf 1.5.91), verified 2026-08-23.

Key findings:

- **`apt-get` is the stable script interface**; the `apt(8)` CLI is a
  user-facing front-end whose output/options are explicitly not stable for
  scripts. PatchCycle uses `apt-get` exclusively.
- `apt-get update` resynchronises indexes and must always precede upgrade
  operations. `apt-get` returns **0 on success, 100 on error**.
- `upgrade` never removes packages and never installs new ones; packages
  requiring such changes are held back. `--with-new-pkgs` allows new
  dependencies but still never removes. `dist-upgrade` may remove packages.
  → This maps directly onto PatchCycle strategies `safe` (plain `upgrade`) and
  `full` (`dist-upgrade`), with `safe` as default.
- `-y/--assume-yes`: assumes yes but **aborts** on undesirable situations
  (held package change, unauthenticated package, removing essential package).
  It is a safe noninteractive driver; dangerous escape hatches
  (`--allow-remove-essential`, `--allow-downgrades`,
  `--allow-change-held-packages`, `--allow-unauthenticated`, `--force-yes`)
  are **prohibited** in PatchCycle.
- `-s/--simulate` performs a no-action simulation and can run as non-root —
  the basis for `d3v-patchcycle updates` and dry-run update discovery.
- **debconf noninteractive operation:** `DEBIAN_FRONTEND=noninteractive`
  selects the noninteractive frontend, which never prompts and uses default
  answers. `DEBIAN_PRIORITY=critical` limits question visibility. For
  non-default answers, preseeding the debconf database is the supported
  mechanism — not interactive answering.
- **Conffile prompts:** dpkg prompts when a shipped configuration file was
  locally modified and the package ships a new version. Automation-safe
  resolution is via dpkg options passed through apt-get:
  `-o Dpkg::Options::=--force-confdef` (take default action when one exists)
  combined with `--force-confold` (keep installed) or `--force-confnew`
  (take package version). PatchCycle's `keep_existing` policy maps to
  `confdef + confold`; `take_package` maps to `confdef + confnew`
  (documented as risky).
- **Interrupted dpkg:** `dpkg --audit` reports packages in inconsistent
  states (half-installed, half-configured, triggers pending).
  `dpkg --configure -a` and `apt-get install -f` are the native recovery
  tools. PatchCycle **detects and reports**; it only runs `dpkg --configure -a`
  when policy explicitly allows (`repair_interrupted = true`), and never
  removes packages to "fix" a broken state.
- **Locks:** apt/dpkg use flock-based locks (`/var/lib/dpkg/lock-frontend`,
  `/var/lib/dpkg/lock`, `/var/lib/apt/lists/lock`, archive lock). A busy lock
  means another process legitimately owns the package system. PatchCycle
  waits/retries up to `lock_timeout`, then aborts. Lock files are **never
  deleted**; processes holding them are **never killed**.

## 3. Ubuntu reboot-required & unattended-upgrades behaviour

Source: Ubuntu Server documentation, "Automatic updates" (updated 2026-07-15),
verified 2026-08-23.

Key findings:

- **Reboot-required detection:** packages signal a required reboot by creating
  the sentinel file `/var/run/reboot-required` (with
  `/var/run/reboot-required.pkgs` listing the responsible packages). This is
  the authoritative detection mechanism on Ubuntu/Debian.
- **needrestart:** from Ubuntu 24.04 LTS, `needrestart` restarts affected
  services **automatically by default** after updates (config
  `$nrconf{restart} = 'a'`). Administrators can override per-service via
  `/etc/needrestart/conf.d/`. PatchCycle must be aware that services may be
  restarted by the system during its upgrade step, and must not fight this.
- **Concurrent activity:** the `apt-daily.timer` and `apt-daily-upgrade.timer`
  systemd timers run `unattended-upgrade` daily (default), with
  `Persistent=true` — meaning a machine that was off at schedule time runs
  updates **at next boot**. This is a real source of apt-lock contention at
  boot exactly when PatchCycle's resume service runs. Design consequence:
  PatchCycle's lock-wait logic must tolerate this; operators may choose to
  disable unattended-upgrades when PatchCycle manages the host (documented in
  operations guide; PatchCycle never modifies that configuration itself).
- unattended-upgrades' own model validates PatchCycle's: noninteractive
  upgrade, `/var/run/reboot-required` detection, optional automatic reboot
  with `Automatic-Reboot-WithUsers`, mail report with SUCCESS/FAILED subject,
  `--dry-run` support.

## 4. systemd timers

Source: `systemd.timer(5)` man page (systemd 257), verified 2026-08-23.

Key findings:

- Calendar timers use `OnCalendar=` with `systemd.time(7)` calendar event
  expressions (e.g. `Sun *-*-* 02:00:00`, `weekly`). Expressions can be
  validated offline with `systemd-analyze calendar`.
- `Persistent=true` stores last-trigger time on disk and catches up missed
  runs after power-off. Desirable for maintenance timers (a server off on
  Sunday 02:00 gets patched at next boot) — but operators must understand the
  boot-time catch-up behaviour; it is configurable in the installed unit.
- Default `AccuracySec=1min` coalesces wake-ups; `RandomizedDelaySec=` spreads
  load. PatchCycle defaults: `AccuracySec=1min`, small `RandomizedDelaySec`
  (default 5min, configurable) to avoid thundering-herd on shared mirrors.
- **Duplicate execution is structurally prevented:** if the unit a timer
  activates is already active when the timer elapses, it is not started again.
  Combined with PatchCycle's own flock, this gives two independent layers of
  duplicate-run protection.
- Timers with `OnCalendar=` automatically order after `time-sync.target`,
  avoiding runs against a wrong wall clock.
- A boot-resume service (`Type=oneshot`, `WantedBy=multi-user.target`) is the
  correct native mechanism for post-reboot continuation — no `@reboot` cron,
  no daemon.

## 5. DNF / RHEL-family reboot detection (architecture-ready)

Source: dnf-plugins-core documentation (4.4.2), verified 2026-08-23.

Key findings:

- `dnf needs-restarting -r` reports whether a reboot is required via exit
  code (1 = required, 0 = not). This is the RHEL/Fedora-family equivalent of
  `/var/run/reboot-required` and the anchor for a future `DnfProvider`.
- `dnf needs-restarting -s` lists affected systemd services (health-check
  synergy).
- Strategy mapping (to be finalised in the DNF provider phase):
  `security` → `dnf upgrade --security`; `safe`/`full` → `dnf upgrade
  --refresh` (DNF's normal upgrade already resolves dependencies; semantics
  differ from APT — must not be assumed identical, per project requirement).

## 6. Boot identity & kernel verification

- `/proc/sys/kernel/random/boot_id` — kernel-generated UUID unique per boot;
  reading it is the standard way to prove a reboot occurred (compare
  recorded pre-reboot value with current value).
- `uname -r` / `platform.release()` — running kernel release; compare against
  recorded pre-reboot value and against newest installed kernel package to
  verify an expected kernel transition actually took effect.
- `os.getlogin()`-adjacent session detection: `who`/`loginctl list-sessions`
  for `allow_if_users_logged_in` policy. Prefer `loginctl` (machine-readable
  `--output=json` on newer systemd) with `who` fallback; never parse `w`.

## 7. macOS (future, architecture-ready)

- Detection: `platform.system() == "Darwin"`, `sw_vers` for product version,
  `platform.machine()` for architecture.
- Updates: `/usr/sbin/softwareupdate --list` / `--install --all` (or targeted)
  for OS updates; Homebrew (`brew update && brew upgrade`) is a separate,
  third-party provider and must be reported distinctly from OS updates.
- Scheduling/resume: `launchd` `LaunchDaemons` with `StartCalendarInterval`
  (schedule) and `RunAtLoad` (boot resume) are the native equivalents of
  systemd timer + boot service.
- No macOS implementation in V1; the provider/scheduler abstractions are
  designed so these mechanisms slot in without core changes.

## 8. Python suitability confirmation

- Python ≥ 3.11 provides `tomllib` (stdlib TOML parser) → TOML config with
  **zero runtime dependencies**, satisfying the minimal-dependency mandate.
- `subprocess.run([...], shell=False)`, `fcntl.flock`, `os.replace` (atomic
  rename), `smtplib`/`urllib`/`json`/`logging` cover every V1 functional need
  from the standard library.
- Python 3.11+ is present by default on Ubuntu 24.04 (3.12), Debian 13 (3.13),
  Fedora/RHEL-9-family (3.9–3.12; 3.11+ via AppStream on RHEL 9).
  → Minimum supported runtime: **Python 3.11**.
- Decision recorded in ADR-0001.

## 9. Secure subprocess & file-handling practices (adopted as requirements)

- Always argv arrays, `shell=False`, absolute binary paths resolved at
  provider init from a fixed search path (`/usr/bin:/bin:/usr/sbin:/sbin`),
  never from ambient `PATH` at call time.
- Scrubbed child environment: explicit minimal `env` mapping
  (`PATH`, `DEBIAN_FRONTEND`, `DEBIAN_PRIORITY`, `LANG=C`, `LC_ALL=C`) —
  never inherit the caller's full environment into privileged subprocesses.
- Atomic state writes: write temp file in same directory → `fsync` →
  `os.replace` → `fsync` directory. `O_NOFOLLOW`/`stat` checks against
  symlink attacks on state/config; enforce ownership/mode at every open.
- Threat model detail in `docs/security/threat-model.md`.

## 10. Reference list

| Topic | Reference | Verified |
|---|---|---|
| os-release format | `os-release(5)`, systemd 257 (manpages.debian.org) | 2026-08-23 |
| apt-get scripting | `apt-get(8)`, apt 3.0.3 (manpages.debian.org) | 2026-08-23 |
| debconf noninteractive | `debconf(7)`, debconf 1.5.91 | 2026-08-23 |
| Ubuntu auto-updates / reboot sentinel / needrestart | ubuntu.com/server/docs Automatic updates | 2026-08-23 |
| systemd timers | `systemd.timer(5)`, systemd 257 | 2026-08-23 |
| DNF reboot detection | dnf-plugins-core `needs-restarting` 4.4.2 docs | 2026-08-23 |
| Python TOML | `tomllib` (CPython 3.11 stdlib docs) | 2026-08-23 |
| os-release from Python | `platform.freedesktop_os_release()` (3.10+) | 2026-08-23 |

Open research items deferred to their phases (none blocking V1):

- Exact `systemd-analyze calendar` validation behaviour on minimal images
  (verify during Phase 4 implementation).
- `loginctl --output=json` availability floor across supported distros
  (verify during Phase 3/4; `who` fallback kept).
- macOS `softwareupdate` catalogue semantics and `launchd` job-result
  reporting (Phase 8, architecture-ready only).
