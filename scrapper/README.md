# Scraper

Phase 1 collects source-faithful and normalized market data from Screener and
user-supplied official AMC portfolio workbooks. The 12-month PPFAS vertical slice
is complete for Jul-2025 through Jun-2026, and the M08 generalization pilot adds
official May/June 2026 Groww and Motilal Oswal workbooks. All three publisher
formats are matched primarily by ISIN and produce consecutive stock-level
accumulation features for all schemes and active equity schemes.

Screener company-page coverage is already proven across the five-company P02
pilot (LAURUSLABS, NTPCGREEN, HDFCBANK, TCS and ETERNAL), with component-manifest
coverage enforced as a blocking acceptance rule. The existing Trendlyne adapter,
fixtures, and offline tests are preserved, but its P03/P04 network backfill is
frozen. Do not issue new Trendlyne requests or expand/redistribute those fixtures.

Phase 2 D01 discovers investor presentations, credit-rating reports,
earnings-call transcripts and recordings from saved NSE/BSE, company-IR and
official rating-agency pages, with Screener retained only as an additional
discovery path. Discovery is offline, source-faithful and content-addressed; it
does not download linked documents. D02 adds bounded document/media downloads
whose response status, declared media type, byte signature and logical artifact
type must agree before immutable content is registered. D03 extracts native PDF
pages and PPT/PPTX slide structure, preserving ordered text blocks, tables,
image/chart references and speaker-note availability. Native, OCR and selected
text are stored separately; OCR is invoked only for an unusable native surface.
D04 classifies and normalizes credit-rating reports from those persisted pages,
retaining full text, source tables, rationale/history passages and per-field page
evidence. Ambiguous or conflicting actions remain reviewable but cannot enter the
preferred canonical view.
D05 ingests usable official transcript PDFs into ordered, page-evidenced speaker
segments, retaining explicit prepared-remarks/Q&A boundaries and raw speaker
labels. An accepted official transcript disables recording transcription for the
event and cannot be displaced by an existing machine-generated version. D06
resolves only signature-validated primary-source recording downloads, preserves
their original audio/video bytes, and derives a separately hashed 16 kHz mono
PCM WAV with duration/codec provenance.
D07 turns that accepted WAV into a timestamped machine transcript only when no
usable official transcript exists, unless a human explicitly requests a
comparison. It preserves engine/model/options, detected language, confidence,
uncertainty and both original/normalized media hashes; speakers remain `unknown`
because speaking order is not identity evidence.
D08 reports terminal document quality and provenance anomalies across the bounded
pilot. D09 adds durable event-backed artifact expectations, append-only check
history and an overlap-safe due queue. Its persisted schedule checks at +2h, +6h,
+12h, +24h, daily through day 7 and weekly through week 6; explicit absence,
uncovered, access and technical-failure states remain distinct.

The planned local mutual-fund workflow is:

```text
scrapper/store/mf_import/
├── inbox/<publisher>/<YYYY-MM>/
├── accepted/
├── rejected/
└── quarantine/
```

M01 implements safe workbook inventory, M02 adds the versioned PPFAS structure
and source-cell contract, M03 adds exact holdings normalization plus auditable
security matching, M04 adds scheme classification and acceptance, M05 adds
consecutive accepted-month scheme/security changes, and M06 adds exact stock-month
feature views with workbook/delta lineage. M08 places Groww's consolidated
multi-scheme workbook and Motilal Oswal's fund-level workbook behind the same
publisher-adapter contract. M09 adds a local saved-response mfdata cross-check;
it never fetches implicitly and cannot outrank official observations:

