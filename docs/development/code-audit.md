# Production readiness audit

Baseline: `1c98c9e`, 2026-09-08. Status: active; release blocked.

## Current execution evidence

Every production module, the SDD/TDD set, original nine ADRs, workflows and
acceptance harnesses received source review. Baseline was 450 Windows tests,
90.99% coverage. The current candidate is 1.0.0rc2/schema2, with ADR-0010 documenting
migration. No live GitHub CI, stable release or deployment has been claimed.

- Linux Python 3.11/3.12/3.13/3.14: 601 passed each, 91.58% coverage.
- Windows Python 3.13: 587 passed, 14 POSIX skips, 90.59% coverage.
  Python coverage tracer used; no socket hang reproduced.
- Ruff, strict mypy, CI guard, local Markdown links and workflow YAML passed.
- Final L3: 11 tests across all seven supported platforms. Separate final
  strategy runs: APT 8 passed / DNF 3 passed, including real security/full
  upgrades, native holds/versionlocks, APT locks and interrupted repair.
- Final wheel-installed Debian 13 normal cycle upgraded a synthetic package,
  restored SUID mode, rebooted/resumed, verified boot ID and health, and notified.
  A second guest lost power during postinst and correctly failed at UPGRADING
  without silent repair; outstanding updates remained unknown, health not run.
- Both guests proved durable JSON/text archive, persisted delivery success,
  idle status, idempotent install and history-preserving uninstall/reinstall.
- See the [evidence index](evidence/README.md) for final delivery-* runs and
  artifacts. Earlier retry-*, wheel-*, rc2-upgrade-*, final-* and power-cut-*
  files are historical snapshots, not the final production-source VM results.
- Wheel/sdist/portable bundle and exact dependency audit evidence is indexed
  with hashes. There are no third-party runtime dependencies.

These are local working-tree snapshots, not a CI result for a committed release.
The [checklist](production-readiness.md) is the authoritative remaining queue.

## Implemented remediation

Most critical reproductions were observed RED before their fixes. Additional
edge/adapter regressions and fixture corrections were added during the audit;
this report does not claim every added test was run RED independently.

| Findings | Code and verification delivered | Remaining proof |
|---|---|---|
| A01-A02 | facts before command, pending async reboot, bounded retries, post-boot ID, retry/window guards; recovery regressions and real wheel reboot | power-cut VM passed; target staging remains |
| A03-A04 | fresh/custom config install, real systemd-analyze, checked commands, timer removal, boot ordering, idle lifecycle lock; native wheel install passed | VM lifecycle passed; target application acceptance remains |
| A05-A06 | strict private files/ancestors, exclusive writes, durable errors, lock ownership, runtime hook/health validation; POSIX regressions | operator-managed environment and executable trust remains prerequisite |
| A07-A08 | finalized truthful outcomes, persistent text reports/warnings, held counts, health severity, outstanding failure, status delivery visibility | remote delivery can duplicate after crash between send and save |
| A09-A10 | persistent quarantine, strict nested state/run IDs, terminal archive recovery, schema2 migration | storage failure cannot guarantee a durable report |
| A11-A12 | stale/unexpected diagnostics, failure-hook isolation, disk checks, reboot-first, user wait, APT repair, manual resume guard | configuration traceability completed below |
| A13-A15 | native record locks, apply recheck, held/versionlocks, candidate security origins, native kernel ordering, failed security/audit probe refusal | all seven native strategy fixtures passed |
| A16 | shared process-group TERM/120s grace/KILL/10s drain for PM/hooks/commands | deliberately detached descendants can escape a POSIX group; documented limit |
| A17-A19 | verified STARTTLS, no redirect/proxy credential forwarding, exception/state/log redaction, private logs, correlation, configured bounded health probes | real destination staging |
| A20-A21 | explicit tested version registry, actual transaction/archive tests, native lock/repair/versionlock fixtures | final native APT hold matrix |
| A22-A23 | wheel-installed VM, cloud-init/sink/session policy, evidence retention, file-only checksum and validated bundle tooling | power-cut mode, final artifact copy and live release gates |
| A24-A25 | strict config tables/types/URL/ports, chronological history and notification status | final operator documentation reconciliation |

