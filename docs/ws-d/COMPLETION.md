# Workstream D — CLI/TUI, evidence, telemetry, MCP & demos: completion report

Workstream D is complete. Every milestone and child issue delivered, merged
to `main`, and covered by deterministic tests.

## Delivered scope

| Milestone | Children | Evidence |
|---|---|---|
| #2 — evidence store and run directory | #24–#26 | PRs #98, #112, #137; store landed with the telemetry workstream |
| #3 — CLI/TUI workflow | #27–#29 | PRs #99, #100, #102 |
| #17 — telemetry, metrics, final report | #69–#71 | PR #95 |
| #18 — MCP gateway and GitHub ingestion | #72–#74 | PR #96 |
| #19 — demos and large-context proof | #75–#77 | PR #97 |

## Key artifacts

- `harness/telemetry/` — structured redacted lifecycle events, usage
  metrics derived from the event log, honest VERIFIED/PARTIAL/FAILED
  final reports, the run store with atomic writes and hashed artifacts
- `harness/cli/` — argument/interactive input with advanced constraints,
  live state rendering with evidence-linked tool events, final summary
  and exit-code mapping, the orchestrated command path entry
- `harness/mcp/` — permissioned MCP gateway with provenance, read-only
  GitHub issue/PR ingestion with summarized threads and graceful fallback
- `demos/` — three reproducible judge-ready scenarios (bug fix with
  deterministic recovery, bounded feature, large-context proof) plus the
  one-command runner

## Test evidence

- run directory: atomic writes surviving interruption, whitelisted
  documents, JSONL streams with time/duration/status/evidence, hashed
  artifacts, reconstruction from the directory alone
- CLI: identical shapes for interactive/non-interactive input, actionable
  validation errors, permission and policy enforcement at the boundary,
  all four terminal outcomes mapped to exit codes
- telemetry: secret redaction, metrics/event reconciliation, honest
  reports for verified, partial, and failed sessions
- MCP: provenance on every result, write operations disabled by default,
  graceful degradation keeping local tools working
- demos: one command, all scenarios, judge-checkable final reports

## Known limitations

- live GitHub MCP ingestion requires network access and a token; the seam
  is covered offline by the deterministic fake server