```bash
python3 -m scrapper.cli import-mf \
  --publisher ppfas \
  --month 2026-06 \
  --path scrapper/store/mf_import/inbox/ppfas/2026-06/

python3 -m scrapper.cli import-mf-directory \
  --path scrapper/store/mf_import/inbox/

python3 -m scrapper.cli derive-mf-features \
  --publisher ppfas --through-month 2026-06

python3 -m scrapper.cli export-mf-features \
  --publisher ppfas --scope active_equity --through-month 2026-06

python3 -m scrapper.cli mf-coverage-report \
  --publisher ppfas --through-month 2026-06 --months 12

python3 -m scrapper.cli crosscheck-mfdata \
  --publisher ppfas --scheme-code PPFCF --family-id 8292 \
  --month 2026-06 --path /path/to/saved-mfdata-response.json

python3 -m scrapper.cli discover-documents \
  --symbol LAURUSLABS \
  --manifest /path/to/saved-discovery-manifest.json \
  --dry-run

python3 -m scrapper.cli fetch-documents \
  --symbol LAURUSLABS \
  --artifact-type presentation

python3 -m scrapper.cli queue-document-downloads \
  --manifest /path/to/da05-p0-scope.json \
  --dry-run

python3 -m scrapper.cli queue-document-downloads \
  --manifest /path/to/da05-p0-scope.json

python3 -m scrapper.cli run-document-download-worker \
  --scope-id da05-p0-2026-q4-q1 \
  --limit 25

# Backfill primary documents from every accepted Screener page. New accepted
# parses do this automatically; the Compose document worker repeats it safely.
python3 -m scrapper.cli queue-accepted-document-downloads
python3 -m scrapper.cli run-document-download-worker \
  --scope-id accepted-screener-concall \
  --limit 25

python3 -m scrapper.cli extract-document \
  --artifact-id 1 \
  --content-id 1

python3 -m scrapper.cli normalize-credit-rating \
  --artifact-id 1

python3 -m scrapper.cli ingest-official-transcript \
  --artifact-id 1

python3 -m scrapper.cli normalize-recording \
  --artifact-id 1

python3 -m scrapper.cli transcribe \
  --artifact-id 2 \
  --recording-artifact-id 1 \
  --model-path /path/to/local/faster-whisper-model

python3 -m scrapper.cli reconcile-artifacts --due --limit 100 --dry-run
python3 -m scrapper.cli reconcile-artifacts --due --limit 100
python3 -m scrapper.cli artifact-coverage --symbol LAURUSLABS
```

PDF rendering and OCR use `pdfplumber`/PDFium plus the local Tesseract executable.
Install Tesseract separately when OCR fallback is required; native-text PDF and
PowerPoint extraction does not invoke it. The extraction command is offline and
verifies the immutable content byte size and SHA-256 before parsing.
Recording normalization uses local `ffprobe` and `ffmpeg` executables. It is also
offline, requires a validated D02 company/exchange/agency source path, and keeps
the publisher recording as preferred content rather than replacing it with the
generated WAV.
Machine transcription uses `faster-whisper` as an optional local runtime. Install
it separately where D07 runs and supply an already-present model directory with
`--model-path`; `local_files_only` is enforced, so the command never downloads a
model. `--artifact-id` names the transcript artifact, while the matching accepted
D06 recording is resolved from company/event identity (or pinned with
`--recording-artifact-id`). Use `--comparison-requested` only for an explicit
human-requested comparison; such output cannot displace official text.

The discovery manifest is schema `1.0.0`: it names the requested company and a
non-empty list of saved UTF-8 pages, each with `source` (`nse`, `bse`,
`company_ir`, `rating_agency`, or `screener`), its original `page_url`, optional
publisher claim/content hash, and local `content_path`. Dry-run reads and hashes
those pages, prints every in-scope candidate and provenance field, and performs
no database, snapshot, or network write. Omit `--dry-run` only after inspecting
the plan; persistence is idempotent and retains every primary/Screener URL path.

DA-05 bulk downloads require a separate JSON scope; there is no "all artifacts"
default. A production P0 scope must contain 30–50 current NSE symbols, both
positive and negative examples, and at least two archetypes:

```json
{
  "scope_id": "da05-p0-2026-q4-q1",
  "scope_kind": "P0",
  "description": "Bounded Q4/Q1 document acquisition pilot",
  "artifact_types": ["transcript", "presentation", "credit_rating"],
  "artifact_ids": [101, 102, 103],
  "companies": [
    {"symbol": "EXAMPLE1", "outcome_label": "positive", "archetype": "manufacturing"},
    {"symbol": "EXAMPLE2", "outcome_label": "negative", "archetype": "financials"}
  ]
}
```

