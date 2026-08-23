# Failure & Recovery Specification

Status: Approved baseline for V1
Date: 2026-08-23
Governing principle: **report truthfully, recover conservatively, never
force.** Every scenario below has automated tests (test-strategy.md §6).

Recovery entry points:

- `d3v-patchcycle run` — detects a non-terminal persisted cycle from the
  *same boot* and offers governed recovery instead of starting a second cycle.
- `d3v-patchcycle resume` — invoked by the boot-resume service; handles
  cross-boot continuation.

---

## Scenario catalogue

### FR-S1 — Crash during REFRESHING / DISCOVERING_UPDATES (same boot)

- **Detection:** resume/run finds non-terminal state ≤ `DISCOVERING_UPDATES`,
  same `boot_id`, no live process (flock free).
- **Facts:** no system mutation beyond index refresh has occurred; refresh is
  idempotent.
- **Recovery:** restart the cycle cleanly from `PRECHECK` with the **same
  `run_id`**, appending a `recovery` note to `transitions`. No notification
  storm: recovery restart is logged, not emailed, unless it subsequently
  fails.

### FR-S2 — Crash/kill during UPGRADING (same boot)

- **Detection:** state == `UPGRADING`, `updates_applied == false`, same boot,
  no live process, apt/dpkg locks free.
- **Recovery steps:**
  1. Provider preflight: `dpkg --audit`.
  2. Audit clean → the PM had not committed work (or finished between state
     write and crash): re-enter `UPGRADING` (apt operations are idempotent;
     already-upgraded packages are simply no-ops).
  3. Audit dirty (half-configured etc.) → **stop**.
     - `repair_interrupted = true` → run `dpkg --configure -a`, re-audit;
       clean → re-enter `UPGRADING`; still dirty → FAILED.
     - default (`false`) → FAILED with `kind=interrupted-transaction`,
       `manual_intervention=true`, report includes the `dpkg --audit` output
       and the exact safe command an admin can run.
- **Never:** kill apt/dpkg processes, remove lock files, `--remove` packages,
  or pass force flags to "fix" the state.

### FR-S3 — Package manager fails mid-upgrade

- `apply_updates` returns not-ok (exit 100, timeout, malformed output).
- State → `FAILED` at stage `UPGRADING`; capture stderr tail + `dpkg --audit`
  snapshot into the report. If audit is dirty, apply FR-S2 step 3 policy
  (default: report only). Notify immediately.
- The next scheduled/manual `run` sees the FAILED cycle archived and starts
  fresh; preflight will catch a still-dirty dpkg before doing anything.

### FR-S4 — Power loss / crash at any non-reboot state

- Equivalent to FR-S1/S2 depending on persisted state, discovered by the next
  `run` (timer or admin) since the boot-resume service also fires after an
  unplanned power-cycle boot. If the boot changed while state < `REBOOTING`,
  treat as **unexpected reboot**: FR-S6.

### FR-S5 — Host loses power *during* reboot step

- State == `REBOOTING` with `boot_id_before` recorded. After power restore,
  resume service runs. If boot_id changed → normal `POST_REBOOT` path —
  indistinguishable from a clean reboot, by design (that is exactly why facts
  are persisted before the reboot command). `POST_REBOOT`/`VERIFYING`
  determine actual system health; partial upgrade damage surfaces at
  `VERIFYING` (PM consistency) and is reported.

### FR-S6 — Unexpected reboot mid-cycle (state < REBOOTING, boot changed)

- **Detection:** resume service finds non-terminal state earlier than
  `REBOOTING`, but `boot_id` ≠ any recorded `boot_id_before` and ≠ boot id
  at state creation.
- **Meaning:** the machine rebooted underneath an in-progress cycle (power,
  kernel panic, admin action). Package state is unknown.
- **Recovery:** run provider preflight + `verify()` to establish ground
  truth; then FAILED with `kind=unexpected-reboot`, `manual_intervention`
  per verify result, full report (what stage was reached, whether PM is
  consistent). Never silently restart the upgrade in this path — the
  administrator is told first. Next `run` starts a fresh cycle normally.

### FR-S7 — Reboot command issued but reboot did not happen

- **Detection:** `d3v-patchcycle resume` runs (manually, or by admin) with
  state == `REBOOTING` and current boot_id == `boot_id_before`.
