# AI engineering workflow

This guide defines how coding agents should work in this repository. It is an engineering protocol, not a claim that a runtime multi-agent framework is installed.

## Goals

- Route a task to the correct code and domain quickly.
- Load only relevant context rather than rereading the whole repository.
- Find root causes from code and reproducible evidence.
- Make small, safe, reviewable changes.
- Use specialist reviews and deterministic checks where they add value.
- Produce an honest, traceable completion report.

## Repository navigation

Start with `AGENTS.md`, `README.md`, and `PROJECT_MAP.md`. Then route by task:

| Task signal | First areas to inspect | Expand to |
|---|---|---|
| PDF, OCR, table, document extraction | `scrapper/research/document_intelligence/`, `scrapper/documents/` | extraction adapters, persistence, fixtures, PDF tests, relevant issues #45–#51 |
| Market/source ingestion | `scrapper/sources/`, `scrapper/workflows/` | source contracts, retries, identity mapping, raw-response storage, tests |
| Database/schema/migration | `scrapper/db/`, migration manifests and audit scripts | DB connection selection, migrations, backup/restore controls, DB tests, issues #5–#6 and #52–#57 |
| CLI/commands | `scrapper/cli/`, `scrapper/cli_groups/`, `scrapper/commands/` | root launchers, compatibility facades, command tests |
| Acquisition/runtime/operations | `ops/`, root launchers, `scrapper/operations/` | Compose, lifecycle, schedules, state/receipt paths and operational docs |
| Financial calculations/research | `scrapper/research/`, `scrapper/validation/` | source lineage, units, period semantics, point-in-time availability, fixtures |
| Quant/ML/backtesting | relevant `scrapper/research/` and `experiments/` modules | dataset construction, labels, feature timestamps, train/test split, leakage tests |
| Security/deployment | workflows, Docker/Compose, dependency locks, config and secrets handling | deployment contract, permissions, health checks, recovery and rollback |

This table is a starting point, not a substitute for tracing actual imports and entry points. Confirm exact paths in the current checkout before editing. Historical experiments and artifacts may still be consumed by tests or scripts.

## CI database and storage isolation contract

Every GitHub Actions job that creates, initializes, migrates, or otherwise writes to an application database or application-owned storage must:

1. Set `TRADING_AI_ENV: test` at job scope.
2. Redirect every configurable application write root (`SCRAPER_STORE_PATH`, `SCRAPER_DB_PATH`, and the related files, snapshots, exports, campaigns, logs, browser-state and backup paths) into a job-specific directory under `$RUNNER_TEMP/trading-ai-test`. Leave `SCRAPER_DOCUMENT_STORE_PATH` unset unless a test explicitly creates its required store sentinel; by default the document store follows the isolated snapshots path.
3. Create the disposable root in a setup step using the runner-provided `RUNNER_TEMP` environment variable, export the paths through `$GITHUB_ENV`, and do this before database initialization or tests.
4. Keep source fixtures read-only; never copy a developer/production runtime identity or mount into CI.
5. Avoid fixed shared database paths such as a common `/tmp/*.sqlite3` file when tests can run concurrently.

The database-writing CI workflows include `.github/workflows/migration-lineage.yml`, `.github/workflows/ml-readiness.yml` and `.github/workflows/postgresql.yml`. They use the same isolation contract, and `scrapper/tests/test_ci_database_isolation.py` guards the workflow path contract against regressions. The runtime write guard in `scrapper/runtime_identity.py` rejects an unset or unsupported environment and, in test mode, rejects known production roots, production mounts, and unverified production identity markers. `TRADING_AI_ENV=test` is necessary but is not itself proof that paths are isolated. CI database and storage targets must remain disposable; the environment flag does not authorize access to production data. Any new CI job that writes application data must follow this contract.

## Portable software tests and retained-source audits

The default offline suite uses committed fixtures or explicitly synthetic inputs in temporary directories. It never opens production data or needs a developer's external drive. CI installs the locked test dependencies (including workbook builders) and Chromium; browser regressions intercept all outbound requests.