The abbreviated example is intentionally not runnable until expanded to the
30-company minimum and its exact Q4/Q1 artifacts (including selected pre-cutoff
rating evidence) are listed. P0 rejects recordings and non-PDF payloads. Queue creation
is idempotent; workers claim short SQLite leases, recover expired leases, stop
requesting a host after a block response, and skip all remaining URLs for an
artifact once validated content exists. Each worker invocation writes cumulative
scope and byte-size accounting under `exports/artifact_download/queue/`. Workers
also require the configured external document-store sentinel and free-space
reserve before claiming work.

At the M08 boundary, `import-mf` preserves safe workbook bytes by content hash,
inventories OOXML and legacy BIFF8 sheet metadata, appends duplicate observations idempotently, and
quarantines unsafe/invalid content without partial sheet or cell rows. For the
official PPFAS format it also validates publisher/month evidence, classifies all
seven scheme sheets, and persists exact cells, merged ranges, formula/cache pairs,
number formats, source totals and table boundaries. Primary and derivative
instrument rows are normalized with exact decimal strings and declared INR-lakh
scale. Structurally valid ISINs create/reuse security identities; the only name
fallback is a versioned exact normalized-name rule anchored by valid-ISIN evidence,
and ambiguity remains unmatched for manual review. Missing-ISIN TREPS and all
derivatives remain stored but outside security-resolved and stock-feature views.
Dry-run performs the same parsing, normalization, registry, and acceptance plan
without creating a database, copying a file, or appending a transition. A real
import atomically accepts all scheme/month pointers only after the complete
blocking contract passes; deterministic validation failures are rejected and
structural/unsafe inputs are quarantined. Duplicate hashes add observations but
not holdings or acceptance events, directory execution resumes by content hash,
and superseding accepted hashes preserve history while current views follow the
new pointers. The importer stores every scheme; passive, ETF, arbitrage,
fund-of-funds, and unknown schemes remain queryable, while a separate view exposes
active-equity holdings. The optional `mfdata` adapter stores immutable local JSON
snapshots in a separate schema, labels every row third-party/unverified, reports
gaps and conflicts, and exposes a preferred view where official rows always rank
first. It can never update an official holding. After acceptance, the importer compares only the immediately
preceding calendar month when both scheme pointers reference complete accepted
workbooks. It stores exact quantity and NAV-weight deltas with both import IDs and
the completeness evidence used to authorize absent rows as zero. Missing,
rejected, and quarantined adjacent months create an audited skipped comparison,
never a synthetic entry or exit. Superseded comparisons remain stored while the
current view follows accepted-version pointers. Complete current M05 pairs are
aggregated with Python `Decimal` into versioned all-scheme and active-equity
stock-month rows; passive, ETF, arbitrage, fund-of-funds, and unknown schemes
remain in the all-scheme surface but cannot leak into active-equity metrics.
Feature sets are skipped as a whole if any scheme input is incomplete. Current
views follow both accepted workbook/change pointers and effective classification
versions, while exports link every metric back to accepted workbook hashes and
the exact contributing scheme/security deltas. Groww derivative sections are
stored as derivative holdings with missing normalized ISINs, so exact-name
fallback cannot leak futures into stock features.

Company coverage and quarter coverage are separate. Accepted captures now resolve
their page period from the latest actual quarterly table column, including
bootstrap captures whose discovery period is `1970-01`. Discovery/worker skip
checks require accepted rows for the requested quarter. Audit a specific quarter
before treating collection as complete:

```bash
./scraper screener-period-coverage --period 2026-06
./scraper screener-period-coverage --period 2026-06 --repair --refresh --limit 25
./scraper exchange-unaccepted-backfill --result-period 2026-06 --limit 25
```

The audit excludes companies explicitly below the configured collection floor
unless exact symbols are requested with repeated `--symbol` options. `--repair`
adds corrected acceptance pointers and exports from existing captures without
network requests; mislabeled current pointers are retired with supersession
events, and original captures and acceptance history remain preserved.
`--refresh` makes a bounded, throttled attempt only for missing quarter rows.
Missing source quarters and refresh failures remain reported as gaps. This
command exits nonzero while gaps remain. Frozen research backups retain their
original contents; subsequent analyses should use a new audited export.

