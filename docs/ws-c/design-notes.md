# Workstream C — Execution, verification & recovery (design notes)

Tracking issue: [#83](https://github.com/affan80/pentester-AI-harness/issues/83) ·
Owner: Person C · Contract file: `harness/contracts/verification.py`

## Milestone status

| Milestone | Sub-issues | State | Evidence |
|---|---|---|---|
| #11 executor loop + patch engine | #51–#53 | **merged** (closed) | `harness/execution/executor.py`, `harness/tools/files.py`; mutation evidence with patch id / old+new hashes / changed lines; git diff inspected after every mutation |
| #12 shell, git checkpoints, rollback | #54–#56 | **merged** (closed) | `harness/execution/runner.py`, `executor._checkpoint_before`, `rollback_checkpoint` |
| #14 verification ladder + reports | #60–#62 | **implemented, merged via PR #105**; sub-issues open pending close | `harness/verification/`; 20 tests; evidence comments on #60–#62 |
| #15 failure classification, recovery, loops | #63–#65 | **implemented** on `feat/recovery-loop-detection` | `harness/recovery/`; 29 tests; evidence comments on #63–#65 |
| #16 evidence-gated audit (shared with A) | #66–#68 | **implemented** on `feat/evidence-gated-audit` | `harness/audit/`; 22 tests; evidence comments on #66–#68 |

## Design decisions

1. **Ladder gating is structural, not procedural.** `run_verification_ladder` has no
   model-verdict parameter; VERIFIED requires no failed stage, a passed diff/scope
   check, and at least one passing test-bearing stage. Missing test evidence yields
   `INCONCLUSIVE`, never VERIFIED (issue #14, "false VERIFIED" risk in PRD §27).
2. **Failure classification reads structured facts only** (stage identity, exit
   codes, timeout/availability flags). Missing executables and OS errors classify
   as `ENVIRONMENT` and stop recovery without burning retries — runtime problems
   are not source defects (issue #15, PRD §17).
3. **Recovery input is a narrow bundle by construction.** `FailureEvidence` has no
   run-history field, so nothing but the focused evidence can reach a model prompt
   or a recovery decision (PRD §17).
4. **Loop fingerprints are content-addressed**: tool name + canonical-JSON
   arguments + result digest (artifact sha256 takes precedence). Identical
   attempts block at the configured threshold into forced REPLAN without
   consuming retry budget (PRD §20).
5. **The audit evidence gate lives in the state machine.** Leaving SUSPECTED and
   reaching CONFIRMED require an `EvidenceRef` of kind `reproducer` or
   `tool_result`; `model_observation` kinds are rejected. Rejections must record
   why, and failed reproduction attempts are preserved as evidence (PRD §18).
6. **One audit pass consumes one `AUDIT_ROUNDS` unit.** The orchestrator
   re-engages after routed repairs; the budget bounds total engagements.
7. **Verification commands never mutate the target repository**: the runner sets
   `PYTHONDONTWRITEBYTECODE=1` and the Python syntax stage is parse-only
   (`ast.parse`) — a same-second fix would otherwise keep serving stale bytecode
   from `__pycache__` written by an earlier check.
8. **Routed audit findings are ordinary goals.** `finding_to_verification_input`
   produces `goal_kind=audit` with scope = the finding's path and the proven
   reproducer, so repaired findings run the identical ladder as requested work.

## Contract file

`harness/contracts/verification.py` mirrors the public shapes of
`harness/verification/models.py`, `harness/recovery/models.py`, and
`harness/audit/models.py` as a stdlib-only, import-free contract per the
file-per-owner rule. Enum values are the wire format (persisted in
`verification.json` / `recovery.json` / `findings.json`); new members may be
appended, existing values must never be renamed. Any drift is reconciled at #20.

## Local verification

```bash
./.venv/bin/python -m pytest tests/contracts tests/verification tests/execution tests/recovery tests/audit -q
./.venv/bin/ruff check harness tests
./scripts/ci.sh
```
