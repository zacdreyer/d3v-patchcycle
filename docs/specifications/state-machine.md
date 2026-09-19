# State Machine Specification

Status: Approved baseline for V1
Date: 2026-08-23
Implements: FR-04, FR-08–FR-11 (product-specification.md)

---

## 1. Principles

Readiness clarification (2026-09-08): `existing_pending=reboot_first` means
PRECHECK → REBOOT_PENDING before refresh/apply, then POST_REBOOT → PRECHECK
after verified boot. Persist a `reboot_before_updates` marker for that branch;
clear it before restarting precheck. If reboot policy prohibits the initial
reboot, report manual reboot required without applying updates. This supersedes
the contradictory update-first wording in §4.5. A queued reboot on the original
boot leaves REBOOTING pending and returns; only boot-ID change permits POST_REBOOT.
Reboot timestamps and attempt counts must be durable before issuing the command.

1. The state machine is **persistent**: the current state and all data needed
   to resume are written to `/var/lib/d3v-patchcycle/state.json`
   **atomically before** the state's action executes. If the process dies at
   any instant, the persisted state is the last state whose action had not
   provably completed.
2. **Idempotent stages:** every stage's action is safe to re-execute, and the
   recovery path (failure-recovery.md) defines re-entry behaviour per state.
3. **No silent skipping:** resumption continues at the correct state;
   completed actions are not repeated (guarded by recorded facts, e.g.
   `updates_applied: true`).
4. Every non-terminal state may transition to `FAILED`, **except**
   `NOTIFYING`: delivery failure is recorded in `notification_status` and
   the cycle completes (§4.11), because delivery failure must not rewrite
   maintenance truth. `FAILED` and `COMPLETED` are terminal for a cycle; a
   new `run` starts a new cycle with a new `run_id`.

## 2. States

| State | Terminal? | Description |
|---|---|---|
| `IDLE` | (rest) | No active cycle. Entry point for `run`. |
| `PRECHECK` | no | Validate privileges, OS/provider, config, storage, PM health/locks, disk, notifications, window. |
| `REFRESHING` | no | Refresh package indexes. |
| `DISCOVERING_UPDATES` | no | Enumerate applicable updates per strategy. |
| `UPGRADING` | no | Apply updates unattended. |
| `CHECKING_REBOOT` | no | Probe reboot requirement; apply existing-pending policy. |
| `REBOOT_PENDING` | no | Decision point: reboot now per policy, defer, or notify-only. |
| `REBOOTING` | no | Persist final pre-reboot facts; run pre-reboot hooks; issue reboot. |
| `POST_REBOOT` | no | (After boot) verify genuine reboot + kernel transition. |
| `VERIFYING` | no | Provider consistency verification + outstanding-update count. |
| `HEALTH_CHECKING` | no | Run configured health checks. |
| `NOTIFYING` | no | Deliver final report. |
| `COMPLETED` | yes | Cycle archived to history; state returns to IDLE. |
| `FAILED` | yes | Cycle archived to history with error model; admin notified if possible. |

## 3. Transition table

