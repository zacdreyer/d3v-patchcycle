# Test Strategy & TDD Workflow

Status: Approved baseline for V1
Date: 2026-08-23

---

## 1. TDD workflow (mandatory)

For every unit of work:

1. **RED** — write the failing test that encodes the spec behaviour.
2. **GREEN** — implement the minimum to pass.
3. **REFACTOR** — improve structure without changing behaviour; tests stay
   green.
4. **SYNC** — if behaviour changed from spec, update the spec in the same
   commit. Spec drift is a defect.

Tests are written before implementation, never "filled in" afterwards. PR
checklist includes: spec section referenced; tests precede implementation in
history or arrive in the same commit.

## 2. Tooling (dev-only dependencies, justified)

| Tool | Purpose | Justification |
|---|---|---|
| pytest | test runner + fixtures | de-facto standard, fixture model fits dependency injection |
| pytest-mock / unittest.mock | mocking system commands | stdlib mock suffices; pytest-mock optional ergonomics |
| ruff | lint + format | single fast tool replaces flake8/black/isort |
| mypy | static type checking | NFR-06 (type hints) needs enforcement |
| coverage (pytest-cov) | coverage measurement | gate: ≥90% on `src/patchcycle` excluding defensive `sys.platform` branches |

Runtime dependencies remain **zero** (NFR-01).

## 3. Test layers

### L1 — Unit tests (fast, no real maintenance commands)

Seam-based design: all system interaction goes through injectable
collaborators (a `CommandRunner` protocol, filesystem paths injected, clock
injected). Coverage targets:

POSIX ownership/permission tests run as root in disposable CI runners or
containers, using temporary files and fake package managers. Windows developer
tests skip native POSIX cases. Hook/subprocess and local transport tests execute
controlled helpers and loopback servers; no host package update or reboot is permitted.

- OS detection: os-release fixtures for ubuntu/debian/rhel/rocky/alma/fedora/
  suse/alpine/arch/macos/freebsd/unknown; precedence rules; quoting/escapes;
  missing fields; duplicate keys (later wins).
- Provider selection: ID match, ID_LIKE order, unsupported → exit 5 message.
- Config validation: every option (type/range/enum), unknown keys, duration
  grammar, schedule grammar, secret indirection, warning cases (plaintext
  password, TLS-less SMTP with credentials, `take_package` policy).
- State machine: all legal transitions; illegal transitions rejected;
  invariants §7 of state-machine.md; window math (incl. overnight).
- State store: atomic write (fsync/rename called, verified via fakes);
  corrupt file quarantine; schema version gate; symlink/ownership refusal.
- Locking: second instance → exit 3; lock released on simulated crash
  (flock semantics with real file in tmp dir).
- Reboot policy: policy×window×users matrix; boot-id comparison logic.
- Notifications: report rendering (success/failure fixtures from the product
  spec §23 examples), SMTP via a fake server (`smtpd`-style test double),
  webhook via local HTTP test double; delivery-failure semantics (FR-14).
- Health checks: each kind with mocked transport/systemctl; criticality
  rollup.
- Hooks: argv validation, timeout enforcement, failure policies, no-shell
  guarantee (a hook named `$(touch /tmp/pwn)` must execute nothing).
- Command construction (APT): exact argv for each strategy and conffile
  policy; prohibited-flag static assertion (test scans provider source for
  forbidden strings: `--force-yes`, `allow-remove-essential`, etc.).

### L2 — Component tests (real filesystem, fake binaries)

- A `bin/` fixture directory with shell-script fakes for `apt-get`, `dpkg`,
  `systemctl`, `loginctl` that emit recorded outputs and exit codes from a
  scenario file. The provider runs against them with the fixed-path override
  pointed at the fixture dir. This exercises real subprocess plumbing,
  parsing, native POSIX record-lock probing, and exit-code handling without a
  real package manager.
- Reboot simulation fixture: a fake `boot_id` source (injected path) that
  flips value between "pre" and "post" phases drives the full
  REBOOTING→POST_REBOOT→VERIFYING path in-process, without rebooting anything.

### L3 — Provider integration tests (containers, Linux CI only)

Real package managers in throwaway containers:

- Matrix: `ubuntu:22.04`, `ubuntu:24.04`, `debian:12`, `debian:13`,
  `rockylinux:9`, `almalinux:9`, `fedora:44`.