Current implemented commands from the repository root include:

```bash
python3 -m pip install -r scrapper/requirements.txt
python3 -m scrapper.cli init-db
python3 -m scrapper.cli scrape-one \
  --source screener \
  --symbol LAURUSLABS \
  --scope standalone

python3 -m scrapper.cli quality-report
```

Incremental Screener discovery uses authenticated result HTML only as a bounded
discovery source; every queued path still runs through the full company and peer
pipeline. A saved page is the default safe rollout surface:

```bash
python3 -m scrapper.cli screener-discover-results \
  --query-role current_reporting_cycle \
  --query "Last result date > 202605" \
  --fixture /path/to/saved-screen.html \
  --dry-run

python3 -m scrapper.cli screener-company-worker --due --limit 5 --dry-run
python3 -m scrapper.cli screener-discovery-coverage
```

Omit `--fixture` only for one authorized authenticated first-page request. A
multi-page result count is persisted and reported as partial, exits non-zero,
and never enqueues a purported complete universe. The implementation does not
generate `page=`, `sort=` or `limit=` requests. Docker services are deliberately
behind the `manual` profile; after mounting the session secret, use
`docker compose --profile manual run --rm discovery ...` or the corresponding
`worker` command. The `exchange-universe` and `exchange-results` services carry
no session secret and read operator files from `./exchange-inbox`. The one
scheduled service is `scheduler` (E02), which refreshes the public exchange
datasets on the IST ladder and carries no Screener session; nothing schedules a
Screener fetch.

E01 makes the official exchange record — not a Screener screen — the universe of
record, which is what discovers IPOs, delistings, symbol changes and BSE-only
companies. Dated NSE/BSE security masters build a durable registry keyed by ISIN
(structure and check digit validated) while keeping NSE symbol and BSE scrip
code separate; companies are never merged from name text. Securities are
effective-dated and marked absent rather than deleted, so additions, removals,
renames, ISIN and status changes stay auditable per snapshot. Official
result filings are then polled incrementally by broadcast timestamp, and a
company enters the queue only when its reported period advances. Screener stays
the detailed company-page source: exchange-discovered work shares the same
queue, leases and events as screen-discovered work, and still passes the full
company/peer pipeline and every validation gate before acceptance. See
`EXCHANGE_UNIVERSE_PLAN.md`.

```bash
python3 -m scrapper.cli exchange-refresh-universe \
  --dataset nse_equity_master \
  --file /path/to/EQUITY_L.csv \
  --effective-date 2026-08-05

python3 -m scrapper.cli exchange-refresh-universe \
  --dataset bse_security_master --file /path/to/ListOfScrips.csv

python3 -m scrapper.cli exchange-discover-results \
  --dataset nse_financial_results \
  --url 'https://www.nseindia.com/api/integrated-filing-results?type=Integrated%20Filing-%20Financials&period_ended=30-Jun-2026&csv=true' \
  --result-period 2026-06 --effective-date 2026-08-05 --dry-run

python3 -m scrapper.cli exchange-discover-results \
  --dataset nse_financial_results \
  --url 'https://www.nseindia.com/api/integrated-filing-results?type=Integrated%20Filing-%20Financials&period_ended=30-Jun-2026&csv=true' \
  --result-period 2026-06 --effective-date 2026-08-05

python3 -m scrapper.cli exchange-discover-results \
  --dataset bse_financial_results \
  --url 'https://api.bseindia.com/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w?SCRIP_CD=&FlagDur=5&HFQ=3&ISUBGROUP_CODE=&segment=C' \
  --result-period 2026-06 --effective-date 2026-08-05

python3 -m scrapper.cli exchange-universe-coverage
```

`--file` performs no network request; `--url` fetches only allowlisted official
NSE/BSE hosts and paths, carries no Screener session, and rejects HTML, captcha
and access-denied bodies. Filings whose identity cannot be resolved are routed
to manual review and make discovery exit non-zero rather than being guessed —
refresh the masters first, since a newly listed symbol is expected to be
unresolvable until the next master snapshot contains it.

