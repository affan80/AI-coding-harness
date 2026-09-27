# Workstream B — Repository intelligence, registry, context & adapters: completion report

Workstream B is complete. Every milestone and child issue delivered, merged
to `main`, and covered by deterministic tests.

## Delivered scope

| Milestone | Children | Evidence |
|---|---|---|
| #4 — inventory and profile | #30–#32 | PRs #103, #104; inventory landed via feat/repo-profile |
| #7 — tool registry, permissions, normalization | #39–#41 | PR #133; registry/policy landed with the execution tooling |
| #8 — discovery and mapping | #42–#44 | PRs #113, #114, #115; discovery landed via issue-8 merge |
| #9 — context manager | #45–#47 | PRs #116, #117, #134; manager landed via #9 merge |
| #13 — Python and Node adapters | #57–#59 | PR #93 |

## Key artifacts

- `harness/repository/` — bounded inventory with the full exclusion matrix,
  language/manifest/workspace/test detection with command discovery,
  import-edge and test-to-source mapping with explained relationships
- `harness/tools/` — typed registry with role permissions and scope guard,
  file tools with stale-hash guards and a minimal unified-diff engine,
  controlled shell, git evidence tools, checkpoints and rollback
- `harness/context/` — token budgets with output headroom, non-evictable
  P0–P2, strict P7→P3 eviction, candidate scoring and progressive
  L0→L1→L2 assembly with per-item tokens and reasons
- `harness/adapters/` — Python/Node detection, configured-scripts-first
  command builders, workspace roots, structured environment failures

## Test evidence

- inventory: exclusion matrix per category, bounded traversal, determinism
- discovery: bounded tree/text search with evidence refs; symbol/reference
  resolution with lexical fallback when git is absent
- mapping: import edges, test conventions, exclusion of generated content
- context: budget headroom, eviction order, invalidation measurement,
  progressive assembly within budget with P0 protected
- adapters: deterministic detection, no-invention guarantee, package-manager
  selection, workspace roots, environment-failure reporting

## Known limitations

- context ranking is lexical; the semantic layer from PRD §12.5 is
  intentionally deferred (recorded in the release notes)
