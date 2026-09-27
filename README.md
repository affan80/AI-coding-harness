# Autonomous AI Software Engineering Harness

Hackathon project for an autonomous coding harness that accepts a repository and a natural-language objective, understands the codebase, plans work, edits code, verifies the result, recovers from failures, and returns evidence.

## Product documents

- [Product Requirements Document](docs/PRD.md)
- [4-person execution plan](docs/TEAM_PLAN.md)
- [Parent and sub-issue breakdown](docs/ISSUE_BREAKDOWN.md)
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

## Run everything with one command

`make` runs the full pipeline: venv setup, repository hygiene, lint, the test suite, the judge demo scenarios, and a live TUI session on the demo fixture.

```bash
make
```

Each stage is also available as a standalone target:

- `make setup` — create `.venv` (via `uv`) and install dev dependencies; idempotent
- `make hygiene` — repository hygiene checks (`./scripts/ci.sh`)
- `make lint` — `ruff check .`
- `make test` — the pytest suite
- `make demo` — demo scenarios A/B/C with evidence reports under `runs/demo/`
- `make tui` — live TUI session on `demos/fixtures/feature_repo`; the M1 pipeline stops after repository inspection, so `PARTIAL` (exit code 1) is the expected outcome there
- `make clean` — remove run artifacts and caches
- `make help` — list all targets

## CI/CD

Pull requests and pushes to `main` run `.github/workflows/ci.yml`. The workflow validates repository hygiene and automatically runs Python and Node checks when those project files appear.

Tags matching `v*` run `.github/workflows/release.yml`, re-run the repository checks, create a source archive and SHA-256 checksum, and upload them as release artifacts. A deployment target is intentionally not assumed until the runtime destination is chosen.

Run the same checks locally with:

```bash
./scripts/ci.sh
```