### Additional findings discovered during execution

- **A26 High: systemd package-script incompatibility.** RestrictSUIDSGID=true
  prevented `chmod 4755` in an actual guest. Both maintenance units now set false;
  the wheel VM upgrade proves legitimate package permission restoration succeeds.
  Other applicable hardening remains enabled.
- **A27 High: unknown metadata reported as success.** Failed security queries
  returned empty sets, failed dpkg audit could pass with empty stdout, and any
  partial-refresh warning was accepted. Probes now fail closed; partial refresh
  requires an actual Hit/Get repository result. Warning reports are persistent.
- **A28 High: incomplete lock/durability checks.** Lock files lacked owner/mode
  checks; directory-open errors during fsync were ignored. Both are corrected.
  A real container caught the new missing-parent case; nested lock dirs now work.
- **A29 High: timeout/policy bypass.** Hooks and command health checks bypassed
  group termination; service checks ignored configured timeout; reboot retries
  bypassed the window. Shared execution and fresh pre-command policy checks fix these.

## Findings

The table below preserves the original baseline findings. Current remediation
and remaining proof are recorded above; descriptions here are historical, not
claims that the original defects still exist. References use function names.

| ID | Severity | Source / evidence | Impact and required closure |
|---|---|---|---|
| A01 | Critical | reboot.prepare_and_reboot calls rebooter before engine saves returned facts | Persist facts/attempt before reboot; crash-at-command regression |
| A02 | Critical | engine._do_rebooting immediately enters POST_REBOOT | Normal asynchronous reboot can report failure on original boot; preserve pending state and resume in new process |
| A03 | High | cli._cmd_install loads missing config; installer._real_systemd prefixes analyze with systemctl | Fresh install and validation fail; real adapter and clean-host tests |
| A04 | High | installer ignores enable/reload results, leaves timer on manual switch; resume unit After and WantedBy multi-user.target | False install success and incorrect schedule/ordering; real systemd validation |
| A05 | Critical | config.load_config reads directly; state store checks symlink only; installer temp files lack nofollow/exclusive creation | Promised root ownership/permission/path protections missing; hostile-file tests on POSIX |
| A06 | High | hooks validation only in config-check, health command executes directly | Writable executable can run as root; enforce on execution and preflight |
| A07 | High | engine._build_report defaults outcome to FAILED before finalization | Successful cycles send false failure reports; assert delivered and archived outcomes |
| A08 | High | no report.txt write; _final_outcome ignores noncritical health; verify outstanding does not fail | Missing durable results / false success; report, criticality and strategy verification tests |
| A09 | Critical | StateStore._quarantine removes active state; next load returns None | Unknown in-flight state can be silently bypassed on next run; durable refusal and explicit recovery |
| A10 | High | CycleState.from_dict coerces types, lacks run_id validation; terminal state not archived on resume | Invalid recovery facts, archive path traversal, lost history; strict schema and crash-finalization tests |
| A11 | High | engine lacks stale timeout, disk checks, actual unexpected-reboot diagnostics; failure hook can raise | Incomplete safe recovery/preflight; injected failures and aging tests |
| A12 | High | accepted repair_interrupted/user_wait_timeout unused; reboot_first acts update-first; resume --force unused | Advertised safety policy not enforced; resolve spec then behavior tests |
| A13 | High | apt.probe_lock uses flock; providers do not recheck locks at apply | Wrong native-lock probe; use POSIX record-lock tests from another process |
| A14 | High | APT simulation ignores new installs; security parser not tied to candidate; kernel versions lexically ordered | Missing updates and incorrect security/reboot decisions; real fixtures and native version comparison |
| A15 | High | DNF rejects only exit 1, omits lock/versionlock checks; both verify safe regardless configured strategy | False discovery/verification/support claims; per-provider failure and strategy tests |
| A16 | High | subproc timeout kills parent only, uses 10s grace vs specified 2m | Orphaned children and incorrect timeout contract; process-tree timeout regression |
| A17 | High | SMTP STARTTLS lacks explicit verified context, error includes server text; webhook follows redirects/proxies | Credential transport/leak risks; TLS, redirect, proxy and canary tests |
| A18 | High | logger adapter unused in engine; exception formatting and literal headers not redacted; unsafe file handler opening | Missing correlation / secret and privileged-file risks; real log-path tests |
| A19 | High | failed-units probe errors reported count 0; service probes have no timeout | Unknown health reported healthy or hangs; bounded failure tests |
| A20 | High | registry mixes exact/fallback priority and broadly claims derivatives/versions | Untested hosts supported implicitly; explicit release scope and selection tests |
| A21 | High | L3 DNF asserts detect only; APT suppresses errors/accepts failure history | Green matrix does not prove maintenance; deterministic real update/lock/dirty-state scenarios |
| A22 | High | L4 HTTP 10.0.2.2 rejected, pip not ensured, no cloud-init wait, SSH session present, cleanup/log retention flawed | Reboot gate cannot establish intended result; repair and run real VM paths |
| A23 | High | release sha256sum glob includes portable directory; release omits some CI checks | Broken publication / incomplete gates; artifact build/install/checksum validation |
| A24 | Medium | config tables unchecked, monthly bool accepted, missing required health values, secret URLs/headers accepted | Malformed config may crash or bypass documented checks; schema regressions |
| A25 | Medium | history sorted by random run_id; status does not expose delivery failures; docs contain obsolete references | Misleading operator view; chronological history and status tests; doc sync |