- **Meaning:** `systemctl reboot` returned success but the machine never went
  down (or the resume service is being run on the same boot).
- **Recovery:** retry budget persisted in state (`reboot_attempts`, max 3,
  30s apart); on exhaustion → FAILED `kind=reboot-failed`,
  `manual_intervention=true`, notify. Also covers "reboot hangs": if
  `REBOOTING` persists with same boot beyond `reboot_stuck_timeout`
  (default 10m from `reboot_initiated_at`), same failure path.

### FR-S8 — Corrupt state file

- **Detection:** JSON parse failure or schema violation on load.
- **Recovery:** the corrupt file is **never deleted or overwritten in
  place**. It is quarantined to `state.json.corrupt.<timestamp>` (mode 0600),
  and behaviour depends on evidence:
  - A valid `.tmp` sibling or history entry proves a completed cycle →
    reset to IDLE, log loudly.
  - Otherwise → treat as an unknown in-flight cycle: run read-only provider
    diagnostics (`dpkg --audit`, reboot sentinel) and exit FAILED
    (`kind=state-corrupt`, `manual_intervention=true`, notify) **without
    starting maintenance**. Starting an upgrade with unknown prior state is
    the unsafe guess this product exists to avoid.
- Symlinked/wrong-owner/wrong-mode state file → same "refuse and report"
  path (also a security signal; threat-model T6).

### FR-S9 — Stale state (cycle older than N days)

- A non-terminal cycle untouched for > `stale_after` (default 7 days) is
  escalated at next `run`: diagnostic preflight + FAILED
  (`kind=stale-cycle`, notify) rather than blind continuation. Continuing a
  week-old `UPGRADING` is not resumption, it's guessing.

### FR-S10 — Package manager remains locked past `lock_timeout`

- Cycle → FAILED/`BLOCKED` (`kind=pm-locked`, exit 2), notify with the list
  of processes holding locks (`fuser`/`lsof` output where available).
  PatchCycle never kills them. Common cause documented: Ubuntu
  `apt-daily*.timer` catch-up at boot (operations.md recommends an explicit
  operator decision about unattended-upgrades coexistence).

### FR-S11 — Notification delivery fails

- Retry per notifier: 3 attempts, exponential backoff (5s/30s/2m), then
  record `notification_status: {<notifier>: failed}` in state + history, log
  at ERROR. Cycle outcome is unchanged (a good upgrade stays SUCCESS; the
  report body is also written to
  `/var/lib/d3v-patchcycle/history/<run_id>.report.txt` so nothing is lost).
- If *all* notifiers fail, exit code gains no extra failure — but `status`
  shows `notification_status: failed` prominently.

### FR-S12 — Health check fails after successful update/reboot

- Cycle reaches `NOTIFYING` regardless; outcome FAILED (critical check) or
  SUCCESS_WITH_WARNINGS (non-critical). Report separates "package update:
  OK" from "health: FAILED (<check>)" — these are never conflated.
- No automated service restarts or remediation by PatchCycle itself;
  remediation belongs to hooks/admin.

### FR-S13 — Kernel verification fails after reboot

- `expected_kernel` set but `kernel_after` differs → FAILED
  (`kind=kernel-mismatch`, `manual_intervention=true`). Typical causes:
  bootloader pinned, /boot full, kexec. PatchCycle does not touch the
  bootloader; it reports.

### FR-S14 — Concurrent execution attempts

- flock held → second instance exits 3 immediately with a message identifying
  the holding PID (read from lock file metadata, best-effort). Timers firing
  during an active service are structurally absorbed by systemd.

### FR-S15 — Resume unit missing at reboot time

- `REBOOTING` entry refuses to issue the reboot if the resume unit is absent/
  disabled → FAILED (`kind=resume-path-missing`) *before* any reboot. This is
  a fail-safe: a maintenance tool must never strand a cycle across a boot.

## Recovery principles summary

1. Ground truth comes from the OS (`dpkg --audit`, sentinels, boot id), not
   from optimism.
2. Re-execution is preferred over repair; repair (`dpkg --configure -a`
   only) is opt-in; destructive repair is absent by design.
3. Unknown provenance (corrupt state, unexpected reboot, stale cycle)
   always resolves to *report and stop*, never *continue and hope*.
4. Every recovery path appends to `transitions` and is covered by failure-
   injection tests.
