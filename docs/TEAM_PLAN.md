# Four-Person Hackathon Execution Plan

The goal is parallel progress with clear integration boundaries. Each person owns a vertical slice and exposes small typed contracts to the rest of the team.

## Person A — Orchestrator and planning

Own:

- `harness/core/`
- `harness/intent/`
- `harness/planning/`
- shared session/goal/plan models

Deliver:

- state machine
- session manager
- goal graph
- planner/replanner contract
- budget/state transitions

Integration contract:

```text
Input: UserRequest + RepositoryProfile + ContextBundle
Output: GoalGraph + ExecutionPlan + next orchestrator state
```

## Person B — Repository intelligence and context

Own:

- `harness/repository/`
- `harness/context/`

Deliver:

- file inventory and ignore handling
- language/framework/manifest detection
- text/symbol/test discovery
- candidate ranking
- context budget and priority tiers
- working-set builder
- compaction/checkpoint summary

Integration contract:

```text
Input: repository path + current goal + query/failure
Output: RepositoryProfile + ContextBundle + evidence refs
```

## Person C — Execution, verification, recovery

Own:

- `harness/tools/`
- `harness/execution/`
- `harness/verification/`
- `harness/recovery/`
- `harness/adapters/`

Deliver:

- tool registry and permission checks
- file/search/shell/git/test tools
- patch/checkpoint flow
- Python and Node adapters
- verification ladder
- failure classifier and bounded retry inputs

Integration contract:

```text
Input: PlanStep + ContextBundle + policy
Output: ToolResult / PatchResult / VerificationReport
```

## Person D — Product surface, evidence, MCP and demo

Own:

- `tui/`
- `harness/telemetry/`
- `harness/mcp/`
- final report/demo fixtures

Deliver:

- CLI/TUI repository + objective flow
- event stream and run directory
- final evidence report
- GitHub MCP adapter for issue/PR context
- demo scripts/data
- token/tool/model metrics display

Integration contract:

```text
Input: session events + structured state
Output: user interaction + persisted evidence + final report
```

## Shared integration rules

1. Do not directly import another person's private implementation internals. Integrate through shared dataclasses/protocols.
2. Keep PRs small and focused on one behavior.
3. Rebase/merge from `main` before opening the final PR when practical.
4. Every behavior-changing PR includes a runnable verification step.
5. The orchestrator owns authoritative state; agents/tools return results and never secretly mutate session state.
6. Only the executor/tool layer writes project source files.
7. A failed deterministic check cannot be converted to `VERIFIED` by an LLM explanation.

## Recommended build order

### Parallel block 1

- A: session models + state machine + mock plan
- B: repository profile + search/read + context budget
- C: tool registry + read-only tools + adapter detection
- D: CLI/TUI shell + event/evidence writer

### Integration checkpoint 1

One command should accept repo + prompt, profile the repo, build context, print a plan, and persist a run directory without modifying code.

### Parallel block 2

- A: real goal/planning model calls + plan validation
- B: dependency/test ranking + compaction
- C: patch/shell/tests + verification
- D: live tool/state rendering + final report

### Integration checkpoint 2

The harness should change one small file and prove the change with a target test.

### Parallel block 3

- A: replan transitions + budget handling
- B: failure-focused context refresh
- C: recovery classifier + retry + rollback
- D: GitHub MCP + polished demo/metrics

### Final checkpoint

Run all three PRD demo scenarios and capture evidence from each.

