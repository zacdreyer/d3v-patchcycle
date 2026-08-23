# ADR-0009: dpkg conffile policy — confdef+confold default; force flags prohibited

Status: Accepted — 2026-08-23

## Context

Unattended apt/dpkg operation must never block on configuration-file prompts,
but suppressing prompts must not mean blindly making dangerous choices. dpkg
offers `--force-confold` (keep installed), `--force-confnew` (take package
version), and `--force-confdef` (take the default action when one exists).
apt-get additionally offers dangerous escape hatches
(`--allow-remove-essential`, `--force-yes`, etc.).

## Decision

- Default policy `keep_existing`:
  `-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold`.
  Rationale: `confdef` first lets packages with a safe default action take it;
  `confold` then keeps the admin's modified file when judgement is required.
  Keeping the known-working config is the conservative choice for production.
- Optional policy `take_package` (`confdef` + `confnew`) exists for
  image-built/immutable-style fleets; `config-check` warns when enabled, and
  the report lists conffile decisions observed (`*.dpkg-new`/`*.dpkg-old`
  artefacts are detected post-upgrade and reported).
- **Prohibited in all code paths** (enforced by a static source-scan test):
  `--allow-remove-essential`, `--allow-downgrades`,
  `--allow-change-held-packages`, `--allow-unauthenticated`,
  `--allow-insecure-repositories`, `--force-yes`, `--ignore-hold`.
- Held packages are respected, reported, never overridden.

## Alternatives considered

- **confnew default:** rejected — silently replacing admin configuration can
  break production services; the risk asymmetry favours keep_existing.
- **Interactive-style prompt answering via pipes:** rejected — fragile and
  indistinguishable from guessing.
- **Three-way diff/merge:** out of scope (that's `etckeeper`/config-mgmt
  territory); we report artefacts so admins can merge deliberately.

## Consequences

- Unattended upgrades cannot wedge on conffile prompts and cannot silently
  discard local configuration.
- Risk is made visible: conffile artefacts appear in the report and logs.