| From | On success | On failure / condition |
|---|---|---|
| `IDLE` | `PRECHECK` (new cycle started) | — |
| `PRECHECK` | `REFRESHING` | `FAILED` (blocked: exit 2/4/5, `manual_intervention` per cause) |
| `REFRESHING` | `DISCOVERING_UPDATES`; recovery arc → `PRECHECK` (FR-S1) | `FAILED` (PmFailed / PmLocked after timeout) |
| `DISCOVERING_UPDATES` | `UPGRADING` if updates exist; else `CHECKING_REBOOT` (still must evaluate pre-existing pending reboot); recovery arc → `PRECHECK` (FR-S1) | `FAILED` |
| `UPGRADING` | `CHECKING_REBOOT`; recovery arc → `PRECHECK` (FR-S2, gated by provider preflight in PRECHECK) | `FAILED` (package-manager failure; state records partial facts) |
| `CHECKING_REBOOT` | `REBOOT_PENDING` if reboot required; else `VERIFYING` | `FAILED` (probe error — treated conservatively) |
| `REBOOT_PENDING` | `REBOOTING` if policy allows now; `VERIFYING` if `notify_only`/`never` (result flagged `MANUAL_REBOOT_REQUIRED`, exit 6); `FAILED` if outside allowed window and policy requires window (blocked) | `FAILED` |
| `REBOOTING` | *(process terminates with the machine)* → after boot, resume enters `POST_REBOOT` | `FAILED` if reboot command failed and retry budget exhausted |
| `POST_REBOOT` | `VERIFYING` | `FAILED` (no genuine reboot detected / kernel verification failed) |
| `VERIFYING` | `HEALTH_CHECKING` | `FAILED` (PM inconsistent, unexpected outstanding state) |
| `HEALTH_CHECKING` | `NOTIFYING` | `NOTIFYING` (health failures are **reported**, not hidden; outcome becomes FAILED or SUCCESS_WITH_WARNINGS per check criticality) |
| `NOTIFYING` | `COMPLETED` | `COMPLETED` with `notification_status: failed` (delivery failure never rewrites maintenance truth) |
| `FAILED` | — | archived; notifier attempted from a dedicated minimal path |
| `COMPLETED` | — | archived; state → IDLE |

Resume entry points (from `d3v-patchcycle resume` after boot):

| Persisted state | boot_id changed? | Resume at |
|---|---|---|
| `REBOOTING` | yes | `POST_REBOOT` |
| `REBOOTING` | no | failure path FR-S7 (reboot did not happen) |
| `POST_REBOOT`/`VERIFYING`/`HEALTH_CHECKING`/`NOTIFYING` | yes | re-enter that state (idempotent) |
| `PRECHECK`..`REBOOT_PENDING` | no (same boot) | crash recovery per failure-recovery.md FR-S1..S4 |
| any non-terminal | boot changed but state < `REBOOTING` | unexpected reboot → failure path FR-S6 |

## 4. Per-state specification

### 4.1 `PRECHECK`

- **Entry:** new cycle requested (`run`) or schedule fired.
- **Actions:** require effective uid 0; detect OS; select provider; validate
  config (already loaded; re-assert); verify state dir writable; provider
  `preflight()` (PM healthy, no unresolved interrupted dpkg transaction, PM
  lock obtainable within `lock_timeout`); disk space thresholds
  (`/`: default 512 MiB free, `/boot`: 128 MiB where separate); notifier
  config validates (no delivery attempt); maintenance window sanity.
- **Persists:** `os` identity snapshot, `provider`, `started_at`, config hash.
- **Success →** `REFRESHING`. **Failure →** `FAILED` with
  `outcome=BLOCKED`, no update action taken, notify attempted.

### 4.2 `REFRESHING`

- **Actions:** provider `refresh()` (e.g. `apt-get update`).
- **Persists:** `indexes_refreshed_at`, refresh warnings (failed repos are
  warnings unless all fail).
- **Success →** `DISCOVERING_UPDATES`. **Failure →** `FAILED` (PmFailed).

### 4.3 `DISCOVERING_UPDATES`

- **Actions:** provider `list_updates()`; classify by strategy (security /
  safe / full per provider mapping); record package list with from→to
  versions; record held-back packages.
- **Persists:** `updates_available[]`, `updates_applicable[]`,
  `packages_pending`, `held_back[]`.
- **Success →** `UPGRADING` (if applicable updates) else `CHECKING_REBOOT`.

### 4.4 `UPGRADING`

- **Actions:** provider `apply_updates()` with the strategy and noninteractive
  policy (confdef/confold default — ADR-0009). Long-running: heartbeat log
  every 60s. The stage records `updates_applied: true` in state **only after**
  the package manager exits successfully.
- **Persists:** `packages_updated`, `updates_applied`, per-package results,
  `kernel_update: bool`, `expected_kernel` (newest installed kernel release if
  a kernel package was updated).
- **Success →** `CHECKING_REBOOT`. **Failure →** `FAILED` (PmFailed,
  `manual_intervention: true`).

### 4.5 `CHECKING_REBOOT`

