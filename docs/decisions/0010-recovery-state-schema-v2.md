# ADR-0010: Recovery state schema v2

Status: Accepted — 2026-09-08
Supersedes: the schema-version-1 selection in ADR-0006; its atomic persistence
and refusal requirements remain binding.

The readiness audit adds facts required for correct recovery: a verified
post-reboot boot ID and markers distinguishing a reboot before updates from
the final reboot. Older binaries would silently ignore these fields in schema
v1, so additive JSON compatibility is insufficient for safe downgrade.

New cycles and migrated records use schema v2. A v1 reader already refuses a
newer schema. The v2 reader explicitly migrates v1 records without inventing
boot identity or marking actions completed. Missing proof remains missing;
normal conservative recovery checks still apply. Reading historical v1 records
does not rewrite their files. An active record is migrated on the next durable
write. Unsupported newer versions are refused without quarantine or mutation.

Operators must upgrade only while idle, preserve history, and must not downgrade
while a v2 cycle is pending. Regression tests cover migration and newer-version
refusal.
