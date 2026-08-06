# Screener Incremental Discovery and Collection Plan

_Created: 2026-08-05_

## 1. Purpose

Add an authenticated Screener result-date discovery layer in front of the
existing source-faithful company scraper. The discovery layer identifies which
companies have entered a reporting period, compares them with durable accepted
collection state, and queues only new companies or companies whose latest result
period has advanced.

The result screen is a discovery source only. It does not replace company-page
collection. Every queued company must still pass through the existing full
Screener pipeline to collect and validate:

- identity and source identifiers;
- quote and top ratios;
- quarterly results;
- profit and loss plus growth blocks;
- balance sheet, cash flow and annual ratios;
- quarterly and yearly shareholding;
- company profile, Pros and Cons;
- peer data from its first-party supplemental request; and
- document-link metadata.

After a company is accepted, D09 continues delayed-artifact reconciliation for
presentations, transcripts, recordings and rating reports.

## 2. Explicit decisions

### Included

- Authenticated HTML discovery from `/screen/raw/?query=...`.
- Immutable storage of every permitted screen response used for discovery.
- Source-faithful parsing of the result table before normalization.
- Durable result-period observations and an incremental company queue.
- Direct use of the company link supplied by the screen.
- Bounded, resumable workers that call the existing company pipeline.
- Coverage, retry, failure and session-expiry reporting.
- Docker packaging and scheduled execution after the queue is proven.

### Excluded

- The Screener Excel export. It must not be used for company fundamentals or as
  the discovery implementation.
- Treating the screen table as a replacement for company-page data.
- Trendlyne live backfill, which remains frozen.
- AMC crawling; AMC portfolio acquisition remains local-workbook only.
- CAPTCHA, login, pagination or rate-limit bypasses.
- Starting an unbounded universe run before the rollout gates in this document
  pass.

## 3. Confirmed authenticated HTML contract

The locally configured `~/.screener_session` successfully authenticated this
query without redirecting to login:

```text
https://www.screener.in/screen/raw/?query=Last%20result%20date%20%3E202605
```

The inspected response reported:

```text
1308 results found: Showing page 1 of 53
```

The response currently contains:

- `[data-page-info]` with result and page counts;
- `[data-page-results] table` containing the visible result table;
- one `<tr data-row-company-id="...">` per company;
- a company anchor such as `/company/NOIDATOLL/consolidated/`,
  `/company/PGHH/`, or `/company/517320/`;
- exact visible company name and source-order position;
- exact display cells for the selected ratios; and
- `Qtr Date` / `Last result date` values such as `202606.00`.

The parser must identify columns from header evidence, not fixed offsets. A
changed, missing or duplicated `Last result date` column blocks acceptance.

## 4. Query semantics

Screener represents `Last result date` as a numeric `YYYYMM` value. The source
display may include a decimal suffix such as `202606.00`; normalize it to
`2026-06` while retaining the original display.

The supplied broad query:

```text
Last result date < 202605 AND Last result date > 202202
```

is an initial recently-reporting universe range, not one quarter. It spans more
than four years and excludes companies outside those strict boundaries.

The current-cycle query:

```text
Last result date > 202605
```

selects companies whose latest reported period is June 2026 or later.

All queries must be stored in canonical decoded form and as the exact requested
URL. Thresholds must be explicit configuration, never silently derived from the
machine date.

Suggested configured query roles:

```text
initial_universe
current_reporting_cycle
revision_audit
```

## 5. Current blocker: complete permitted enumeration

The first current-cycle HTML page contains only part of the 1,308-result set.
Ordinary enumeration needs `page=` URLs. Screener's current robots guidance
disallows automated `page=`, `sort=` and `limit=` query URLs. The session cookie
authenticates the account but does not override that guidance or the site's
terms.

Therefore the implementation must fail closed when a screen reports more rows
than were captured. It must never label page 1 as the complete universe.

Before enabling automatic multi-page discovery, obtain explicit authorization
for the required access pattern or replace initial-universe discovery with a
permitted official exchange security/result source. Until then, allowed modes
are:

- parse operator-supplied saved screen pages offline;
- perform one explicitly authorized first-page smoke; or
- use a permitted external universe list and retain the screen only as a bounded
  cross-check.

Do not evade the pagination restriction by generating large numbers of narrow
queries, manipulating parameter order, or partitioning ratios solely to recreate
pagination.

