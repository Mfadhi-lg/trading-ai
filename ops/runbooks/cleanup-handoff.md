# Final cleanup candidate handoff (C27.1)

This is the offline cumulative cleanup candidate. C28.1 is the sole promotion
into canonical main. Promotion does not authorize deployment. The existing
canonical checkout and production image remain the rollback reference.

## Capability owners and compatible entries

- Operator commands: `scrapper/cli/` dispatches grouped `commands/` handlers;
  `scraper`, `scraper-monitor`, package/module entry points and existing flags
  remain compatibility surfaces. SQL and long workflows belong to their owners.
- Screener and result acceptance: `sources/screener/`, `workflows/screener/`,
  `db/screener/`; `pipeline/` facades retain public calls. Standalone acceptance,
  result revisions, immediate document expectations and document-only delayed
  refresh keep separate financial and document evidence.
- Exchange identity, filings and schedules: `sources/exchange/`,
  `workflows/exchange/`, `db/exchange/`, `operations/coordinator/exchange.py`.
  Collection and periodic opt-in owners are in `operations/coordinator/`.
- Logical documents, download queues, content, recovery and reconciliation:
  `db/documents/`, `documents/download/`, `documents/recovery/`,
  `documents/reconciliation/`, `workflows/documents/`. Failed URLs, alternate
  official sources, saved pages and browser recovery remain distinct strategies.
- Browser acquisition: `documents/recovery/browser_rating/` persistence and
  bridge owners; `ops/host/` handles host browser lifecycle. Explicit reviewed
  run selection and publisher/host authorization remain required.
- Extraction, ratings, transcripts and media: `documents/extraction/`, existing
  normalization owners and `documents/readiness/`; `operations/workers/`
  supplies durable processing, renewal, health and drain. Keep original bytes,
  derivatives, technical failures, partial output and unknown states distinct.
- Mutual funds: `sources/amc_workbook/`, `sources/mfdata/`,
  `workflows/mutual_funds/`, `db/mutual_funds/`. Trendlyne remains supported in
  `sources/trendlyne/` and its retained reporting/workflow interfaces. Source
  versions, workbook safety, accepted schemes and units/provenance remain explicit.
- Financial and document features: `research/features/`; governed Focus and
  labels: `research/governance/`, `research/focus/`. Public replay/cutoff contracts
  do not certify historical universe eligibility or independent document
  ingestion timestamps. B2 is superseded audit history, not production labels.
- Reports and operator views: `exports/`, `view/` and compatible command handlers.
  Schema connectivity/application: `db/common/` and immutable `db/migrations/`.

## Release procedure and data paths

Read the role-specific runbooks here and `ops/campaigns/retirement-c235.md`.
Configured paths and aliases are defined in `scrapper/config.py`: existing STORE,
DB_PATH, FILES, SNAPSHOTS and DOCUMENT_STORE stay valid. Campaign, log, browser
state and backup roots are explicit. Keep `/data` and external `/documents`
identities and persisted local paths unchanged; no corpus move is required.

Before activation, review the actual schema ledger and pending migration set
against the checksum manifest. Apply only reviewed migrations explicitly; neither
imports, help nor routine worker startup authorize schema replay. The two 065
filenames retain distinct identities. Rebuild and pin an image containing this
exact source projection: the C05.3 image referenced by prepared Compose is a
historical test runtime and does not contain the later owners. Reinstate actual
release/image identities, runtime sentinel and target guards before any writer.

Selected coordinator and role transitions require stop/drain of their old owners,
one replacement owner, preserved pending queues/claims and bounded observation
of progress, retries, contention, outputs and shutdown/resume. Do not start old
and new owners against an unfenced queue. Inspect saved shutdown state so wake
cannot resurrect the dated June owner beside the quarter replacement. Opt-in
periodic dispatch remains opt-in; daemon and market-cap-monitor loops retain
explicitly documented limitations. Do not infer activation from passing tests.

## Campaigns, history and unresolved work

The June quarter processing replacement retains quarter 2026-06-30, batch 10,
60-second cadence and CPU/resource isolation. Five June partial contents remain
partial; one pending download and two alternate-source candidates stay with their
acquisition/recovery owners. See the C23.5 disposition for exact content IDs.

Frozen browser run browser-ratings-20261001 accounts for 5,627 succeeded,
133 failed and 61 review-required acquisition jobs; processing has 5,602 done and
25 failed. These are historical counts, not new live checks. Failures/review and
partial evidence need operator investigation; no automatic reset/requeue is
approved. Four reusable browser service templates require an explicit run ID.

Copied A2/B1/B2 trees, four root diagnostics, one URL facade and the generated B2
export have checksum-verified retention indexes under C26.1 through C26.4 in the
canonical evidence tree. Historical scripts require a separately restored archive
layout. Restore after conflict checks; do not run audit code as runtime setup.
All prior patches/harnesses remain immutable and separately accessible.

Remaining release work: C28.1 integration verification, image rebuild/pinning,
reviewed pending migrations, live state reinventory and separately authorized
canary/observation. Single-host SQLite/local locks are retained; no distributed
ownership, starvation, full-corpus integrity or live throughput claim is made.
Future document AI, ML, news, market context and trading remain deferred under
`docs/architecture/FUTURE_DOMAIN_CONTRACT.md`; no runtime placeholders are added.

Rollback before activation: select the prior compatible release and omit the
candidate. After authorized activation, drain replacement owners, reconcile saved
claims/manifests with cached outputs, retain additive schema and all later writes,
and resume one compatible old topology. Do not restore an old database or drop
queue/schema/history records as routine code rollback.