No separate source finding in the small entrypoints, enum/dataclass-only models,
or notifier abstract base during this pass. Window arithmetic has existing
same-day/overnight tests; timezone/DST and equal-boundary policy still need audit.

## Requirement traceability and remaining proof

| Requirements | Implementation / existing tests | Audit findings |
|---|---|---|
| FR-01–03 | osdetect, providers; test_osdetect, test_apt, test_dnf | A14–15, A20; seven listed versions verified; derivatives excluded |
| FR-04, NFR-02/04 | engine, states, state_store; test_engine, test_states, test_state_store | A01–02, A05, A09–11 |
| FR-05–09 | config, engine, providers; test_config, provider tests | A11–15, A24 |
| FR-10–12 | reboot, engine, installer; test_engine, test_installer | A01–04, A08, A12 |
| FR-13–14 | health, notify, report; test_health, test_notify, test_report | A07–08, A17, A19 |
| FR-15–18 | cli, lock, engine; test_cli*, test_lock, test_hooks_dryrun | A06, A12; readiness CLI/provider regressions passed |
| FR-19–21 | installer, cli; test_installer, test_cli_install | A03–05 |
| NFR-01/06/07 | pyproject, source layout, CI | stdlib-only runtime confirmed; static and artifact checks recorded in evidence |
| NFR-03/05/08 | logging, subproc, providers, policies | A12–18 |
| FR-S1–7 | engine, states, reboot; test_engine*, test_apt_integration | A01–02, A11–12; recovery regressions and power-cut VM passed |
| FR-S8–10 | state_store, engine, providers | A09–13 |
| FR-S11–15 | engine, notify, health, reboot, lock | A01–02, A07–08, A17, A19 |
| State invariants 1–6 | engine, reboot, state_store, states | A01–02, A09–10; persistence/recovery regressions and VM passed |
| T1–4, T6–8, T11–12 | config, hooks, subprocess, providers, persistence, installer | A05–06, A10, A13, A16, A24 |
| T5/T9/T13–14 | logging, notify, state/report | A07–08, A17–18; delivery idempotency must acknowledge crash ambiguity |
| T10 | dependencies, workflows, artifacts | A23; exact dependency audit; artifact hashes; dev tools remain unpinned |

### Configuration traceability

All accepted configuration groups were traced through parsing to their consumer.
`test_config.py`/`test_config_edges.py` cover schema boundaries; the tests below
cover execution. Paths and hook commands are revalidated at privileged use.

