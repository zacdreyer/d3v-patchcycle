# Architecture Decision Records

ADRs capture significant, hard-to-reverse decisions. Format: Context →
Decision → Alternatives considered → Consequences. ADRs are immutable once
accepted; superseding decisions get a new ADR that references the old one.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-python-implementation-language.md) | Python 3.11+ as implementation language | Accepted |
| [0002](0002-toml-configuration-format.md) | TOML configuration via stdlib `tomllib` | Accepted |
| [0003](0003-systemd-timers-over-cron.md) | systemd timers/services instead of cron | Accepted |
| [0004](0004-explicit-persistent-state-machine.md) | Explicit persistent state machine as the core | Accepted |
| [0005](0005-provider-abstraction.md) | Provider/scheduler/notifier registry abstractions | Accepted |
| [0006](0006-atomic-state-persistence.md) | Atomic JSON state persistence, schema-versioned | Accepted |
| [0007](0007-notification-architecture.md) | SMTP + generic webhook in V1; delivery failure ≠ cycle failure | Accepted |
| [0008](0008-boot-id-reboot-verification.md) | boot-id comparison + always-on resume unit | Accepted |
| [0009](0009-dpkg-conffile-policy.md) | confdef+confold default; force flags prohibited | Accepted |
| [0010](0010-recovery-state-schema-v2.md) | Recovery facts require schema v2 and explicit v1 migration | Accepted |