NSE financial results for quarter-ended March 2025 and later are published in
the `Integrated Filing - Financials` export, not the legacy
`corporates-financial-results` feed. Filter `period_ended` to the exact reporting
period and request `csv=true`; revision rows use `REVISED DATE/TIME` when the
original broadcast column is blank. Add `index=sme` for the SME export. Ingesting
either export is idempotent, and revisions reuse the same company/scope/period
queue item while their source rows remain preserved.

The BSE results endpoint spans many reporting periods even when its recency and
quarterly filters are set. Always pair it with `--result-period YYYY-MM`; the
immutable response is retained, while nonmatching periods and BSE's duplicate
HTML/XML representations are counted as excluded. A BSE integrated row is
expanded into separate standalone and consolidated filing records only when the
corresponding official content field is present.

E02 makes the exchange universe safe to refresh unattended, then schedules it.
Every dataset's URL, cadence, row floor and authoritative scope now live in one
registry (`sources/exchange/datasets.py`), so `--url` is optional and `--file`
still overrides everything. Compressed sources (`.csv.gz`, `.zip`) are expanded
for parsing only — the stored blob stays byte-identical to what the exchange
served, with the codec recorded on the snapshot. Absence sweeps are scoped to the
segments a file is authoritative for, so a partial master can no longer mark
securities absent that it was never going to list. See
`EXCHANGE_AUTOMATION_PLAN.md`.

Within its declared scope a file is swept unconditionally, which is only safe
because the guard judges each scope on its own and not just the file as a whole.
A combined master that keeps its NSE rows and loses a third of its BSE-exclusive
ones moves the total by a few percent — inside every whole-file tolerance —
while a third of a swept scope disappears. So the row-count and absence-volume
bounds are applied per authoritative `(exchange, segment)` as well as overall,
and report `scope_row_count_collapse` / `scope_absence_volume`.

```bash
# See what an endpoint actually publishes before trusting a parser against it.
# Fetches or reads, decompresses, prints headers and sample rows, stores nothing.
python3 -m scrapper.cli exchange-inspect-dataset \
  --dataset nse_equity_master --sample-rows 3

# What an official refresh matched, added and disputed. Exits 1 while any
# identity conflict is open — this is the gate the scheduler runs behind.
python3 -m scrapper.cli exchange-reconciliation-report

# One attempt now, or serve the IST ladder (19:30, 21:00, 06:00 next morning,
# plus the Sunday 08:00 reconciliation job).
python3 -m scrapper.cli exchange-scheduler --once
python3 -m scrapper.cli exchange-scheduler --status
python3 -m scrapper.cli exchange-scheduler
```

The scheduler never degrades the universe: a failed fetch or a guard rejection
leaves the last accepted snapshot exactly where it was and schedules the retry,
and only after the final attempt is a dataset recorded as unrefreshed and the run
exits non-zero. Alerting is structured JSON on stderr plus the exit code.
The `scheduler` Compose service runs it with no Screener session mounted; turn it
on only once `exchange-reconciliation-report` exits 0.

Publication and content change are separate facts in `exchange_schedule_runs`. A
security master is republished every trading day and is legitimately unchanged on
a day with no listing, delisting or symbol change, so byte-identical content is
`published_unchanged` — a success, which satisfies the day and stops the ladder —
and a file whose content moved is `published_changed`. `not_published` means the
dated artifact is absent (the endpoint answers 404/410), which is the only
evidence of non-publication that does not require guessing; that keeps the later
rungs looking, and a day that never published is visible as `unrefreshed`.

Result feeds are period-addressed, so the ladder asks for the quarter that has
ended. Because companies report late and correct or restate what they already
filed, the previous quarter is re-read once a day (`role='revision'` in the
ledger, first rung only, NSE only — the BSE endpoint already returns a rolling
window). The watermark keeps the newer broadcast, so a revised filing supersedes
the one it replaces. A revision never satisfies the scheduled day and its failure
never makes the day incomplete.

