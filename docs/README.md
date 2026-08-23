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

Phases 0–7 complete; **v1.0.0-rc1** (feature-complete, pre-release). The V1.0
release gate is one green nightly L4 VM reboot run
(`tests/vm/reboot_harness.sh` via `.github/workflows/vm-reboot.yml`).
Phase 8 (additional providers) is post-V1. See
`development/implementation-plan.md` for the phase breakdown and
`../agent-memory.md` for a token-efficient cold-start summary.
