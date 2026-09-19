# Security Specification & Threat Model

Status: Approved baseline for V1
Date: 2026-08-23
Context: PatchCycle executes as **root** on production servers and drives the
package manager and the reboot mechanism. It is itself high-value attack
surface and must assume hostile-adjacent inputs (config files, hook paths,
environment, package-manager output, filesystem state).

Methodology: STRIDE-flavoured asset/threat enumeration with mitigations and
verification (each mitigation has tests; see test-strategy.md §7).

---

## 1. Assets

- Integrity of the package system (what gets installed/removed).
- Availability of the host (reboot decisions).
- Credentials: SMTP password, webhook tokens.
- Integrity of state/history/logs (they drive recovery and reporting).
- Integrity of configuration (it disables safety policy if tampered).

## 2. Trust boundaries

1. Config file → process (root-owned, but validated as hostile input anyway).
2. Package-manager output → parsers (text from a privileged child, possibly
   influenced by network/repo content).
3. Hook executables → HookRunner (admin-configured; still validated).
4. Environment variables → process (systemd EnvironmentFile or ambient).
5. Filesystem (state dir, /run, /tmp, /var/lib/dpkg) → file operations.
6. Network → SMTP/webhook notifiers and apt transports (apt's own signature
   verification is out of scope but must never be weakened — no
   `--allow-unauthenticated`, no `Acquire::AllowInsecureRepositories`).

## 3. Threats & mitigations

| ID | Threat | Mitigation | Verification |
|---|---|---|---|
| T1 | Command/shell injection via config values, hook args, package names | argv-only subprocess (`shell=False` hard rule, enforced by a wrapper that rejects string commands); config values never concatenated into commands; package names validated against `^[a-z0-9][a-z0-9+._:-]*$` before use; service names validated | unit + security tests with malicious fixtures |
| T2 | PATH hijacking (malicious `apt-get` earlier in PATH) | binaries resolved once at startup from fixed `/usr/bin:/bin:/usr/sbin:/sbin`; ambient PATH never used; resolved path verified absolute and root-owned | unit tests with planted fake binaries |
| T3 | Environment manipulation (`DEBIAN_FRONTEND`, `LD_PRELOAD`, proxy vars) | scrubbed child env (explicit allowlist: PATH-fixed, LANG/LC_ALL=C, DEBIAN_* set by us); `LD_*` never propagated; proxy variables not propagated to notifiers unless explicitly configured | unit tests asserting child env |
| T4 | Malicious config (e.g. `strategy` typo silently disables policy; hook pointing at attacker-writable file) | strict schema (unknown keys rejected), enum validation, hook path rules: absolute + root-owned + not group/world-writable; config file itself must be root-owned, mode ≤ 0600, no symlink | config validation tests; security tests with crafted configs |
| T5 | Secret leakage (SMTP password, webhook token into logs/state/history/report) | secrets only via `*_env` indirection or 0600 config; logging filter redacts configured secret values and `Authorization` headers; state schema contains no secret fields; `config-check` warns on plaintext | log-scrubbing tests with canary secrets |
| T6 | State-file tampering / symlink attack (trick recovery path, redirect writes) | state dir 0700 root:root; opens with `O_NOFOLLOW`; ownership/mode checked before read and before replace; atomic tmp+fsync+rename; corrupt/tampered → quarantine + refuse (FR-S8) | security tests: symlink, wrong owner, partial write |
| T7 | TOCTOU on locks and reboot checks | flock held continuously for the whole cycle; PM lock probes re-checked immediately before apply; reboot gating re-evaluates users/window at decision time, not at cycle start | concurrency tests |
| T8 | Malicious/compromised hook execution | argv validation as T1/T4; hooks run with scrubbed env and timeout; non-zero exit handled per policy; hooks never receive secrets in env | hook runner tests |
| T9 | Notification spoofing / credential theft in transit | STARTTLS supported; config-check warns on credentials without TLS; webhook requires https except loopback; HMAC/shared-token header supported via `env:` header values | notifier unit tests |
| T10 | Dependency-chain compromise | **zero runtime dependencies** (stdlib only); dev deps pinned with hashes in CI; release artifacts checksummed | CI (pip audit on dev env); packaging checks |
| T11 | Privilege escalation surface in installer | installer refuses to run non-root; sets root:root + modes atomically; never preserves attacker-planted files in target dirs (validates ownership of existing files before reusing) | installer tests |
| T12 | Denial of maintenance via fake reboot sentinel / fake sessions | sentinel is root-writable-only by OS design; we only *read* it; session detection uses loginctl (system API), not parseable user processes | provider tests |
| T13 | Log injection via package names/output | structured JSONL logging escapes by construction; journald fields never built with unescaped newlines (values sanitised) | logging tests with hostile strings |
| T14 | Replayed/duplicated notifications | per-run_id single notification path in `NOTIFYING`; terminal states cannot be re-entered (state machine invariant 6) | idempotency tests |

## 4. Secure defaults (shipped configuration)

- strategy `safe`; conffile policy `keep_existing`; no force flags anywhere.
- reboot `when_required`, no reboot with users logged in, existing-pending
  `reboot_first` (still policy-gated).
- `repair_interrupted = false`.
- config 0600, state dir 0700, state/history 0600, logs 0640 root:adm,
  lock in /run (tmpfs).
- hooks empty; webhook disabled; SMTP to localhost by default.

## 5. systemd unit hardening (installed units)

```
[Service]
Type=oneshot
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=false
EnvironmentFile=-/etc/d3v-patchcycle/environment   # 0600, secrets live here
```

Deliberately **not** used (with rationale): `ProtectSystem=strict` and
`ProtectKernelModules=true` — the service must write package state under
/var and load kernel modules via package scripts; `PrivateNetwork` — apt
needs network. These trade-offs are recorded here so they are decisions,
not omissions.

`RestrictSUIDSGID=false` is explicit: native package managers must restore
package-owned SUID files and SGID directories. The readiness VM reproduced
`chmod` failing under `true`. Keeping that restriction would break legitimate
updates; trust remains rooted in administrator-controlled repositories, package
signatures and protected configuration/hooks. `NoNewPrivileges=true` remains.

## 6. Secret-handling practice (documented for operators)

- Preferred: `/etc/d3v-patchcycle/environment` (root:root 0600) referenced by
  systemd `EnvironmentFile=-`, with config holding only
  `smtp_password_env = "PATCHCYCLE_SMTP_PASSWORD"`.
- Acceptable: plaintext in `config.toml` (0600 enforced) — config-check warns.
- Forbidden by validation: secrets in CLI arguments (visible in ps), secrets
  in hook arguments, secrets in webhook URL query strings.

## 7. Review mapping

- OWASP ASVS-style review applied to: injection (T1–T3), file handling
  (T6/T7), secrets (T5/T9), logging (T5/T13), configuration (T4).
- Every new provider/notifier/health-check type requires this document to be
  revisited (checklist item in the implementation plan phase gates).