Historical audits that certify specific original PDFs, frozen research databases, or operator-supplied workbooks require the original retained bundle. Run them explicitly with `TRADING_AI_RUN_RETAINED_AUDITS=1 python -B -m unittest discover -s scrapper/tests -t . -v` in an isolated checkout with the required bundle. Missing/corrupt evidence then fails normally. Default CI reports these as skipped, not passed; synthetic software checks do not certify historical source bytes or research outcomes. Retained research jobs that write outputs must use isolated copies and temporary output paths.

The November integration correction also permits download recovery audit INSERT operations through the existing routine SQL guard. Explicit audit UPDATE/DELETE operations and unrelated protected writes remain denied. This authorizer is an application write boundary, not protection against arbitrary SQL such as INSERT OR REPLACE. Source changes do not activate a release or clear retained production stops; deployment and recovery remain separately authorized operations.

## Task execution

### 1. Convert the request into a contract

Write down:
- user-visible behavior or defect;
- inputs, outputs and invariants;
- acceptance criteria;
- affected data/API/CLI contracts;
- explicit exclusions and safety boundaries.

If the request is ambiguous but safely actionable, state the assumption. Ask one focused question only when a decision blocks safe implementation.

### 2. Build a focused evidence bundle

Inspect only what is needed:
- entry point and implementation;
- direct callers and relevant imports;
- data model/schema and migration history if persistence is involved;
- configuration and environment selection;
- tests and fixtures;
- current documentation and historical decisions;
- logs or runtime evidence when available and authorized.

For every important finding, record path + symbol/line or command output. Label findings as **confirmed**, **likely**, or **unknown**.

### 3. Diagnose and plan

Do not start with a patch. Establish the failure with a failing test, a reproducible input, a trace, or a clear violated contract. Then propose the smallest change and explain compatibility and side effects.

### 4. Implement

- One implementation owner per change.
- Preserve existing public interfaces unless a versioned migration is part of scope.
- Avoid broad refactoring, new frameworks and new dependencies without evidence.
- Add regression tests for the reproduced failure.
- Keep generated files and indexes deterministic and exclude private/runtime data.

### 5. Verify by impact

Choose checks based on the changed surface:
- pure logic: unit tests, edge cases, type/lint checks if configured;
- API/CLI: contract tests and compatibility tests;
- DB/schema: isolated DB, migration from zero and supported prior versions, integrity checks, rollback/restore proof as applicable;
- document extraction: saved source fixtures, page/table/cell completeness, provenance and OCR/native-text paths;
- financial/quant: decimal/unit checks, point-in-time availability, leakage and realistic execution assumptions;
- operations/deployment: configuration validation, health/readiness, bounded behavior, rollback and explicit human approval.

Do not run a production workflow to verify a code change.

### 6. Independent review

For high-impact changes, ask a separate reviewer to inspect the diff and test evidence. Review should look for:
- logic gaps and edge cases;
- untraced callers or changed contracts;
- data loss/corruption or provenance loss;
- security and concurrency issues;
- missing regression tests;
- deployment and rollback risks.

A review is not a substitute for executable tests.

## Evidence and status vocabulary

Use these terms consistently:
- **PASS:** command/check ran and met its stated criteria.
- **FAIL:** command/check ran and did not meet criteria.
- **BLOCKED:** could not run because a prerequisite or authorization was missing.
- **NOT RUN:** not attempted.
- **NOT APPLICABLE:** the check does not apply, with a short reason.
- **UNKNOWN:** evidence is insufficient to make a claim.

Never convert BLOCKED, NOT RUN or UNKNOWN into PASS.

## Completion report

Every non-trivial task should report:
- task and acceptance criteria;
- confirmed root cause or design decision;
- changed paths and behavior;
- tests/checks with exact commands and outcomes;
- independent review findings;
- data/API/CLI compatibility impact;
- risks, rollback/migration notes and unverified items;
- whether production systems, persistent data or external services were touched.

## Production readiness

“Tests pass” is not synonymous with “production-ready”. A production-readiness claim additionally needs relevant evidence for configuration, secrets, security, data integrity, concurrency, performance, observability, deployment, backup/recovery and rollback. State which of these were actually verified for the task.
