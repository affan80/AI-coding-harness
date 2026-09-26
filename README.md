# Autonomous AI Software Engineering Harness

Hackathon project for an autonomous coding harness that accepts a repository and a natural-language objective, understands the codebase, plans work, edits code, verifies the result, recovers from failures, and returns evidence.

## Product documents

- [Product Requirements Document](docs/PRD.md)
- [4-person execution plan](docs/TEAM_PLAN.md)
- [Contribution rules](CONTRIBUTING.md)
- [Implementation backlog](https://github.com/affan80/pentester-AI-harness/issues)

## Core product flow

```text
Repository + Objective
        ↓
Intent / Goal Analysis
        ↓
Repository Intelligence
        ↓
Context Assembly
        ↓
Planning
        ↓
Execution + Tools
        ↓
Verification
   ┌────┴────┐
 PASS       FAIL
   │          │
 Audit     Recovery
   │          │
   └────┬─────┘
        ↓
 Evidence-backed result
```

The MVP exposes one user workflow rather than separate build, fix, audit, or refactor modes. The harness infers the strategy from the objective.

## CI/CD

Pull requests and pushes to `main` run `.github/workflows/ci.yml`. The workflow validates repository hygiene and automatically runs Python and Node checks when those project files appear.

Tags matching `v*` run `.github/workflows/release.yml`, re-run the repository checks, create a source archive and SHA-256 checksum, and upload them as release artifacts. A deployment target is intentionally not assumed until the runtime destination is chosen.

Run the same checks locally with:

```bash
./scripts/ci.sh
```