- **Actions:** provider `reboot_required()`; capture `reboot_required`,
  `reboot_reasons[]` (sentinel pkgs, kernel ABI change, needs-restarting).
  Evaluate **existing pending reboot**: if reboot was already required
  *before* this cycle changed anything (detected at PRECHECK snapshot vs.
  now), apply `[reboot].existing_pending` policy:
  - `reboot_first` (default): if the pre-existing requirement still stands and
    updates were applicable, still apply updates first unless
    `continue_then_reboot` semantics are overridden — see §4.6 note; the
    reboot then satisfies both. If no updates applied and reboot still
    required → proceed to reboot per policy.
  - `continue_then_reboot`: apply updates, reboot once at the end.
  - `report_only`: do not reboot for a pre-existing requirement; finish the
    cycle and report `pre_existing_reboot_pending: true`. (This policy only
    downgrades *reboot* behaviour, never update behaviour.)
- **Persists:** `reboot_required`, `reboot_reasons`, `pre_existing_reboot`.
- **Success →** `REBOOT_PENDING` or `VERIFYING`.

### 4.6 `REBOOT_PENDING` (decision point, no system mutation)

- **Actions:** evaluate `[reboot].policy`:
  - `never` → continue to `VERIFYING`, outcome flagged
    `MANUAL_REBOOT_REQUIRED`, exit 6.
  - `notify_only` → same, with notification emphasising required reboot.
  - `always` → reboot even if not required (used rarely; still obeys
    users-logged-in and window rules).
  - `when_required` (default) → reboot iff `reboot_required`.
  Gate checks for actually rebooting: `allow_if_users_logged_in=false`
  (default) blocks while sessions exist (re-evaluated with
  `user_wait_timeout`, default 30m); maintenance-window rule
  (§ window.md semantics in configuration.md): reboot allowed if inside window
  or no window configured. If blocked by users/window → `FAILED`
  (`outcome=BLOCKED`, `manual_intervention: true`, notify).
- **Success →** `REBOOTING` | `VERIFYING`.

### 4.7 `REBOOTING`

- **Actions:** record `boot_id_before`, `kernel_before`, `expected_kernel`,
  `reboot_initiated_at`; verify resume unit enabled (fail-safe: refuse to
  reboot if resume path missing); run `before_reboot` hooks (failure policy
  applies; default abort); flush logs; sync; `systemctl reboot`.
- **Persists:** all of the above **before** issuing the reboot.
- **Success:** machine goes down; resume service enters `POST_REBOOT`.
- **Failure:** reboot command errors → bounded retry (3 attempts, 30s apart)
  → `FAILED` (RebootFailed, `manual_intervention: true`).

### 4.8 `POST_REBOOT`

- **Actions:** compare current `boot_id` with `boot_id_before` (must differ);
  record `kernel_after`; if `expected_kernel` set and `kernel_after` ≠
  expected → `FAILED` (kernel verification failed — new kernel installed but
  not running; report, no destructive action); verify uptime sanity.
- **Success →** `VERIFYING`.

### 4.9 `VERIFYING`

- **Actions:** provider `verify()`: package-manager consistency
  (`apt-get check`, `dpkg --audit` clean), outstanding applicable updates
  count (expected 0; held-back packages are reported, not failures).
- **Persists:** `verify_passed`, `outstanding_updates`.
- **Success →** `HEALTH_CHECKING`. **Failure →** `FAILED` (VerifyFailed).

### 4.10 `HEALTH_CHECKING`

- **Actions:** run configured checks (service/http/tcp/command, plus implicit
  failed-units check on systemd). Each check: name, kind, result, detail,
  duration. Critical check failure → cycle outcome FAILED (after notify).
  Non-critical failure → SUCCESS_WITH_WARNINGS.
- **Success →** `NOTIFYING` either way; failures carried in the report.

### 4.11 `NOTIFYING`

- **Actions:** render report; deliver to all enabled notifiers; record
  per-notifier `notification_status`.
- **Success →** `COMPLETED`. **Failure →** `COMPLETED` with
  `notification_status: failed` (logged loudly).