The combined NSE CM-MII security file (`nse_mii_security_file`) is implemented
but deliberately has **no default URL**. Its parser is written against declared
column aliases and hard-fails on an unrecognised shape rather than guessing;
classify the live report with `exchange-inspect-dataset`, correct the alias map
in `sources/exchange/security_master.py`, then wire the URL in the registry.
Rows are classified NSE-listed or BSE-exclusive by their own columns — never by
a default exchange — and non-equity instruments (ETFs, InvITs/REITs, preference
shares, warrants, rights entitlements, debt, government securities) are retained
as *excluded* rows rather than rejected ones, since they are valid data this
pipeline deliberately does not collect.

Primary-document acquisition is part of company acceptance, not a second manual
backfill pass. Immediately after a Screener parse is accepted, that parse's
rating, presentation, official concall-transcript, and recording links are
promoted to D01 artifacts and inserted idempotently into durable queues. Screener
Notes/AI Summary links stay excluded. Rating resolution and direct document/media
downloads remain in separate always-on workers so publisher latency stays out of
the company scrape.

The Compose workers are partitioned by reviewed hosts and enforce one request
stream per host with a four-second minimum delay, durable leases and persistent
circuit breakers. `credit_rating_host_policy` records the review basis. CRISIL
and ICRA links are retained but marked `SKIPPED_POLICY` because their published
terms prohibit automated scraping/systematic extraction; no worker claims them.
CARE, India Ratings and the conservative fallback host group run independently,
which improves aggregate throughput without increasing concurrency against one
publisher. Validated PDF bytes, temporary-object downloads, and substantial HTML
reports are stored under `SCRAPER_DOCUMENT_STORE_PATH`; the external-store
sentinel and free-space floor are checked before every batch.

Watch those file downloads, durable retries, host policy blocks, circuit breakers,
and stored-byte totals in a dedicated live CLI dashboard:

```bash
./scraper download-monitor
# aliases: ./scraper monitor-downloads, or /downloads in the interactive shell
```

The old Trendlyne pilot orchestration remains in the codebase only as preserved
work. **P03 and P04 are frozen**; do not run these commands:

```bash
python3 -m scrapper.cli trendlyne-pilot-plan \
  --end-period 2026-06 --months 12 --batch-size 3 --persist

python3 -m scrapper.cli trendlyne-pilot-backfill \
  --end-period 2026-06 --months 12 --batch-size 3
```

Prior official-source research is retained as background, not as a requirement
for more feasibility reporting:

- [AMFI/AMC feasibility report](research/amfi_amc_feasibility.md)
- [official-source inventory](research/amfi_amc_source_inventory.json)
- [proposed mapping architecture](research/amfi_amc_mapping_architecture.md)

When parser or manifest policy changes after immutable responses were fetched,
reprocess the saved company and peer blobs without another HTTP request:

```bash
python3 -m scrapper.cli reprocess-screener \
  --symbol ETERNAL --scope standalone \
  --blob-id 12 --peer-blob-id 13
```

Existing scraper commands write:

- immutable HTML under `scrapper/store/snapshots/`;
- immutable validated document/media bytes and bounded diagnostic responses under
  `SCRAPER_DOCUMENT_STORE_PATH/artifact_content/` (the default remains
  `scrapper/store/snapshots/artifact_content/`);
- safe official workbook bytes under content-addressed AMC snapshot paths;
- provenance and observations to `scrapper/store/scraper.db`;
- an accepted source-faithful and normalized JSON record under
  `scrapper/store/exports/<run_id>/<source>/`.
- a cross-source decision and validation report at
  `scrapper/store/exports/quality-report.json` when `quality-report` is run.
- a structured per-run download report under
  `scrapper/store/exports/artifact_download/` when `fetch-documents` is run.
- a cumulative DA-05 queue/host/type/byte report under
  `scrapper/store/exports/artifact_download/queue/` when the durable worker runs.
- an idempotent page/slide-level extraction export under
  `scrapper/store/exports/document_extraction/` when `extract-document` is run.
