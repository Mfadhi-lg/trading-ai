# Phase 5: offline document intelligence vertical slice

The supported stopping point is now the [offline source-review workflow](../../artifacts/phase5/source-review/v9/REPORT.md).
Use its [source-linked demonstration](../../artifacts/phase5/source-review/v9/demonstration.html)
or `python -m scrapper.research.document_intelligence.source_review` with a retained
manifest and a new output directory. It searches canonical native/OCR passages,
original cells and supported chart metadata independently of claim construction.
All search hits remain review-only and categories are unestablished; topic matches
are suggestions. The original v8 first pass is preserved. Further broad automated
claim extraction is stopped; existing verified features and historical ML gates
remain intact. See the report for frozen evaluation, noise and limitations.

Implemented on 2026-10-13. This is an executable derived research-evidence overlay,
not a document summary or a second document database. Production schedules,
workers, extraction orchestration, runtime identities and database contents were
not changed. Existing workspace changes were preserved.

The latest [generalization and model comparison](../../artifacts/phase5/generalization/v3/REPORT.md)
fixes Himadri as an 8/8 known regression but establishes **no unseen improvement**:
v2 and the corrected implementation each recover 1/29 fresh holdout references.
The report includes independent source review, 0/20 original-public-release
certification and bounded local Granite, Paddle, Qwen 9B, MinerU, GLM-OCR,
OvisOCR2, Qwen3-VL 8B and TeleOCR comparisons. TeleOCR uses a separately
authorized isolated Rust/Candle Metal runtime; its custom GGUF remains
incompatible with the installed LM Studio GGUF backend. All model outputs
remain offline proposals, with no production integration.
Production, historical backtesting and ML admission remain blocked.

## Reproduce

Run from the repository root with its existing Python environment. No dependency
installation, source requests, credentials or database access are needed:

```sh
scrapper/.venv/bin/python -m scrapper.research.document_intelligence.offline \
  --manifest artifacts/phase5/offline-demo/inputs/manifest.json \
  --output "$(mktemp -d /private/tmp/trading-ai-phase5.XXXXXX)/results" \
  --decision-at 2024-01-24T00:00:00Z
```

The command prints the output directory. It re-extracts the pinned originals,
validates evidence, and writes canonical JSON, evidence JSONL, guidance comparison
outcomes, a point-in-time projection and `run.json`. `--reuse-canonical` instead
uses the pinned retained D03 exports after verifying their byte hashes, artifact
IDs, content IDs and original hashes. `--ocr` enables installed local Tesseract;
OCR numeric values require manual validation. Defaults use native extraction.
Output directories must be new and separate from inputs and production stores.
The CLI is bounded to 12 documents, each at most 50 MiB. Python socket connections,
DNS, binds and datagrams are denied during extraction. No model or provider calls
are present; production orchestration is never invoked.

## Infrastructure audit and reuse

- `scrapper/documents/extraction.py`: existing canonical pages/slides, native/OCR
  selection, ordered elements, tables, image/chart references and bounding boxes.
- `scrapper/documents/pdf.py`, `powerpoint.py`, `html.py`: existing byte parsers;
  PDF/PPT/PPTX and stored rating HTML are supported by the offline adapter. Legacy
  PPT remains limited to the existing bounded text-recovery capability.
- `scrapper/documents/ocr.py`: existing local Tesseract fallback, opt-in here.
  Image-only fixtures also exercise the explicit unsupported path with OCR off.
- `scrapper/documents/transcript.py`: existing explicit speaker segmentation,
  prepared/Q&A boundaries, fragment provenance and validations. The overlay adds
  source-cited roster roles and opening-remarks declarations; it does not change
  production transcript preference or its segmentation behavior.
- `scrapper/documents/credit_rating.py`: existing agency recognition, facility
  tables, grade/outlook/action normalization, passages and conflict validations.
  The overlay quarantines combined grades and mixed actions and distinguishes
  an affirmation, upgrade, downgrade, assignment and explicit outlook revision.
- Existing `document_artifacts`, `artifact_contents`, `response_blobs`,
  `document_extractions`, `document_pages`, `document_page_elements` and source
  provenance remain the canonical architecture. Manifest references reuse actual
  artifact/content/company IDs and original SHA256 values. No migration was added
  or run. No production database was opened, even for a read.
