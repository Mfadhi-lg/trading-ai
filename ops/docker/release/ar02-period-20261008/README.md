# AR-02 replacement with shared IST periods

Replacement: `trading-ai-ar02-period-release@sha256:a7025c3e70ea14939874aa2c1337a41845e3311ba9b5a4fc1a1004c6d6f4c16d`. Baseline: accepted combined AR-02, retained unchanged.

Exact source delta: shared reporting-period helper, NSE acceptance of actual source periods beyond query priorities, scheduler shared priorities and single-quarter historical overrides; three focused test inputs. Dockerfile, dependencies, migrations and all other application inputs match the combined baseline. Provenance, SBOM and OCI archive are retained with this release.

NSE activation: `python3 ops/activation/nse_results.py --execute` from the repository root. Routine acquisition needs no `--period`; optional `--period 2024-03` selects a historical query. Scheduler is repinned in compose.candidate.json and remains locked. All 21 candidate roles have disabled identities and no default services. The standalone NSE identity enables only the prepared bounded role.

Maximum operation: two official requests, no redirects/retries, 30-second request timeout, 300-second deadline, total 10 MiB, 6,000 accepted filing rows and 100 new queue entries. Existing owner, schema, physical-marker, source and queue protections remain active; BSE holds stay unchanged. Live checks execute as part of the bounded operation.

Preparation only: no production acquisition or scheduling started. Reuse schema075, migration/recovery and unaffected evidence from the combined package. No migrations or recovery rehearsals repeated. Live NSE acceptance remains unproven. Prior images and documents are retained.
