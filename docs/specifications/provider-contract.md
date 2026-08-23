# Update Provider Contract & APT Provider Specification

Status: Approved baseline for V1
Date: 2026-08-23
Implements: FR-01–FR-03, FR-05–FR-08, FR-12

---

## 1. Contract

Every provider implements this interface (Python `Protocol`, in
`providers/base.py`). All methods return result dataclasses; they never raise
for *expected* operational failures (locks busy, PM error exit) — those are
encoded in results. Exceptions indicate programmer error or unexpected I/O
failure.

```python
class UpdateProvider(Protocol):
    name: str  # "apt" | "dnf" | ... (lowercase, stable, logged)

    @staticmethod
    def supports(os_id: str, id_like: list[str]) -> bool: ...
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

Applies to: `ID=debian`, `ID=ubuntu`, and any `ID_LIKE` containing `debian`
**only after** explicit integration-test sign-off for that derivative.

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
3. Lock probe: non-blocking `flock` attempt on `/var/lib/dpkg/lock-frontend`
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
| `security` | `apt-get install --only-upgrade <pkg…>` for security-classified updates only |
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

## 3. DNF provider (V1.1 design notes — not implemented in V1)

- Selection: `ID in {rhel, rocky, almalinux, fedora}` or `ID_LIKE` containing
  `rhel`/`fedora` (after integration tests).
- refresh: `dnf makecache --refresh` (or rely on `dnf upgrade --refresh`).
- list: `dnf --refresh check-update` (exit 100 = updates available — note the
  inverted convention vs apt-get's 100=error; parsers must be per-provider).
- apply: `dnf -y upgrade [--security] [--refresh]`;
  `safe`/`full` both map to `upgrade` (DNF semantics differ from APT; document
  the mapping at implementation time; do not assume equivalence).
- reboot probe: `dnf needs-restarting -r` (exit 1 = required).
- verify: `dnf check` + re-run check-update.

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
