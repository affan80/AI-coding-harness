# File Ownership and Parallel Workstream Map

Four workstreams (one team member each) execute in parallel and merge into `main` with minimal conflicts. This document is the binding file map; each workstream issue also carries a copy:

- [Workstream A — Orchestrator, planning & shared contracts](https://github.com/affan80/pentester-AI-harness/issues/81)
- [Workstream B — Repository intelligence & context](https://github.com/affan80/pentester-AI-harness/issues/82)
- [Workstream C — Tools, execution, verification & recovery](https://github.com/affan80/pentester-AI-harness/issues/83)
- [Workstream D — CLI/TUI, evidence, telemetry, MCP & demos](https://github.com/affan80/pentester-AI-harness/issues/84)

## Directory ownership

| Path | Owner | Notes |
|---|---|---|
| `harness/core/` | A | state machine, session manager, budgets |
| `harness/contracts/` | A | shared typed models/protocols; changes need one consumer review |
| `harness/intent/` | A | objective → goal graph |
| `harness/planning/` | A | plan schemas, planner/replanner |
| `harness/repository/` | B | inventory, ignore rules, profiles |
| `harness/context/` | B | ranking, budgets, compaction, `ContextBundle` |
| `harness/tools/` | C | registry, permissions, `ToolResult` |
| `harness/execution/` | C | executor, file tools, patch engine |
| `harness/verification/` | C | verification ladder, reports |
| `harness/recovery/` | C | classification, bounded retry, rollback |
| `harness/adapters/` | C | Python/Node project adapters |
| `cli/` | D | CLI/TUI, prompts, exit codes |
| `harness/telemetry/` | D | events, metrics, final report |
| `harness/mcp/` | D | MCP gateway, GitHub ingestion |
| `demos/` | D | demo fixtures and scripts |
| `tests/<area>/` | same owner as `<area>` | each workstream's tests and fixtures live under its own area |
| `docs/ws-<a|b|c|d>/` | that workstream | private design notes |

The "own" rule covers creating, editing, and deleting files inside the owned path. Nobody else writes there — not even one-line fixes.

## Shared files

| File | Rule |
|---|---|
| `pyproject.toml` | Anyone may append their own dependencies, alphabetically, in their own PR. Only Workstream A restructures the file. |
| `harness/__init__.py` and other package `__init__.py` files | Owned by the package owner. Others import from module paths, never from `__init__` re-exports, so exports can change without cross-PR edits. |
| `scripts/ci.sh`, `.github/workflows/` | Team lead only. |
| `README.md`, `CONTRIBUTING.md`, `docs/TEAM_PLAN.md`, `docs/ISSUE_BREAKDOWN.md`, `docs/FILE_OWNERSHIP.md`, `.gitignore` | Team lead only. |
| `docs/PRD.md` | Read-only reference. |

## Conflict-avoidance rules

1. **One branch per workstream:** `feat/a/<topic>`, `feat/b/<topic>`, `feat/c/<topic>`, `feat/d/<topic>`. Small PRs per sub-issue, all cut from that workstream's branch line.
2. **Single sequencing gate:** `harness/contracts/` from #21 is the only blocking dependency. Workstream A merges #21–#23 first; until then B/C/D code against the schema in #21 and rebase onto it once merged. After that, the four streams are fully independent.
3. **Cross-boundary changes:** open a comment on the owning workstream issue instead of editing. Small fixes needed to unblock an integrated PR are allowed only when clearly described in the PR and reviewed by the owning teammate.
4. **Contract changes** in `harness/contracts/` are reviewed by one consumer of that contract (per CONTRIBUTING).
5. **Rebase before opening a PR** and after every `main` update; conflicts in `pyproject.toml` dependency lists resolve alphabetically with no discussion needed.
6. **Merge order per integration checkpoint:** A → B → C → D when multiple PRs are ready simultaneously (matches the dependency direction of the contracts).
7. Final integration (#78–#80, parent #20) happens only after every workstream's own sub-issues close, and goes through one coordinated integration PR per person off their own branch.

## Workload summary

| Workstream | Milestone parents | Feature sub-issues |
|---|---|---|
| A (#81) | #1, #5, #6, #10 | 12 (+3 shared with C: #66–#68) |
| B (#82) | #4, #8, #9 | 9 |
| C (#83) | #7, #11, #12, #13, #14, #15, #16 | 18 (+3 shared with A) |
| D (#84) | #2, #3, #17, #18, #19 | 15 |
| All (#20) | — | 3 (#78–#80) |
