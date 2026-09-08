# Deployment acceptance

Candidate: 1.0.0rc2. This record must be completed for the intended deployment
environment before stable fleet rollout. Local synthetic acceptance evidence is
in [the audit](../development/code-audit.md).

## Record the target

- [ ] Host/owner, distribution/version, x86_64 architecture and systemd version.
- [ ] Python 3.11+ interpreter path and approved installation source.
- [ ] Release artifact SHA256, source commit, CI/release gate-only run URLs.
- [ ] Maintenance window/time zone, expected user sessions and reboot policy.
- [ ] Application health probes and notification destination agreed by the owner.
- [ ] Backup/recovery procedure and console access tested for this host.

## Exercise the candidate on a disposable representative host

1. Verify `SHA256SUMS.txt`, install the wheel into a dedicated root-owned venv,
   and run `d3v-patchcycle version` and `detect`.
2. Start with `schedule="manual"`; configure real application health probes
   and the intended notifications. Protect the config/environment files with
   root ownership and mode 0600. Run `config-check`.
3. Run `run --dry-run`; retain the proposed updates, held packages and timing
   estimate. This refreshes metadata but does not install packages or reboot.
4. Run `install`; inspect both generated service units and the enabled resume
   service. No maintenance cycle may be pending while changing installation.
5. During the approved window, run `run`; retain journal, status, history JSON,
   text report and the received notification. Check actual package versions.
6. Exercise a required reboot with console access available. Verify the changed
   boot ID, automatic resume, health checks, report and absence of pending state.
7. Set the intended schedule, rerun `install`, inspect `systemctl list-timers`,
   and observe a scheduled cycle. Confirm local-time/DST policy with the owner.
8. While idle, exercise candidate reinstall/upgrade and uninstall/reinstall on
   the disposable host. Confirm preserved state/history and documented purge scope.

## Acceptance

- [ ] Package, reboot, application-health and notification outcomes accepted.
- [ ] Recovery instructions in [operations](operations.md) reviewed and exercised.
- [ ] No unresolved critical/high audit finding or failed release gate.
- [ ] Owner, acceptance date and retained evidence locations recorded.
- [ ] Stable version/tag/publication approved, followed by a limited first rollout.

Do not infer fleet acceptance from a synthetic container or VM test. Delivery
failure does not rewrite maintenance outcome: inspect each notification status
and the durable report as part of acceptance.
