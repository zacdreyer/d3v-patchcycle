# Security and correctness review — 2026-09-19

Reviewed the production Python modules against the security model, recovery
specification, existing audit, and regression tests. Baseline: merged main
`9f3ca36` (1.0.0rc2, state schema 2). This review makes no claim that the
application is free of undiscovered vulnerabilities.

## Findings corrected

| Finding | Impact | Correction |
|---|---|---|
| Reboot command retries reused stale safety decisions | A retry could reboot after the window closed, users logged in, or the resume service became unavailable | Recheck policy, users, estimated remaining window, and resume-service availability before retrying |
| Same-boot recovery from CHECKING_REBOOT or REBOOT_PENDING attempted an illegal PRECHECK transition | An interrupted cycle could remain stuck after applying updates | Resume those repeatable checks in place without repeating the upgrade |
| Fresh providers defaulted verification to the safe strategy | Post-reboot security/full cycles could verify the wrong set of applicable updates | Initialize verification strategy from configuration for both APT and DNF |
| Matching installed files bypassed trust validation | A matching symlink or writable unit could be accepted as safely installed | Validate existing unit files and configuration references before reading or returning unchanged |
| HTTP health probes inherited proxies and followed redirects | An unrelated proxy/login endpoint could determine health or receive a redirected request | Disable environment proxies and redirects; compare the original endpoint's status and close error responses |
| Overlapping secrets were redacted sequentially | A shorter secret could leave the suffix of a longer secret exposed | Replace escaped secret patterns in one pass, longest first |
| Invalid encoded subprocess output raised UnicodeDecodeError | A successful package command could be recorded as an internal failure | Decode UTF-8 with replacement for invalid bytes, preserving the command exit status |
| SMTP ignored partial recipient rejection | Delivery could be recorded as sent despite rejected recipients | Return a generic failed status without recipient addresses or server details |
| State validation accepted null counters and unknown outcomes, and overflowed on huge durations | History or recovery could crash instead of rejecting malformed state | Reject these values at schema validation; quarantine active corrupt state and skip corrupt history |

## Verification

- Seventeen initial regression cases failed against the baseline, then passed
  after correction. Three additional reboot-retry cases were separately
  reproduced before their fix. A second HTTP case isolates direct redirects.
- Final Linux Python 3.13 suite: **623 passed, 92.06% branch-inclusive coverage**.
- Ruff lint/format checks, strict mypy, CI guard, and documentation links pass.
- Tests ran as root inside a disposable container, with the repository mounted
  read-only and copied into the container. No host package update or reboot ran.
- These are local working-tree results. The earlier main CI/VM results predate
  these changes; the remote matrix and real-VM acceptance gates have not been
  rerun for this review at that point. The September 20 release/installer
  follow-up reruns these gates on the pushed branch.

SMTP retry after partial delivery can resend to recipients who already accepted
the message. Delivery remains at-least-once, not exactly-once. HTTP checks now
require configuring the endpoint whose status is intended to be measured;
a redirect is returned as its own status rather than followed.

## September 20 release and installation follow-up

The combined changes pass 626 Linux Python 3.13 tests with 92.06% coverage.
Packaging now emits verified per-OS installer bundles. CI and release workflows
exercise all seven installers, and the real Debian VM uses the shipped installer
before its reboot/power-loss scenario. See [installation instructions](../../INSTALL.md).
The new regression cases check bundle completeness/checksums and bootstrap
refusal of corrupt wheels or unsafe paths. Build/Twine, dependency audit, Ruff,
strict mypy, actionlint, shell syntax, CI guard and documentation links passed
locally. Remote gate outcomes are recorded in the pull request.
