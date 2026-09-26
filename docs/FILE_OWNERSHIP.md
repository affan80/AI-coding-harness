# File Ownership and Parallel Workstream Map

Four workstreams (one team member each) run **simultaneously and independently**, with equal load, and merge into `main` with minimal conflicts. This document is the binding file map; each workstream issue also carries a copy:

- [Workstream A — Orchestrator, planning & shared contracts](https://github.com/affan80/pentester-AI-harness/issues/81)
- [Workstream B — Repository intelligence, registry, context & adapters](https://github.com/affan80/pentester-AI-harness/issues/82)
- [Workstream C — Execution, verification & recovery](https://github.com/affan80/pentester-AI-harness/issues/83)
- [Workstream D — CLI/TUI, evidence, telemetry, MCP & demos](https://github.com/affan80/pentester-AI-harness/issues/84)

## Directory ownership

| Path | Owner | Notes |
|---|---|---|
| `harness/core/` | A | state machine, session manager, budgets |
| `harness/contracts/core.py`, `planning.py` | A | A's shared contract files |
| `harness/intent/` | A | objective → goal graph |
| `harness/planning/` | A | plan schemas, planner/replanner |
| `harness/repository/` | B | inventory, ignore rules, profiles |
| `harness/tools/` | B | tool registry, permissions, `ToolResult` |
| `harness/context/` | B | ranking, budgets, compaction, `ContextBundle` |
| `harness/adapters/` | B | Python/Node project adapters |
| `harness/contracts/tools.py`, `repository.py`, `context.py` | B | B's shared contract files |
| `harness/execution/` | C | executor, file tools, patch engine |
| `harness/verification/` | C | verification ladder, reports |
| `harness/recovery/` | C | classification, bounded retry, rollback |
| `harness/contracts/verification.py` | C | C's shared contract file |
| `cli/` | D | CLI/TUI, prompts, exit codes |
| `harness/telemetry/` | D | events, metrics, final report |
| `harness/mcp/` | D | MCP gateway, GitHub ingestion |
| `demos/` | D | demo fixtures and scripts |
| `harness/contracts/evidence.py` | D | D's shared contract file |
| `tests/<area>/` | same owner as `<area>` | each workstream's tests and fixtures live under its own area |
| `docs/ws-<a|b|c|d>/` | that workstream | private design notes |

Mental split: **B owns the read side** (observe, index, expose, profile the repository — plus the registry through which every tool is reached); **C owns the write side** (change the repository and prove the change). A owns state and planning; D owns the product surface and evidence.

The "own" rule covers creating, editing, and deleting files inside the owned path. Nobody else writes there — not even one-line fixes.

## Shared files

| File | Rule |
|---|---|
| `pyproject.toml` | Anyone may append their own dependencies, alphabetically, in their own PR. Only Workstream A restructures the file. |
| `harness/contracts/__init__.py` | Written **once** by the first contracts PR with the agreed re-export list, then **frozen**. New names are imported from their module path, never added here — so no two workstreams ever edit it. |
| Other `harness/**/__init__.py` | Owned by the package owner. Others import from module paths, never from `__init__` re-exports. |
| `scripts/ci.sh`, `.github/workflows/` | Team lead only. |
| `README.md`, `CONTRIBUTING.md`, `docs/TEAM_PLAN.md`, `docs/ISSUE_BREAKDOWN.md`, `docs/FILE_OWNERSHIP.md`, `.gitignore` | Team lead only. |
| `docs/PRD.md` | Read-only reference. |

## Contract files (no waiting on another workstream)

`harness/contracts/` is file-per-owner: `core.py` + `planning.py` (A), `tools.py` + `repository.py` + `context.py` (B), `verification.py` (C), `evidence.py` (D).

1. Each contract file is **self-contained**: standard library only, no imports between contract files. Small value types (IDs, enums, evidence refs) may be locally duplicated; #20 reconciles any drift at integration.
2. Everyone defines their own contract file **on day one** from the schemas in #21, #39, #57, and #60 — no workstream is ever blocked by another.
3. Consumers code against the **frozen schemas in the sub-issues**, not against a teammate's in-progress branch. Wire the real implementations at integration (#20).
4. Contract changes are reviewed by one consumer of that contract (per CONTRIBUTING).

## Conflict-avoidance rules

1. **One branch per workstream:** `feat/a/<topic>`, `feat/b/<topic>`, `feat/c/<topic>`, `feat/d/<topic>`. Small PRs per sub-issue, all cut from that workstream's branch line.
2. **No sequencing gate.** Nobody waits for another workstream's code. Contracts are file-per-owner and defined day one; cross-workstream wiring happens only at #20.
3. **Cross-boundary changes:** open a comment on the owning workstream issue instead of editing. Small fixes needed to unblock an integrated PR are allowed only when clearly described in the PR and reviewed by the owning teammate.
4. **Rebase before opening a PR** and after every `main` update; conflicts in `pyproject.toml` dependency lists resolve alphabetically with no discussion needed.
5. **When several PRs land at once**, merge in A → B → C → D order as a tie-breaker (matches dependency direction), but this is a convenience, not a gate.
6. Final integration (#78–#80, parent #20) happens only after every workstream's own sub-issues close, and goes through one coordinated integration PR per person off their own branch.

## Workload summary (equal load: 15 sub-issues each)

| Workstream | Milestone parents | Feature sub-issues |
|---|---|---|
| A (#81) | #1, #5, #6, #10 | 12 (+3 shared with C: #66–#68) |
| B (#82) | #4, #7, #8, #9, #13 | 15 |
| C (#83) | #11, #12, #14, #15, #16 | 12 (+3 shared with A) |
| D (#84) | #2, #3, #17, #18, #19 | 15 |
| All (#20) | — | 3 (#78–#80) |
