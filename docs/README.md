# D3V PatchCycle — Documentation Index

This project uses **Spec-Driven Development (SDD)** with **Test-Driven
Development (TDD)**. The repository contained no pre-existing SDD framework,
so the structure below was established per the project mandate. Specifications
are part of the product: any implementation change must update spec, tests,
code, and operations docs together (see `development/implementation-plan.md`).

## Structure

```
docs/
├── research/
│   └── phase-0-research.md          # Verified findings from official docs
├── requirements/
│   └── product-specification.md     # Purpose, goals, FR/NFR, platforms, DoD
├── architecture/
│   └── system-architecture.md       # Components, abstractions, data flows
├── specifications/
│   ├── state-machine.md             # States, transitions, recovery behaviour
│   ├── provider-contract.md         # UpdateProvider interface + APT mapping
│   ├── configuration.md             # Every option: type, default, behaviour
│   └── failure-recovery.md          # Crash/power/corruption scenarios
├── decisions/                       # Architecture Decision Records (ADRs)
├── testing/
│   └── test-strategy.md             # TDD workflow, test layers, CI
├── security/
│   └── threat-model.md              # Threats, mitigations, secure defaults
├── operations/
│   └── operations.md                # Install, run, schedule, troubleshoot
└── development/
    └── implementation-plan.md       # Phases, milestones, current status
```

## Methodology

1. **Specify first.** No production code before the relevant spec section
   exists and is internally consistent.
2. **TDD for all code.** RED (failing test) → GREEN (minimal implementation)
   → REFACTOR. See `testing/test-strategy.md`.
3. **Decisions are recorded.** Significant choices get an ADR in `decisions/`.
4. **Docs stay synchronized.** Spec drift is a defect.

## Current status

**2026-09-08: 1.0.0rc2 is undergoing final release acceptance.** The source audit
and remediation cover correctness, security, installation, reporting and native
package/reboot behavior. Local unit, container and VM gates have passed; the
latest operator/archival gate and delivery evidence are being finalized.

The [production-readiness checklist](development/production-readiness.md) is the
current work queue. [Audit evidence](development/code-audit.md) records verified
fixes and limits; [deployment acceptance](operations/deployment-acceptance.md)
records the staging and release steps still required for actual target hosts.
The [implementation plan](development/implementation-plan.md) preserves history;
[agent memory](../agent-memory.md) provides handoff context.