## 6. Identity changes required

The screen does not always provide an NSE symbol. Company links may contain:

- an NSE-like symbol;
- a BSE numeric code;
- a consolidated suffix; or
- a standalone/default path.

The current `scrape-one --symbol` path assumes an NSE symbol and constructs its
own URL. Universe collection must instead treat the screen-provided company path
as discovery identity until the company page proves stronger identifiers.

Required identity rules:

1. Preserve `data-row-company-id` as `screen_row_company_id`; do not assume it is
   the existing verified Screener company ID.
2. Preserve the exact company href and derive a normalized source path.
3. Fetch the exact discovered path after validating that it is a relative
   `/company/.../` URL on `screener.in`.
4. Parse the company page and resolve the durable company first by verified
   Screener source company ID, then by current NSE or BSE identifier.
5. Permit a company with a valid BSE identifier and no NSE symbol.
6. Never merge two companies from name text alone.
7. Preserve whether the discovered path requested consolidated or standalone
   reporting. The two scopes remain separate accepted identities.

The existing `ensure_company` and company parser must be generalized so NSE is
not mandatory when valid BSE/Screener identity is available.

## 7. New parser module

Add:

```text
scrapper/sources/screener/result_screen.py
```

Suggested immutable models:

```text
ParsedResultScreen
  query_raw
  requested_url
  result_count
  page_number
  page_count
  table_headers
  rows
  is_complete_capture

ResultScreenRow
  source_position
  serial_display
  screen_row_company_id
  company_name_raw
  company_href_raw
  company_path_normalized
  reporting_scope_hint
  last_result_date_display
  result_period
  source_cells
```

Parser requirements:

- BeautifulSoup/deterministic parsing only.
- Reject login, registration, CAPTCHA and access-denied pages.
- Require exactly one result table and one result-date column.
- Preserve every header and cell in source order, including blank cells.
- Require a company link and `data-row-company-id` for every accepted row.
- Validate `YYYYMM` structurally and calendar-wise without binary floats.
- Reject duplicate source positions or conflicting identities.
- Compare captured rows with `[data-page-info]` and mark partial coverage.
- Never return partial success as a complete screen.

## 8. HTTP changes

Extend `scrapper/core/http.py` with a separate response classifier and fetcher:

```text
fetch_screener_result_screen(url, cookie_path=...)
```

It must:

- require an available session cookie;
- use the existing secret loader without logging values;
- accept only HTTPS `www.screener.in/screen/raw/` URLs;
- allow only the configured `query` parameter unless a separately approved
  access policy permits more;
- follow redirects only to detect and reject login/registration;
- require `[data-page-info]`, `[data-page-results]` and a result table;
- stop on `401`, `403`, `429`, CAPTCHA or unexpected login;
- record `auth_mode='authenticated'`; and
- never include cookies, CSRF values or response request headers in exports or
  errors.

The existing company and peer fetchers continue to load the same secret file.

## 9. Database migration

Add migration `031_screener_result_discovery.sql` with logical tables similar to:

```text
screener_screen_runs(
  screen_run_id PK,
  query_role,
  query_raw,
  requested_url,
  fetch_id,
  blob_id,
  parser_name,
  parser_version,
  observed_at_utc,
  result_count,
  page_number,
  page_count,
  captured_row_count,
  coverage_status,
  validation_status,
  UNIQUE(blob_id, parser_name, parser_version)
)

screener_screen_source_cells(
  screen_run_id,
  row_position,
  column_position,
  row_label_raw,
  column_label_raw,
  raw_text,
  normalized_display_text,
  link_url,
  cell_role,
  PRIMARY KEY(screen_run_id, row_position, column_position)
)

screener_result_candidates(
  candidate_id PK,
  screen_run_id,
  source_position,
  screen_row_company_id,
  company_name_raw,
  company_href_raw,
  company_path_normalized,
  reporting_scope_hint,
  last_result_date_display,
  result_period,
  discovered_at_utc,
  UNIQUE(screen_run_id, source_position)
)

screener_company_collection_state(
  company_path_normalized,
  reporting_scope,
  latest_discovered_result_period,
  latest_accepted_result_period,
  latest_accepted_parse_id,
  updated_at_utc,
  PRIMARY KEY(company_path_normalized, reporting_scope)
)

screener_company_queue(
  queue_item_id PK,
  candidate_id,
  company_path_normalized,
  reporting_scope,
  result_period,
  queue_reason,
  status,
  attempt_count,
  next_attempt_at_utc,
  claim_token,
  claimed_at_utc,
  claim_expires_at_utc,
  accepted_parse_id,
  last_error_json,
  created_at_utc,
  updated_at_utc,
  UNIQUE(company_path_normalized, reporting_scope, result_period)
)

screener_company_queue_events(
  queue_event_id PK,
  queue_item_id,
  event_at_utc,
  from_status,
  to_status,
  worker_id,
  evidence_json
)
```

