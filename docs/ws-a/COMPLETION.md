# Workstream A — Orchestrator, planning & shared contracts: completion report

Workstream A is complete. Every milestone and child issue delivered, merged
to `main`, and covered by deterministic tests.

## Delivered scope

| Milestone | Children | Evidence |
|---|---|---|
| #1 — session models, budgets, state machine | #21–#23 | merged via affan80/#86; acceptance in `tests/core/` |
| #5 — intent, goal graph, constraints | #33–#35 | PRs #106, #107, #108 |
| #6 — shared ModelClient | #36–#38 | PRs #109, #110, #138; runtime landed via #94 |
| #10 — execution planning | #48–#50 | PRs #119, #120, #121 |

## Key artifacts

- `harness/intent/` — typed goal-graph schemas, extraction engine with
  bounded correction retries, evidence-carrying failures
- `harness/planning/` — plan serialization, policy validation,
  runnable-step identification, failed-step-anchored bounded replanning
- `harness/core/` — session models, budgets, state machine, orchestrator
  (landed via #86) consumed by the integrated path (#128)

## Test evidence

- intent: schema round-trips for mixed create/fix/audit/refactor/test/verify
  objectives; validation boundary (cycles, unknown deps, missing criteria);
  bounded correction retries with structured failures carrying evidence
- planning: serialization round-trips; policy validation (scope normalization,
  verification coverage, cycles); deterministic runnable-step identification;
  replan keeps completed work and unrelated pending steps
- model: capability contract, normalization, retryable matrix, secure
  configuration, deterministic fake client

## Known limitations

- the model layer runs offline via the scripted fake client; live provider
  calls require credentials via environment configuration (documented in
  the model config module and release notes)
