# Candidate acceptance evidence

Date: 2026-09-08. Candidate: 1.0.0rc2, schema 2. These are local working-tree
results, not final-commit GitHub CI or deployment-target acceptance.

## Final production-source tests

- `python311-final.txt`, `python312-final.txt`, `delivery-python313.txt`,
  `python314-final.txt`: 601 passed each, 91.58% coverage on Linux.
- `readiness-tests.txt`: Windows Python 3.13, 587 passed, 14 POSIX skips,
  90.59% coverage. Other Windows Python versions remain live-CI gates.
- `final-l3-acceptance.txt`: 11 tests passed across all seven supported images.
- `apt-final-strategies.txt`: 8 passed across four APT images.
- `dnf-final-strategies.txt`: 3 passed across three DNF images.

Native fixtures exercise actual security/full upgrades, holds/versionlocks,
APT record locks and interrupted repair. Only disposable containers were used.

## Final wheel-installed Debian 13 VMs

- `delivery-normal-*`, run `911ba2fc-475d-4205-a317-80dc205c5218`: actual
  synthetic package upgrade, SUID restoration, reboot, changed boot ID,
  automatic resume, health and successful webhook.
- `delivery-power-cut-*`, run `8a154872-610d-4da5-a885-da0b320d9160`: QEMU
  killed during package postinst; restart correctly reports unexpected-reboot
  at UPGRADING, unknown outstanding updates and health not run, without repair.
- Each scenario includes report, journal, package verification, boot IDs,
  installed artifact hash, archive assertion and operator smoke output.
  Both proved persisted webhook success, JSON/text history, idle status,
  idempotent install and history-preserving uninstall/reinstall.

The webhook payload precedes recording its own delivery success. The separate
archive assertion and operator output verify the persisted `sent` outcome.
VM wheel hashes identify the tested builds; the final distributable is rebuilt
with updated documentation, so its ZIP/wheel bytes may differ.

## Scope and retained history

`delivery-build.log` records wheel/sdist build, twine validation, portable ZIP
checks and clean wheel installation. `delivery-SHA256SUMS.txt` identifies the
three files in local `dist/`. `delivery-source-SHA256SUMS.txt` records production
source and package metadata; wheel contents were compared with workspace source.
`delivery-dependency-audit.log` reports no known vulnerabilities in the exact
third-party inventory in `delivery-requirements.txt`; only the unpublished
project itself was excluded. Development dependencies are not hash-locked.

Earlier `retry-*`, `wheel-*`, `rc2-upgrade-*`, `final-*` and `power-cut-*`
artifacts document intermediate reproductions and fixes. Use the specifically
named final test logs and `delivery-*` VM evidence for current conclusions.
Private SSH keys, VM images and cloud-init credentials are not retained here.

Synthetic upgrades do not establish real kernel upgrade or application/fleet
acceptance. Follow [deployment acceptance](../../operations/deployment-acceptance.md)
and the [remaining gates](../production-readiness.md) before stable deployment.