Queue statuses:

```text
pending
claimed
running
accepted
failed_retryable
failed_terminal
manual_review
skipped_already_accepted
```

All queue transitions append an event. Accepted parses, response blobs and
source rows are never deleted when pointers advance.

## 10. Incremental enqueue rules

For every accepted complete screen candidate:

1. If no collection state exists, enqueue with `queue_reason='new_company'`.
2. If `result_period` is later than `latest_accepted_result_period`, enqueue with
   `queue_reason='result_period_advanced'`.
3. If the same company/scope/period is already pending, running or accepted,
   reuse it; do not duplicate work.
4. If the screen period is older than accepted state, retain the observation and
   do not move the pointer backward.
5. If identity is ambiguous or the period is invalid, retain the source row and
   route it to manual review.
6. A partial screen capture may persist evidence but may not represent complete
   universe coverage or enqueue an alleged full-universe plan automatically.

The accepted result period must come from the parsed company page's quarterly
table, not merely from the screen claim. A mismatch blocks queue acceptance and
remains queryable.

## 11. Company worker changes

Add a direct-path company pipeline surface that reuses existing parsing,
validation, acceptance and export logic:

```text
scrape_screener_company_path(company_path, reporting_scope_hint, ...)
```

Required behavior:

- validate and canonicalize the discovered path;
- fetch main and peer responses through one authenticated session policy;
- preserve requested and final URLs;
- parse and verify source company identity;
- prove the expected screen result period is present in quarterly results;
- accept only when the existing manifest and all validation gates pass;
- update collection state only after acceptance;
- append queue outcome events; and
- keep failed rows retryable or terminal with explicit reasons.

The bounded worker must claim rows with `BEGIN IMMEDIATE` and expiring unique
leases, following the overlap-safe pattern used by D09.

Rate limiting must apply across the entire worker process, not restart for each
company:

```text
one Screener host limiter
configured base delay + jitter
maximum items per invocation
stop-host behavior on 401/403/429/CAPTCHA/login
bounded retry schedule
```

## 12. CLI additions

Add commands with structured JSON output:

```bash
# Read a saved response and print the exact discovery plan without writes.
python3 -m scrapper.cli screener-discover-results \
  --query-role current_reporting_cycle \
  --query "Last result date > 202605" \
  --fixture /path/to/saved-screen.html \
  --dry-run

# Perform one allowed authenticated discovery request and persist its evidence.
python3 -m scrapper.cli screener-discover-results \
  --query-role current_reporting_cycle \
  --query "Last result date > 202605"

# Show exactly what would be claimed without changing queue state.
python3 -m scrapper.cli screener-company-worker \
  --due --limit 5 --dry-run

# Claim and execute one bounded batch.
python3 -m scrapper.cli screener-company-worker \
  --due --limit 5

# Export discovery, queue, acceptance and failure coverage.
python3 -m scrapper.cli screener-discovery-coverage
```

If the screen capture is incomplete, live discovery must exit non-zero for
full-universe mode while still reporting and preserving the partial evidence.

## 13. Coverage report

Add a deterministic JSON report containing:

- query role, raw query and exact URL;
- source content hash and observation time;
- reported result/page counts versus captured rows;
- complete/partial/rejected coverage decision;
- candidates grouped by result period and reporting-scope hint;
- new, advanced, unchanged, ambiguous and stale candidate counts;
- pending, claimed, running, accepted, retryable, terminal and review queue
  counts;
- accepted parse IDs and company export paths;
- validation failures and identity/result-period mismatches; and
- session-expired, rate-limited and blocked-host outcomes.

No downstream consumer may treat an incomplete discovery run as complete
universe coverage.

## 14. Tests and fixtures

All parser and queue tests remain offline.