- Existing research interfaces in `scrapper/research/features` expose financial
  features and recorded document availability. Their capture-aware conventions
  and timestamp parser are reused; this overlay does not alter those interfaces.
- `scrapper/documents/revisions.py` and immutable content identities already
  preserve changed bytes. This slice adds explicit derived `supersedes_version`
  relationships and cutoff-aware projection without replacing old evidence.
- No implemented local RAG, embedding, reranking or LLM research pipeline was
  found in the active `scrapper`/`experiments` code. No such service is required or
  added. Roadmap descriptions are not treated as working infrastructure.

## Added code

All implementation lives in `scrapper/research/document_intelligence/`:

- `schema.py`: shared context and evidence records, explicit statuses, immutable
  content/version references, parser/model identities and missing metadata states.
- `numbers.py`: Decimal-based currency/scale, percentages, ratios, ranges and
  approximate values. Missing units remain unresolved.
- `extract.py`: relevant metric/section detection, conservative numeric binding,
  speaker/statement taxonomy, native tables and reused rating normalization.
- `charts.py`: explicit embedded PPTX series data only, with title-declared units
  and category-declared fiscal periods. No image or PDF chart digitization.
- `evidence.py`: exact page/element/text-span, table-cell and chart-cell checks,
  normalization revalidation, duplicate preservation and contradiction quarantine.
- `temporal.py`: capture-safe cutoff eligibility, version supersession chains,
  and two-sided guidance comparisons with known public chronology.
- `offline.py`: bounded, new-only local runner, original hash rechecks and runtime
  measurements. Exports are files referencing existing identities, not a DB.
- `scrapper/tests/test_phase5_document_intelligence.py`: 38 focused tests, including
  real archived disclosures, existing difficult fixtures, and labeled synthetic
  contracts for rating actions, guidance revisions, tables and embedded charts.

## Corpus, outputs and evidence traces

`artifacts/phase5/offline-demo/inputs/manifest.json` identifies three previously
acquired local originals. Isolated byte-identical copies and their retained D03
exports are pinned there. Originals on the external document volume were read
and hash-checked without alteration. The rating sample is a permitted CARE
archive document. No CRISIL/ICRA source was contacted.

The authoritative demonstration outputs are in
`artifacts/phase5/offline-demo/results/`. Start with `sample-evidence.json`, which
contains complete records for all three types. Each record contains the original
identity/hash, the canonical extraction hash, exact citations, source metadata,
normalization, validation status and parser/model version. Full outputs contain
606 records; these are extraction/coverage outcomes, not 606 independent facts.

- **Sona Comstar presentation**, artifact 23925 / content 24957, physical PDF page
  14 (printed slide 13): “Our net order book1 grows to ₹240 billion”. Normalized
  value is INR 240,000,000,000. The 221/1/20/240 chart bridge is not independently
  digitized or assigned to periods. The title's explicit figure is native text.
  Its precise fact period remains unknown; the filing's period is separately
  preserved at document level. Source page 14 was visually checked.
- **Dynamic Cables transcript**, artifact 17496 / content 10344, physical page 4
  (printed page 3): CFO Murari Lal Poddar states the order book stands at INR808
  crores as of 31 March 2026. Normalized value is INR 8,080,000,000. Roster role
  evidence is on physical page 2. Page 4 was visually checked.
- **Dynamic Cables guidance**, physical page 5: anonymous “Management” gives
  long-term growth guidance of 18% to 20%. It is stored as a projected range and
  a long-term horizon. The specific growth basis and individual speaker are not
  inferred. It is not converted into FY27 revenue guidance. No earlier matching
  document was supplied, so comparison status is NOT_FOUND, change UNKNOWN.
- **Vimta CARE report**, artifact 561 / content 407, physical page 1: the long-term
  bank facility has a reported amount of INR36.22 crore, rating “CARE A; Stable”,
  action “Assigned”. It is not an upgrade. The exact table cells and headers are
  retained. The report date is 2023-07-11, but an explicit effective action date
  cannot be established by the reused parser: the event remains AMBIGUOUS. The
  combined long/short-term rating is also quarantined instead of manufacturing
  an outlook. “Liquidity: Strong” is a cited qualitative statement on page 2.
  Report page 1 was visually checked.

