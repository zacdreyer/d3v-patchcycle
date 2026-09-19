# Production readiness checklist

Updated: 2026-09-19. Candidate: **1.0.0rc2 / state schema 2**.
Baseline: `1c98c9e`; branch: `release/1.0.0rc2-readiness`.
**Local and live GitHub engineering gates passed; deployment-target acceptance is pending.**

CI follow-up, 2026-09-19: the September 8 rc2 CI and release runs failed
11 Linux hook/command-health tests because GitHub's tool-cache interpreter
has untrusted parent directories. A validated system-Python test fixture
removes that environment assumption without weakening production checks.
The same 11 failures were reproduced with non-root-owned interpreter ancestry
in a disposable container; the corrected Python 3.13 suite passed 602 tests
at 91.63% coverage. Ruff, formatting, strict mypy, CI guard and documentation
links passed. On commit `f3dbbef`, all 17 [CI jobs](https://github.com/zacdreyer/d3v-patchcycle/actions/runs/35463221859)
passed, and the [release gates](https://github.com/zacdreyer/d3v-patchcycle/actions/runs/35463221935)
passed through both real-VM scenarios (normal reboot and power loss during
upgrade). Publishing was skipped because this was a release-branch push.

This is the active work queue. The [audit](code-audit.md) preserves original
findings and reproductions; historical phase completion is not release proof.

## Implemented and exercised

- [x] Review every production module and record the initial 25 grouped findings.
- [x] Persist boot facts and retry counts before reboot; handle asynchronous
  shutdown, missing IDs, stuck retries and interrupted post-boot verification.
- [x] Implement reboot-first, logged-in-user wait, maintenance-window rechecks,
  opt-in APT repair, disk preflight and conservative unexpected/stale recovery.
- [x] Protect config/state/log/lock files and ancestors; use exclusive atomic
  writes, strict state types, durable quarantine refusal and schema-2 migration.
- [x] Fix fresh/custom-config installation, real systemd validation, boot order,
  command failures, manual scheduling and lifecycle locks/refusal while pending.
- [x] Finalize outcomes before delivery, persist text reports, preserve warnings,
  expose notification failures, count held packages and fail outstanding updates.
- [x] Use verified SMTP STARTTLS; refuse webhook redirects/proxies; redact
  credentials from exceptions, logs, persisted errors and health details.
- [x] Use native dpkg record locks, DNF lock refusal/versionlocks and native kernel
  version comparisons. Refuse failed audit/security probes as unknown state.
- [x] Terminate timed-out POSIX subprocess groups; use that wrapper for hooks and
  command health checks. Bound service probes using their configured timeout.
- [x] Restrict built-in registration to Ubuntu 22.04/24.04, Debian 12/13,
  Rocky/AlmaLinux 9 and Fedora 44. No implicit derivative/version support.
- [x] Replace permissive L3 tests with deterministic package upgrades and archive
  assertions; add native lock, dirty APT repair and DNF versionlock cases.
- [x] Repair wheel-installed L4 harness, local webhook sink, cloud-init setup,
  SSH session policy, bounded waits and retained evidence without private keys.
- [x] Prove actual package upgrade under systemd, required SUID restoration,
  real reboot, automatic resume, boot-ID verification, health and notification.
- [x] Fix artifact-only checksums and validate wheel/sdist/installable ZIP content.
- [x] Pass final Linux Python 3.11/3.12/3.13/3.14: 601 tests each, 91.58% coverage.
  Windows 3.13: 587 passed, 14 POSIX skips, 90.59%.

## Remaining engineering and evidence closure

- [x] **VAL-01 — Final L3 run:** all seven distributions passed (11 tests).
  Final strategy fixtures separately passed all four APT platforms (8 tests)
  and all three DNF platforms (3 tests), including security/full upgrades,
  native holds/versionlocks, APT record locks and interrupted repair.
- [x] **VAL-02 — VM power loss:** normal reboot and POWER_CUT=1 passed from
  the final production source. Interrupted postinst reported unexpected-reboot
  at UPGRADING, archived the failure, and did not silently repair dpkg. Both
  scenarios proved persisted webhook success, JSON/text history, idle state,
  idempotent install and uninstall/reinstall with history preserved.
- [x] **VAL-03 — Final source checks:** final test/static gates passed; wheel,
  sdist and portable ZIP built, validated and copied to dist/ with SHA256SUMS.
  Clean wheel installation reports rc2/schema2. Exact third-party dependency
  audit found no known vulnerabilities. See the evidence index for source hashes.
- [x] **AUD-02 — Detailed requirement closure:** audit contains requirement and
  configuration traceability, advisory estimate semantics, schema corrections,
  native strategy proof and explicit limitations.
- [x] **DOC-01 — SDD/TDD and operator reconciliation:** synchronize schema,
  provider contract, lock semantics, supported scope, timeout bounds and purge
  safeguards. Add the target-host deployment acceptance procedure; validate
  operator lifecycle commands in both wheel-installed VM scenarios.

## External acceptance before stable deployment

- [ ] **OPS-01 — Deployment-target staging:** identify the first distribution,
  version and representative disposable staging host(s). Exercise the intended
  schedules, real application health probes and actual notification destination
  across maintenance/reboot cycles. The synthetic Debian VM is not fleet acceptance.
- [x] **REL-01 — Commit and live CI:** commit `f3dbbef` passed repository CI
  (Python 3.11–3.14 on Linux and Windows, seven container targets, package
  build and quality checks) and the release workflow through both L4 VM gates.
  Run URLs are recorded above; the release run retains L4 evidence artifacts.
  Publication still requires a tag and separate stable-release acceptance.
- [ ] **REL-02 — Stable release:** after engineering and staging gates close,
  reconcile version/changelog/memory and authorize the v1.0.0 tag/publication.
  No release has been published or deployment performed in this session.

Future Zypper/APK/Pacman/macOS providers and unlisted Linux derivatives are outside
this release scope. Preserve the pre-existing untracked `repro-ci.sh`; real
package-manager/reboot fixtures run only in explicitly disposable containers/VMs.