- a page-evidenced rating normalization export under
  `scrapper/store/exports/credit_rating/` when `normalize-credit-rating` is run.
- an ordered official-transcript export under
  `scrapper/store/exports/official_transcript/` when
  `ingest-official-transcript` is run.
- a recording/audio relationship and validation export under
  `scrapper/store/exports/recording_audio/` when `normalize-recording` is run.
- a gated, timestamped machine-transcript export under
  `scrapper/store/exports/machine_transcript/` when `transcribe` is run.
- append-only reconciliation run exports under
  `scrapper/store/exports/artifact_reconciliation/` and company/event/artifact
  retry coverage under `scrapper/store/exports/artifact_coverage/`.

Document download recovery is explicit and auditable. Start with the logical
recovery report; it separates temporary failures, dead URLs, interaction work,
policy skips, and artifacts whose content is already present:

```bash
python3 -m scrapper.cli document-recovery-report \
  --scope-id accepted-screener-concall
```

Do not bulk-reset terminal rows. Requeue exact job IDs only after an operator or
versioned resolver change establishes a reason:

```bash
python3 -m scrapper.cli requeue-document-downloads \
  --queue-kind generic --job-id 1234 \
  --reason-code RESOLVER_UPDATED --dry-run
```

A moved/expired publisher URL becomes a new source and queue job; the failed URL
is retained and marked inaccessible:

```bash
python3 -m scrapper.cli register-document-replacement \
  --artifact-id 81 --replaces-source-id 144 \
  --url https://publisher.example/investors/archive/report.pdf \
  --reason-code SOURCE_REPUBLISHED \
  --evidence "Found on the publisher's current archive page"
```

When reviewed publisher terms require human acquisition, import the authorized
file without fabricating an HTTP attempt. Byte signatures, size bounds, content
addressing, source ownership, and the operator assertion are all persisted:

```bash
python3 -m scrapper.cli import-manual-document \
  --artifact-id 81 --source-id 144 --file /path/to/report.pdf \
  --official --reason-detail "Downloaded manually under authorized access"
```

DA-08 storage sizing uses observed files from an explicit scope and reports
mean/median/p90/p95/max bytes, validated rates, deduplicated actual storage, a
planned-count projection, and configurable headroom:

```bash
python3 -m scrapper.cli document-storage-projection \
  --scope-id p0-pilot --headroom-percent 25 \
  --planned-target transcript=5000 --planned-target presentation=5000
```

Offline tests use the standard library:

```bash
python3 -m unittest discover -s scrapper/tests -v
```

See `PLAN.md` for architecture, `EXECUTION.md` for the ordered small build chunks,
and `SCRAPPER_2.0.md` for the explicitly out-of-scope exchange-direct and same-day
metrics roadmap.
# Background rating recovery

Open `./scraper` and enter `/ratings` for live ICRA/CRISIL/other-agency progress.
The same panel appears in `/downloads`. From a terminal, use `./scraper ratings`
for a live panel, `./scraper ratings --once` for one snapshot, or `./scraper status`
for services, the hidden ICRA process, and rating progress. The panel shows saved
files, pending reports, current companies, downloads in the last 15 minutes,
publisher cooldowns, and worker activity. Stale activity is flagged explicitly.

`./scraper shutdown` finishes the current bounded rating request, closes the host
browser before its database bridge, and records the running services. It verifies
that no project worker remains running. `./scraper wake` checks the document-store
mount, restores the recorded containers, then restarts the hidden browser. Both
commands are safe to repeat; wake does not launch duplicate ICRA processes.
Keep the laptop awake with Docker running. Download progress is persisted in the
database, and interrupted claims return to the queue without discarding saved files.

For a fresh host, the ICRA browser uses a local environment (never committed):

```sh
python3 -m venv artifacts/icra-download-test/.venv
uv pip install --python artifacts/icra-download-test/.venv/bin/python playwright==1.60.0
artifacts/icra-download-test/.venv/bin/python -m playwright install chromium
```

This finite recovery requires explicit operator authorization; ordinary host
access policies stay in effect. Browser logs, environments, PID files, downloaded
documents, local sessions and databases belong outside the published code.
