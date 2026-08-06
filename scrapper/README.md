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

Current implemented commands from the repository root include:

```bash
python3 -m pip install -r scrapper/requirements.txt
python3 -m scrapper.cli init-db
python3 -m scrapper.cli scrape-one \
  --source screener \
  --symbol LAURUSLABS \
  --scope consolidated

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
no session secret and read operator files from `./exchange-inbox`. No scheduler
is enabled by the image or Compose file.

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
  --symbol ETERNAL --scope consolidated \
  --blob-id 12 --peer-blob-id 13
```

Existing scraper commands write:

- immutable HTML under `scrapper/store/snapshots/`;
- immutable validated document/media bytes and bounded diagnostic responses under
  `scrapper/store/snapshots/artifact_content/`;
- safe official workbook bytes under content-addressed AMC snapshot paths;
- provenance and observations to `scrapper/store/scraper.db`;
- an accepted source-faithful and normalized JSON record under
  `scrapper/store/exports/<run_id>/<source>/`.
- a cross-source decision and validation report at
  `scrapper/store/exports/quality-report.json` when `quality-report` is run.
- a structured per-run download report under
  `scrapper/store/exports/artifact_download/` when `fetch-documents` is run.
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

Offline tests use the standard library:

```bash
python3 -m unittest discover -s scrapper/tests -v
```

See `PLAN.md` for architecture, `EXECUTION.md` for the ordered small build chunks,
and `SCRAPPER_2.0.md` for the explicitly out-of-scope exchange-direct and same-day
metrics roadmap.
