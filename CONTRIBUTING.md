# Contributing

This repository is a four-person hackathon project. Optimize for integration speed, readable code, and verified behavior.

## Branches

Use short-lived branches:

```text
feat/<area>-<change>
fix/<area>-<bug>
docs/<change>
```

Do not develop directly on `main` unless the team explicitly agrees for an emergency hackathon fix.

## Pull requests

Every PR must:

- solve one coherent problem
- describe the behavior changed
- list the files/areas affected
- include the command(s) used to verify the change
- pass CI
- avoid unrelated formatting/refactors
- keep public contracts backward-compatible unless the PR explicitly coordinates the contract change

At least one teammate should review behavior-changing changes before merge when time allows. Changes to shared models, the orchestrator state machine, tool schemas, or context contracts should be reviewed by the teammate consuming that contract.

## Architecture rules

1. The orchestrator is the single owner of session state and state transitions.
2. Agents are logical roles over the shared model runtime; do not create a new service/model process just to add a role.
3. The executor/tool layer is the primary path allowed to modify target repository source.
4. Verification uses deterministic tools where available. A model statement is not test evidence.
5. Context must be retrieved and budgeted. Do not send the entire repository to the model.
6. Large tool output is summarized in context and retained as an evidence artifact.
7. MCP is for external integrations. Keep local file/search/git/test operations native unless there is a concrete reason to move them.
8. Respect user scope, command policy, timeout, and retry budgets at tool boundaries.
9. Prefer the existing project command/configuration over inventing a second build/test path.
10. Add an abstraction only when at least one current behavior needs it.

## Code ownership by area

The working ownership model is documented in [`docs/TEAM_PLAN.md`](docs/TEAM_PLAN.md). Ownership exists to reduce conflicts, not to prevent teammates from helping each other.

Before changing another owner's core area, coordinate the shared contract first. Small fixes needed to complete an integrated PR are fine when clearly described.

## Tests and verification

Run:

```bash
./scripts/ci.sh
```

The script automatically runs checks appropriate to files present in the repository. A PR that changes Python or Node behavior should include focused tests for that behavior once those stacks are introduced.

## Commits

Use concise imperative messages, for example:

```text
feat(context): add token-budgeted working set
fix(verification): preserve failing command evidence
docs: define agent tool permissions
```

## Merge conflicts

Do not commit unresolved conflict markers. CI rejects them.

## Secrets

Never commit API keys, access tokens, model credentials, `.env` secrets, private SSH keys, or captured credentials. Use environment variables and provide `.env.example` entries when configuration becomes necessary.