Public timestamps, ingestion timestamps and original download URLs are absent
from these retained exports and remain UNKNOWN. Filing dates are not relabeled
as publication timestamps. The known D03 completion timestamp establishes only
a conservative upper bound on when these bytes had been captured locally.
Unknown historical availability is never backdated. All records are excluded
from the supplied 2024-01-24 prediction cutoff, even though one filing is dated
2024-01-23. A rating record is always an as-of observation; no “current rating”
feature or freshness claim is produced.

## Validation and runtime

`artifacts/phase5/validation.xml` records **60 passing tests in 3.68 seconds**:
38 Phase 5 tests plus 22 reused PDF/PPTX, rating and transcript regression tests.
Tests write only disposable databases/stores under temporary roots; the demo
test additionally rejects any SQLite connection. Coverage includes numeric units,
actual/guidance/targets/questions, order books, speaker identity and role evidence,
rating actions and outlook revisions, changed/withdrawn/unchanged guidance,
missing values, ambiguity, citation and normalized-value tampering, revisions,
future publication and capture cutoffs, duplicate/contradictory signals, native
tables, embedded charts and scanned documents. Synthetic cases are not reported
as corporate research facts.

`results/run.json` records the fresh original-to-evidence run: 62 pages,
7,861,125 source bytes; 1.787529 seconds wall time, 1.778738 seconds CPU time,
237,633,536 bytes peak process RSS (about 226.6 MiB). Hardware: ARM64,
10 logical CPUs, macOS 26.6.2, Python 3.14.5; no GPU or model used. These are
small-sample process measurements, not bulk throughput or model benchmarks.

The final statuses are 9 VERIFIED numeric disclosures, 293 EVIDENCED_STATEMENT,
55 AMBIGUOUS, 218 UNSUPPORTED and 31 NOT_FOUND. VERIFIED certifies extraction of
an explicit disclosure and its normalization, not independent verification of
management's claim. EVIDENCED_STATEMENT is never promoted to a verified numeric
fact. Margin increases retain `measurement_kind=change`, distinct from levels.
Most unsupported records are image references, including decorative images;
they are not counted as missing financial facts.

`protected-source-verification.json` confirms that 99 repository schedule,
activation, worker, production extraction and compose files match their recorded
baseline. This is a source-file check, not an inspection of running services.
No production service control, database connection or live source request ran.

## Before scaling

- Recover authoritative publication/ingestion/source-URL metadata and historical
  identity provenance from an approved isolated archive/DB copy. Capture-safe
  backfill is not a filing-time historical ML certification.
- Add document-family golden examples and precision/recall evaluation against
  human-labeled disclosures. This slice favors precision and exposes missing
  coverage; it is not comprehensive extraction for every issuer layout.
- Improve multi-column PDF number/label alignment, multi-row tables, explicit
  rating-date formats, combined facilities, negation and cross-page statements.
  Current ambiguous cases require review rather than guessed values.
- Supply chronological prior guidance documents. Comparisons require both
  citations, matching identity/metric/horizon/unit/scope and public timestamps.
  Absence, refusal to issue guidance and a new unchanged rating publication do
  not constitute guidance withdrawal or rating change.
- If interpretation models become useful, evaluate an explicitly configured
  local model and its hardware needs separately. Keep model proposals unverified
  until deterministic evidence/normalization checks or human review pass.
- Integrate approved derived evidence persistence into the existing architecture
  only after offline quality review. Scheduler/worker activation and any DB
  migrations remain outside this implementation.

To rerun all validation with isolated runtime paths:

```sh
env TRADING_AI_ENV=test \
  SCRAPER_STORE_PATH=/private/tmp/trading-ai-phase5-test-store \
  SCRAPER_DOCUMENT_STORE_PATH=/private/tmp/trading-ai-phase5-test-store/snapshots \
  SCRAPER_DB_PATH=/private/tmp/trading-ai-phase5-test-store/scraper.db \
  SCRAPER_EXPORTS_PATH=/private/tmp/trading-ai-phase5-test-store/exports \
  scrapper/.venv/bin/python -m pytest \
  scrapper/tests/test_phase5_document_intelligence.py \
  scrapper/tests/test_d03_document_extraction.py \
  scrapper/tests/test_d04_credit_rating_normalization.py \
  scrapper/tests/test_d05_official_transcript.py -q \
  -o cache_dir=/private/tmp/trading-ai-phase5-pytest-cache
```
