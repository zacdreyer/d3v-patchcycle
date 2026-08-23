# ADR-0008: boot-id reboot verification with an always-on resume unit

Status: Accepted — 2026-08-23

## Context

The application must never assume `reboot` succeeded. It needs proof a real
reboot happened, and a guaranteed continuation path after boot.

## Decision

- **Proof of reboot:** record `/proc/sys/kernel/random/boot_id` as
  `boot_id_before` in persisted state *before* issuing the reboot; after
  boot, resume proceeds only if the current boot_id differs (state-machine
  invariant 2). Same boot_id → the reboot did not happen → bounded retry then
  FAILED (FR-S7).
- **Kernel verification:** when a kernel package was updated, record
  `expected_kernel` (newest installed release) and require
  `platform.release()` to match after reboot; mismatch → FAILED
  `kernel-mismatch` (FR-S13), never bootloader surgery.
- **Continuation path:** an always-enabled `d3v-patchcycle-resume.service`
  that no-ops when no cycle is pending. Before entering REBOOTING the engine
  verifies the unit exists and is enabled; if not, it refuses to reboot
  (FR-S15).
- No cron `@reboot`, no dynamically generated/toggled units.

## Alternatives considered

- **Uptime comparison:** weaker — an unexpected reboot also resets uptime and
  would masquerade as success. boot_id distinguishes "the reboot I ordered"
  from "something happened" (combined with recorded timestamps; FR-S6 covers
  the unexpected case).
- **Enable resume unit only when needed:** rejected — dynamically toggling
  unit state before a reboot is an extra failure point; a static always-on
  no-op unit is simpler and auditable.
- **systemd `ConditionNeedsUpdate`-style mechanisms / kexec:** out of scope;
  kexec bypasses firmware and complicates "real reboot" semantics — not used.

## Consequences

- Reboot verification is deterministic and testable both in-process (boot-id
  fixture flip) and in real VMs (L4 tests).
- Power loss during reboot (FR-S5) is indistinguishable from a clean reboot
  and therefore handled by the same verified path — by design.
