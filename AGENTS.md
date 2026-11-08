# AI coding agent instructions

These instructions apply to the entire repository. Read the relevant module documentation and narrower instructions before changing code.

## Mission

Make the smallest correct, maintainable, evidence-backed change that satisfies the user's task. Optimize for correctness, data integrity, security and operational safety—not the number of files changed, agents invoked or lines written.

## First-read order

1. This file.
2. `README.md` for project purpose, current operating constraints and production boundaries.
3. `PROJECT_MAP.md` for repository navigation and warnings about operational/historical paths.
4. `docs/STATUS.md` and the linked current-status/supersession index when task freshness matters. Treat dated status and architecture documents as snapshots, not live truth.
5. `docs/ai-engineering/README.md` for the task workflow and verification report contract.
6. The smallest relevant set of source files, callers, tests, migrations, configuration and current documentation.

Do not read the entire repository by default. Use targeted search, imports, entry points, test names and the repository map to expand context only as needed.

## Repository-specific boundaries

- The canonical Python application package is `scrapper/`; its actual spelling is intentional.
- Important operational behavior also exists outside that package, including root launchers, `ops/`, `scripts/`, Docker/Compose files and GitHub workflows. Trace the actual entry point before editing.
- `PROJECT_MAP.md` explains that `artifacts/`, `experiments/`, `OLD/`, operational state and retained evidence can have active consumers. Never assume these are disposable.
- Preserve CLI commands, aliases, flags, Python import paths, database migration identities, source provenance and accepted data contracts unless the task explicitly requires a compatible change.
- Existing workflow definitions and operational records are evidence, not proof of current live runtime health. State which source was checked and when.

## Mandatory task lifecycle

1. **Clarify scope internally.** Extract expected behavior, constraints, acceptance criteria and forbidden side effects. Ask the user only if a missing decision blocks safe progress.
2. **Route the task.** Identify domains such as documents/PDF, sources/market data, database/migrations, CLI, operations, research/quant, security or deployment.
3. **Collect evidence.** Find entry points, implementation, callers, data models, relevant configuration, tests and docs. Record exact paths and symbols. Distinguish confirmed facts from hypotheses.
4. **Trace impact.** Follow imports/callers and identify persisted data, external requests, concurrency, public interfaces, runtime state and backward-compatibility effects.
5. **Diagnose before editing.** Reproduce the issue or establish a failing test/observable contract. Do not treat an issue description or an LLM guess as proof.
6. **Plan the smallest safe change.** List files to change, behavior to preserve, tests to add/run and risks. Avoid opportunistic refactors.
7. **Implement within authorization.** Keep changes scoped. Do not change production runtime or data as part of a code task.
8. **Verify.** Run targeted tests first, then relevant regression checks and static checks. Inspect the final diff. Report exact commands and outcomes.
9. **Independent review.** For material logic, data, security, database or operational changes, use a separate review pass/agent where available. The implementer must not self-approve.
10. **Report honestly.** State what changed, evidence, tests passed/failed/not run, residual risks, migration/rollback considerations and required human action.

## Agent use

- Use specialist agents only when the task benefits from independent expertise or parallel read-only investigation.
- Give each agent a bounded scope, exact questions, allowed paths/actions and a required evidence-based output.
- Parallelize independent analysis/reviews; do not allow multiple agents to write overlapping files.
- Assign one implementation owner for a change.
- Require reviewers to cite paths, symbols, tests or command output. Reject unsupported claims.
- Never let an agent approve its own implementation or invent test results.
- If subagents are unavailable, perform the same roles as explicit sequential review passes; do not claim that separate agents ran.

## Production and safety rules

The following require explicit user authorization before execution: production database access or writes, applying migrations to persistent/production stores, deployment/restart, acquisition/runtime activation, deleting or rewriting retained data, credential changes, destructive filesystem operations, and external actions that alter user or third-party state.

- Prefer isolated temporary databases and test fixtures.
- Never infer that a database is disposable from its filename or location.
- Never expose secrets, tokens, credentials, private datasets or production records in generated maps, logs, prompts or reports.
- Do not weaken CI isolation or bypass existing migration/data-safety controls.
- Do not make network calls from tests unless the test explicitly mocks or isolates them and the task requires them.
- Preserve source bytes and provenance for financial/document data.
- For financial calculations, use exact decimal/typed units and verify formulas against source evidence; do not use LLM output as the calculation authority.
- For quantitative research, explicitly guard against look-ahead leakage, survivorship bias and unrealistic execution assumptions.

## Definition of done

A task is not complete merely because code was written or an agent says it is done. Completion requires:
- Acceptance criteria mapped to implementation and tests.
- Relevant tests/checks actually executed, with results recorded.
- No unexplained regression or unrelated diff.
- Security, data integrity and operational impact reviewed where applicable.
- Documentation updated when behavior/contracts changed.
- Unverified checks and residual risks disclosed.
- No unauthorized production side effects.

## Final response format

Provide:
1. **Result:** completed / partially completed / blocked.
2. **Root cause or design decision:** concise and evidence-backed.
3. **Files changed:** paths and purpose.
4. **Verification:** exact commands and pass/fail/not-run status.
5. **Risks and follow-ups:** only concrete remaining work.
6. **Production status:** explicitly state whether production systems/data were touched (normally no).