| Accepted keys | Consumer and evidence |
|---|---|
| maintenance enabled, schedule/day/time/random_delay | engine run gate; installer calendar/timer; test_engine, test_installer and installed VM units |
| maintenance window_start/window_end | Window + engine before upgrade/reboot, after hooks and retry; test_window, test_readiness_recovery |
| estimates refresh/per_package/upgrade_min/reboot_verify | dry-run total includes refresh; action gates use remaining upgrade/reboot estimates; test_readiness_engine, test_engine |
| updates strategy | provider-specific safe/security/full argv, discovery and verification; provider unit/L2 and native L3 strategy fixtures |
| repair_interrupted | explicit APT repair only, window-guarded and never dry-run; unsupported DNF repair reports manual action; native dirty APT fixture |
| config_files.policy / dnf.allow_erasing | APT conffile options; DNF full-only opt-in flag; provider/config tests. RPM retains native conffile semantics |
| reboot policy/existing_pending | RebootController + initial reboot markers; policy matrix, recovery tests and L4 |
| allow_if_users_logged_in/user_wait_timeout | bounded user wait, unknown probe refusal and subsequent window check; recovery/CLI tests |
| package_manager lock_timeout/lock_poll_interval | bounded native preflight polling and pre-apply recheck; native lock and engine tests |
| refresh_timeout/upgrade_timeout | provider configuration to run_argv; timeout/termination tests |
| email enabled/to/from/host/port/TLS/username/password/env | CLI notifier composition + SmtpNotifier; config, local SMTP and TLS/secret tests |
| webhook enabled/url/headers/timeout | WebhookNotifier with env headers; local transport, redirect/proxy and redaction tests |
| all five hooks/timeout/failure_policy | runtime validated HookRunner + engine policy; hook/dry-run/security tests |
| health service/http/tcp/command fields, timeout, critical | HealthCheckRunner and strict config; native command/local transport and health failure tests |
| logging level/file | configure_logging, protected JSONL, redaction and cycle context; logging tests |
| paths state_dir/lock_file | CLI composition, StateStore and installer lifecycle; custom path and adversarial file tests |

Draft schema examples were corrected to the actual version-2 contract; config
fingerprints and index timestamps are not claimed as shipped metadata. Core
recovery facts, explicit blocked reasons, terminal timestamps and reports remain
required and tested. Read-only plans may refresh package indexes; they do not
change installed packages, run hooks, write cycle state or reboot.

Operational limits: native kernel ordering/expected-kernel rejection is tested
in L2, while the VM uses a synthetic package and real boot IDs; no real kernel
upgrade was claimed. HTTP/email delivery can duplicate if a process dies after
remote acceptance but before persisting success. A full/unwritable filesystem
can prevent durable reporting; failures are not reported as successful writes.
The CI guard is a static tripwire, not a sandbox; disposable execution boundaries
and injected adapters remain essential.

### Final native and lifecycle findings

- **A30 High: security strategy mismatch.** Native Fedora 44 DNF5 advisory
  tables have additional columns. Parse NEVRA tokens instead of a fixed third
  column. Pin APT security installs to the exact classified version so a later
  index change cannot substitute a different candidate. Native security/full
  upgrades passed on all seven platforms.
- **A31 Medium: misleading operator output.** Failed early cycles now show
  outstanding updates as unknown and health as not run. Config-check conceals
  webhook paths; invalid custom config cannot silently produce default IDLE.
- **A32 High: destructive purge scope.** Before changing units or deleting
  state, reject unrelated files, invalid history JSON and orphan text reports.
  Valid paired history still purges; refusal and positive regressions passed.
- **A33 High: malformed nested recovery data.** Validate required and unknown
  nested record fields and finite health durations before engine construction;
  malformed records enter durable quarantine rather than crashing on resume.

## Native behavior references

- Linux [flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html) and
  [lockf(3)](https://man7.org/linux/man-pages/man3/lockf.3.html): local Linux flock
  and traditional fcntl record locks are distinct; lockf uses fcntl record locks.
- Python [SMTP](https://docs.python.org/3/library/smtplib.html): STARTTLS accepts
  an SSL context; supply verified trust configuration explicitly.

Native package-manager behavior and generated systemd units were exercised in
disposable L3/L4 environments. Target application and real kernel upgrade
acceptance remain deployment-specific; synthetic packages do not prove them.
