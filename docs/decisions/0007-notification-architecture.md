# ADR-0007: Notification architecture — SMTP + webhook in V1; delivery failure ≠ cycle failure

Status: Accepted — 2026-08-23

## Context

Admins need a final report through practical channels. V1 must provide at
least email, ideally a generic webhook, without a dependency explosion.
Crucially, the truth of what happened to the machine must not be hostage to
notification delivery.

## Decision

- `Notifier` registry; V1 implementations: **SMTP email** (stdlib `smtplib`,
  optional STARTTLS/auth) and **generic webhook** (stdlib `urllib`, JSON POST,
  `env:`-indirected header secrets).
- All enabled notifiers are attempted with bounded retries (5s/30s/2m).
  Per-notifier `notification_status` is recorded in state/history.
- **Delivery failure never changes the maintenance outcome.** The full report
  is always persisted to `history/<run_id>.report.txt` so no information is
  lost when delivery fails.
- Future notifiers (Slack/Teams/Discord/Gotify/ntfy) are webhook-shaped
  specialisations; they enter via the same registry with their own tests.

## Alternatives considered

- **sendmail/local MTA only:** kept as the default SMTP target
  (`localhost:25`), but not sufficient alone — many servers have no MTA.
- **Third-party SDKs per service:** rejected (dependency surface); generic
  webhook covers them.

## Consequences

- Zero runtime dependencies for notifications.
- Operators get a truthful outcome taxonomy: SUCCESS / SUCCESS_WITH_WARNINGS
  (incl. notify-failed) / NO_UPDATES / FAILED / BLOCKED /
  MANUAL_REBOOT_REQUIRED.
- Report content is fixed by spec (product-specification examples) and
  snapshot-tested.