### 4.12 `COMPLETED` / `FAILED`

- **Actions:** finalize outcome, archive state to
  `history/<run_id>.json`, reset `state.json` to IDLE, emit final log record,
  set process exit code.

## 5. Maintenance-window semantics

- Window configured as `start`/`end` local time; `start > end` means
  overnight (e.g. 23:00–04:00).
- **Before** `UPGRADING` and before `REBOOTING`, the engine checks that the
  estimated time to finish the disruptive action fits in the remaining window
  (estimates: refresh+discover 5m; upgrade: max(10m, 30s/package); reboot +
  verify: 15m; configured via `[maintenance.estimates]`). Refresh is advisory
  in the dry-run total; after discovery the upgrade gate uses the remaining
  upgrade estimate, and the reboot gate uses reboot+verify. If not, the
  cycle stops cleanly **before** the disruptive action with
  `outcome=BLOCKED, reason=window` and notifies; the next scheduled run
  retries.
- If the window **expires mid-upgrade**: the running package transaction is
  always allowed to complete (never interrupt dpkg/apt mid-transaction);
  subsequent disruptive steps (reboot) are deferred per the same rule, and the
  state records a blocked outcome and explicit window reason. Verification, health checks and
  notification are **not** window-restricted — they are non-disruptive.
- Principle: the clock never justifies leaving the system knowingly
  inconsistent; it only gates *starting* disruptive actions.

## 6. Persisted state schema (version 2; ADR-0010)

`/var/lib/d3v-patchcycle/state.json` — mode 0600, root:root, atomic writes.

```json
{
  "schema_version": 2,
  "run_id": "018f2c3a-example-uuid4",
  "state": "REBOOTING",
  "outcome": null,
  "started_at": "2026-08-23T02:00:01Z",
  "updated_at": "2026-08-23T02:14:55Z",
  "host": {"hostname": "web01.example.com"},
  "os": {"id": "ubuntu", "version_id": "24.04", "pretty_name": "Ubuntu 24.04 LTS",
         "codename": "noble", "arch": "x86_64", "init": "systemd"},
  "provider": "apt",
  "updates_available": [{"name": "libc6", "version_from": "2.39-0ubuntu8.3", "version_to": "2.39-0ubuntu8.4", "security": true, "held": false}],
  "packages_pending": 27,
  "packages_updated": 0,
  "updates_applied": false,
  "kernel_update": false,
  "expected_kernel": null,
  "reboot_required": false,
  "reboot_reasons": [],
  "pre_existing_reboot": false,
  "boot_id_before": null,
  "boot_id_after": null,
  "kernel_before": "6.8.0-55-generic",
  "kernel_after": null,
  "reboot_initiated_at": null,
  "reboot_attempts": 0,
  "reboot_before_updates": false,
  "initial_reboot_completed": false,
  "verify_passed": null,
  "outstanding_updates": null,
  "health_results": [],
  "warnings": [],
  "notification_status": {},
  "error": null,
  "transitions": [{"from": "IDLE", "to": "PRECHECK", "at": "…"}]
}
```

Rules:

- `schema_version` gates migration; unknown/newer versions → refuse to mutate,
  exit 4 with a clear message (no silent downgrade of state).
- `error` object: `{kind, message, stage, manual_intervention}`.
- `transitions` is the authoritative audit trail of the cycle (also mirrored
  to structured logs).
- History files share the schema with final `outcome`; `updated_at` is the
  terminal transition timestamp. Held records remain in `updates_available`.
  Earlier draft-only config-hash/index-timestamp fields are not emitted.

## 7. Invariants (asserted in code and tested)

1. A reboot command may only be issued after boot identity, timestamp and
   attempt count are persisted successfully.
2. `POST_REBOOT` requires `boot_id_before != current boot_id`.
3. `updates_applied == true` ⇒ `UPGRADING` is never re-executed on resume.
4. No state transition occurs without a preceding successful atomic write.
5. Terminal states are written to history before `state.json` resets to IDLE.
6. A cycle in a terminal state can never be resumed — only inspected.
