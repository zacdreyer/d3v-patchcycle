# Update Provider Contract & APT Provider Specification

Status: Approved baseline for V1
Date: 2026-08-23
Implements: FR-01–FR-03, FR-05–FR-08, FR-12

---

## 1. Contract

Every provider implements the abstract `UpdateProvider` interface in
`providers/base.py`. Operation methods return result dataclasses for ordinary
failures. Discovery cannot encode an error as an empty update list: failed
metadata/security/hold probes raise a domain `PreflightError`. Execution/I/O
exceptions are caught by the engine and produce a failed cycle, never success.

```python
class UpdateProvider(ABC):
    name: str  # "apt" | "dnf" | ... (lowercase, stable, logged)

    def preflight(self) -> PreflightResult: ...
    def refresh(self) -> RefreshResult: ...
    def list_updates(self, strategy: Strategy) -> list[UpdateInfo]: ...
    def apply_updates(self, strategy: Strategy,
                      updates: list[UpdateInfo]) -> ApplyResult: ...
    def reboot_required(self) -> RebootStatus: ...
    def verify(self) -> VerifyResult: ...
```

### Shared semantics (binding on all providers)

- **`preflight()`** — verify the package manager exists and is functional,
  check for an unresolved interrupted transaction, and probe PM lock
  availability (non-blocking probe only; waiting is the engine's job, bounded
  by `lock_timeout`). Also report whether a reboot was *already* required
  before this cycle changed anything (`pre_existing_reboot`).
- **`refresh()`** — update package indexes only. Never installs/removes.
  Partial repo failure → `RefreshResult.ok=True` with `warnings[]`; total
  failure → `ok=False`.
- **`list_updates(strategy)`** — pure discovery; MUST NOT alter installed
  state. Returns `UpdateInfo(name, version_from, version_to, security: bool,
  held: bool, requires_reboot_hint: bool)`.
- **`apply_updates(strategy, updates)`** — noninteractive by construction;
  MUST NOT use any provider "force dangerous operation" flag; MUST respect
  held packages (never unhold); MUST capture output for logs; returns
  per-package outcomes and sets `kernel_update`/`expected_kernel` when
  determinable.
- **`reboot_required()`** — native mechanism only (sentinel file, plugin exit
  code, OS API). Returns `RebootStatus(required, reasons[])`. Probe errors are
  conservative: report `required=True, reasons=["probe-error:<detail>"]`
  rather than risking a missed reboot — the reboot policy gate still decides
  whether to act.
- **`verify()`** — PM consistency check + count of outstanding applicable
  updates. Returns `VerifyResult(consistent, detail, outstanding)`.

### Registration & support honesty

- Providers register for explicit `(ID, ID_LIKE)` sets in
  `providers/__init__.py`.
- A provider may be registered only when it has passing provider integration
  tests (test-strategy.md §5). Detection without a registered provider →
  exit 5, no maintenance actions, message:
  `Unsupported operating system: <PRETTY_NAME>. No maintenance actions were performed.`

## 2. APT provider (V1)

Release scope: Debian 12/13 and Ubuntu 22.04/24.04. Unlisted versions and
derivatives require explicit integration-test sign-off before registration.

### Binary resolution & environment

- Binaries resolved once at construction from fixed path list
  (`/usr/bin:/bin:/usr/sbin:/sbin`): `apt-get`, `dpkg`, `dpkg-query`,
  `apt-cache`, `systemctl` (for lock diagnostics only).
- All invocations: argv arrays, `shell=False`, environment scrubbed to:
  `PATH=<fixed>`, `DEBIAN_FRONTEND=noninteractive`, `DEBIAN_PRIORITY=critical`,
  `LANG=C`, `LC_ALL=C`, plus provider-specific `-o` options. `LANG=C`/`LC_ALL=C`
  guarantees parseable English output.

### preflight()

1. Binaries exist; `apt-get --version` runs.
2. `dpkg --audit` → any half-installed/half-configured/triggers-pending
   packages → `ok=False, kind=interrupted-transaction` with package list.
   (Recovery behaviour is policy-gated; see failure-recovery.md FR-S3.
   PatchCycle never repairs unless `[updates].repair_interrupted = true`,
   and then only runs `dpkg --configure -a` — never removals.)