Required saved/synthetic fixtures:

1. Authenticated result screen with symbol, numeric-BSE and consolidated links.
2. Empty valid screen.
3. Multiple-page metadata with only page 1 captured.
4. Login/register redirect body.
5. CAPTCHA/access-denied body.
6. Changed/missing/duplicated result-date header.
7. Invalid result period and blank visible cells.
8. Duplicate company path with conflicting screen row identity.
9. New company, unchanged period and advanced-period queue cases.
10. BSE-only company page.
11. Screen-period/company-quarter mismatch.
12. Overlapping worker claims and expired-lease recovery.
13. `401`, `403`, `429`, parse drift and transient network failure outcomes.

Tests must prove:

- exact source-cell preservation;
- deterministic normalized period parsing;
- no name-only identity merge;
- incomplete screen coverage fails closed;
- dry-run is database/snapshot/queue write-free;
- discovery and enqueue are idempotent;
- the same company/period cannot be queued twice;
- accepted state never moves backward;
- overlapping workers never claim the same row;
- a stopped/expired worker can resume safely;
- a company is marked accepted only after the existing full company parser and
  validations pass; and
- session values never appear in logs, database rows, exports or exceptions.

## 15. Docker changes after queue proof

Add:

```text
Dockerfile
compose.yml
.dockerignore
docker/entrypoint.sh (only if required; no secret handling in shell output)
```

Container requirements:

- run as a non-root user;
- mount `scrapper/store/` as a persistent volume;
- mount the Screener session file as a read-only secret;
- add configurable secret path support rather than assuming a host home path;
- never copy the session into an image layer;
- include a database migration/init step;
- expose a health check that does not contact Screener;
- run discovery and workers as separate scheduled jobs or commands;
- allow only one active host worker unless lease/rate-limit configuration proves
  otherwise; and
- preserve graceful shutdown so unprocessed leases can expire/recover.

Suggested environment variables:

```text
SCREENER_SESSION_FILE=/run/secrets/screener_session
SCRAPER_DB_PATH=/data/scraper.db
SCRAPER_STORE_PATH=/data
SCREENER_THROTTLE_SECONDS=4
SCREENER_THROTTLE_JITTER=1.5
SCREENER_WORKER_LIMIT=5
```

Do not start the Docker scheduler merely because the container builds.

## 16. Rollout sequence

### Gate A — Offline parser

- Save/sanitize the permitted first-page fixture.
- Complete source-faithful parser and golden tests.
- Prove incomplete-page detection.

### Gate B — Persistence and queue

- Apply migration on a temporary copy and an empty database.
- Prove discovery/enqueue idempotency and overlap safety.
- Inspect the accepted discovery JSON manually.

### Gate C — One authenticated discovery smoke

- Perform one query-only first-page request.
- Confirm authenticated status, content hash and structural fingerprint.
- Do not paginate or enqueue a claimed full universe when coverage is partial.

### Gate D — Five-company worker pilot

- Requires a complete permitted candidate input.
- Print exact paths before execution.
- Run at most five companies with shared host throttling.
- Inspect acceptance, failures and exports.

### Gate E — Docker dry run

- Build image.
- Mount a temporary database/store and read-only secret.
- Run migration, discovery dry-run and worker dry-run.
- Verify restart/lease recovery.

### Gate F — Scheduled incremental operation

- Requires authorization for the actual discovery access pattern.
- Begin with current-cycle discovery at a conservative cadence.
- Fetch only new/advanced companies.
- Review coverage and failure reports before increasing limits.

## 17. Definition of done

This change is complete only when:

- the authenticated screen HTML is stored immutably and parsed source-faithfully;
- every candidate retains source position, row ID, exact link/name and result
  period display;
- partial multi-page coverage is explicit and cannot masquerade as complete;
- new/advanced companies create one durable queue item;
- unchanged/accepted companies create no fetch work;
- symbol, BSE-only and consolidated paths resolve safely;
- the existing full company pipeline accepts each completed queue item;
- result-period mismatch, login, CAPTCHA and rate limits fail closed;
- queue claims and events are overlap-safe and auditable;
- coverage reports distinguish discovery gaps from technical failures;
- Docker keeps the database/store durable and the session secret out of images
  and logs;
- the complete offline suite passes; and
- no universe batch begins before the authorization and rollout gates above are
  satisfied.