- Scenarios: detect → refresh → list (with a deliberately outdated index,
  assert updates found) → dry-run plan; `dpkg --audit` dirty fixture (unpack a
  .deb then interrupt via `--no-triggers` state manipulation) → preflight
  blocks; lock contention (hold a `fcntl.lockf` record lock on `/var/lib/dpkg/lock-frontend` in
  background) → bounded wait → BLOCKED.
- Real upgrade smoke: in a container, `apt-get install -y --reinstall` an
  older-pinned trivial package then let PatchCycle upgrade it. Runs without
  systemd (provider-level only; systemd paths are tested in L4).
- **CI safety rule:** these run only inside container jobs; the runner host
  is never mutated. A repo-wide CI guard test fails the build if any test
  invokes `apt-get`/`dnf` without the fixture/container marker.

### L4 — Systemd & reboot tests (VM, Phase 7 harness)

- QEMU/KVM VM harness (Debian 13 cloud image + cloud-init), nightly/manual
  and release-gated, never per-PR:
  1. Build and install the wheel, validate generated systemd units, install an
     outdated synthetic package requiring a SUID permission change on upgrade.
  2. Plant reboot-required sentinel; run cycle → real package upgrade → real
     reboot → resume service → POST_REBOOT → verify → notify (webhook to a
     guest-local listener) → COMPLETED. Assert boot ID, package version,
     required file permissions, healthy result and durable archive.
  3. Power-cut test: hard-kill the VM during UPGRADING; relaunch; assert
     FR-S6/FR-S2 recovery reporting and no silent package repair (`POWER_CUT=1`).
- Kernel-update path: where a real kernel update is impractical in CI, the
  `expected_kernel` verification is tested with a fabricated newer installed
  kernel entry via the L2 fake `dpkg-query`.

### L5 — Failure injection (maps 1:1 to failure-recovery.md)

| Scenario | Technique |
|---|---|
| command timeout | fake binary that sleeps past timeout |
| PM non-zero exit | fake binary exit 100 with captured stderr |
| lock timeout | held native PM lock + short lock_timeout |
| malformed PM output | fixture with garbage/empty/localized output |
| corrupted state | truncated/bit-flipped/wrong-schema state.json |
| process interruption | SIGKILL mid-stage between write and action |
| reboot continuation | L2 boot-id flip + L4 real reboot |
| failed health checks | failing service/http fixtures, criticality matrix |
| failed notification | SMTP server that rejects; webhook 500; assert outcome unchanged |

### L6 — Idempotency tests

- `install` twice → identical unit files, no duplicates, config preserved
  (and a modified config is never overwritten; `config.toml` vs
  `config.toml.new` behaviour verified).
- Re-run each state-machine stage with pre-populated state → same result,
  no duplicate transitions, no duplicate notifications.
- `run` with nothing to do twice → two NO_UPDATES cycles, history appended
  (history is append-only by design), state resets cleanly.

### L7 — Security tests (maps to threat-model.md)

T1 hostile package names/hook args; T2 PATH-planted fake binaries (provider
must ignore); T3 hostile env vars (must not reach child); T4 malicious config
(symlinked config, world-writable hook, unknown keys); T5 canary secrets
through every log path; T6 symlinked/wrong-owner state; T13 newline-injection
package names into logs.

## 4. Coverage & quality gates (CI)

- `ruff check` + `ruff format --check` — clean.
- `mypy --strict` on `src/patchcycle` — clean.
- pytest L1+L2 on Python 3.11, 3.12, 3.13, 3.14 — green.
- Coverage ≥ 90% (branch coverage on states.py, engine.py, config.py,
  state_store.py, reboot.py = 100% expectation).
- L3 container matrix on ubuntu/debian images — green.
- pip-audit over the dev environment — no unacknowledged CVEs.
- Package build (`python -m build`) + twine check.
- CI never executes real maintenance commands on the runner (guard test).

## 5. Definition of "tested" for a provider

A provider may register support only when: L1 parsers have fixtures from the
real target OS versions; L3 passes on each claimed OS/container; reboot
detection verified on the real mechanism; and its failure-injection rows pass.
This is the enforcement mechanism for the support-honesty rule (FR-02).