3. Lock probe: non-blocking POSIX record lock (`fcntl.lockf`) on `/var/lib/dpkg/lock-frontend`
   and `/var/lib/apt/lists/lock`. Busy → `ok=False, kind=pm-locked`
   (engine waits/retries with timeout; never kills holders, never deletes
   lock files).
4. Snapshot `pre_existing_reboot = reboot_required().required`.
5. Warn (not fail) if `unattended-upgrades` systemd timers
   (`apt-daily.timer`, `apt-daily-upgrade.timer`) are enabled — potential
   concurrent activity; operations doc recommends operator choice.

### refresh()

- `apt-get update` (no `-y` needed; it does not prompt).
- Exit 0 → ok. Exit 100 with partial repo errors → parse output; if at least
  one repo succeeded → ok with warnings; else fail.
- Timeout: `[package_manager].refresh_timeout` (default 10m).

### list_updates(strategy)

- `apt-get -s --upgrade` (or `--with-new-pkgs` for parity with `apply`)
  simulation output parsing, cross-checked with
  `apt-get -s -V upgrade` for versions. Held-back packages parsed from
  "kept back" lines.
- Security classification: candidate source pocket determined via
  `apt-cache policy <pkg>` (candidate line origin: `*-security` archive).
- Strategy mapping:
  - `security` → only updates whose candidate origin is the security pocket.
  - `safe` (default) → all `upgrade`-able updates (no removals, no new
    installs; held-back reported).
  - `full` → all updates including those requiring new installs/removals
    (maps to `dist-upgrade` at apply time).

### apply_updates(strategy, updates)

Invocation by strategy (all with `-y` and conffile policy options below):

| Strategy | Command form |
|---|---|
| `security` | `apt-get install --only-upgrade <pkg=classified-version…>`; pin the classified security candidate so a later metadata refresh cannot select another version |
| `safe` | `apt-get upgrade` |
| `full` | `apt-get dist-upgrade` |

Mandatory options on every invocation:

```
-o Dpkg::Options::=--force-confdef
-o Dpkg::Options::=--force-confold     # policy keep_existing (default)
# policy take_package: --force-confnew instead of --force-confold (risky; see ADR-0009)
```

Prohibited always: `--allow-remove-essential`, `--allow-downgrades`,
`--allow-change-held-packages`, `--allow-unauthenticated`, `--force-yes`,
`--ignore-hold`.

Behaviour:

- Exit 0 → parse installed/upgraded counts from output; detect kernel update
  (`linux-image-*` in upgraded set → `kernel_update=True`,
  `expected_kernel` = highest installed `linux-image` release from
  `dpkg-query -W 'linux-image-*'`).
- Exit 100 → `ApplyResult.ok=False`, raw tail captured for report.
- Timeout: `[package_manager].upgrade_timeout` (default 60m). On timeout the
  child is **not** killed mid-transaction blindly: PatchCycle sends SIGTERM,
  waits `terminate_grace` (default 2m), then SIGKILL, then records
  `updates_applied=false` and fails with `manual_intervention=true` plus a
  strong recommendation to run `dpkg --configure -a` manually. (Documented
  risk trade-off: a wedged PM cannot be waited on forever; killing is the
  last resort, logged, and never used for lock contention.)
- Heartbeat log every 60s while running.

### reboot_required()

1. `/var/run/reboot-required` exists → required; reasons from
   `/var/run/reboot-required.pkgs`.
2. Kernel check: running release (`platform.release()`) vs newest installed
   `linux-image` release → mismatch ⇒ required, reason `kernel-not-running`.
3. Otherwise not required.

### verify()

- `apt-get check` (exit 0 required).
- `dpkg --audit` must report nothing.
- `list_updates(strategy)` re-run → `outstanding` count (expected 0; held-back
  are listed separately and do not fail verification).

## 3. DNF provider (V1.1)

Release scope: Rocky Linux 9, AlmaLinux 9 and Fedora 44. RHEL, CentOS and
ID_LIKE derivatives require their own passing acceptance gates before registration.

### Critical convention difference from APT

`dnf check-update` exits **100 when updates are available**, 0 when none, 1 on
error — the exact inversion of apt-get's `100 = error`. All exit-code handling
is per-provider; never share exit-code assumptions across providers.

### Binaries & environment

- Binaries resolved from the fixed search path: `dnf`, `rpm`.
- All invocations argv-only, scrubbed env with C locale.

### preflight()

1. `dnf --version` runs.
2. Interrupted transaction probe: `dnf check` exit ≠ 0 →
   `kind=interrupted-transaction` with detail (no auto-repair; `dnf clean
   packages` + `dnf check` manual guidance; PatchCycle never runs
   `package-cleanup` surgery).
3. Every DNF invocation uses `--setopt=exit_on_lock=True`. A native transaction
   lock refusal (exit 200 or an explicit transaction-lock error) during preflight
   becomes `kind=pm-locked`; the engine performs bounded polling and rechecks
   immediately before apply. Native locking remains authoritative during apply.
4. `pre_existing_reboot` via `reboot_required()` snapshot.

### refresh()

- `dnf -y makecache --refresh`. Exit 0 → ok; non-zero → fail with stderr tail.

### list_updates(strategy)

- `dnf --refresh --quiet check-update` — parse the three-column update lines
  (`name.arch  version  repo`), skipping header/blank/obsoletes lines.
- Security classification: `dnf --quiet updateinfo list --available --security`
  marks packages with security advisories; names from that set are
  `security=True`.
- Strategy mapping (semantics differ from APT by design):
  - `security` → only security-classified updates (applied via
    `dnf upgrade --security` at apply time).
  - `safe` (default) → all updates via `dnf upgrade` (DNF upgrade already
    handles dependency changes; there is no "no new packages" mode equivalent
    to apt's plain upgrade — documented difference).
  - `full` → `dnf upgrade` as well (DNF has no dist-upgrade distinction);
    `full` additionally allows `--allowerasing` **only** when explicitly
    configured (`[updates.dnf] allow_erasing = true`, default false) —
    otherwise identical to `safe`.
- Held/excluded packages: dnf `exclude=` entries are respected (the packages
  simply do not appear in check-update output); version-locked packages are
  listed as such by `dnf versionlock list` and reported held.

### apply_updates(strategy, updates)

| Strategy | Command form |
|---|---|
| `security` | `dnf -y upgrade --security` |
| `safe` | `dnf -y upgrade --refresh` |
| `full` | `dnf -y upgrade --refresh` (+ `--allowerasing` iff configured) |

- Exit 0 → parse "Upgraded:" count; kernel update detection: `kernel-core` or
  `kernel` in upgraded set → `kernel_update=True`, `expected_kernel` = newest
  installed `kernel-core` version-release via `rpm -q`.
- Non-zero → `ApplyResult.ok=False` with stderr tail.
- Timeout per `[package_manager].upgrade_timeout`.

### reboot_required()

`dnf needs-restarting -r` (from dnf-plugins-core): exit 1 = required, 0 = not.
Probe error → conservative `required=True, reasons=["probe-error:..."]`. If the
plugin is missing (exit non-zero with "No such command"), fall back to the
kernel-not-running comparison (running vs newest installed `kernel-core`).

### verify()

- `dnf check` must exit 0.
- Re-run `check-update` → outstanding count (expected 0).

## 4. Future providers (architecture-ready only)

- **Zypper** (SUSE/openSUSE): `zypper --non-interactive`, `zypper ps` /
  `needs-rebooting` probes.
- **APK** (Alpine): `apk upgrade`; reboot detection is kernel-based (no
  sentinel); OpenRC/scheduling differences noted.
- **Pacman** (Arch): `pacman -Syu --noconfirm`; partial-upgrade risks must be
  addressed in its own ADR before registration.
- **macOS**: two distinct providers — `softwareupdate` (OS updates; report
  separately) and `brew` (third-party; runs unprivileged as the brew owner,
  never as root). Scheduling via launchd.

Each future provider requires: provider spec section update, ADR if semantics
diverge, unit tests, container/VM integration tests, and operations doc
updates — before registration.
