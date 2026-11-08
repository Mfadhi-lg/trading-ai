# Trading AI — Phase 5 V2 Review Share Manifest

This manifest documents the contents, exclusions, canonical source assets, SHA-256 hashes, and verification procedures for the compact review archives prepared for technical review and independent inspection.

---

## 1. Archives Overview

| Archive | File Name | Size (Compressed) | Primary Contents & Purpose |
| :--- | :--- | :---: | :--- |
| **Slim Code & Results (Primary)** | `trading-ai-v2-review.zip` | **~44.5 MB** | Complete Python codebase, test suites, configs, V2 deliverables, latest Phase 5 Qwen local component deliverables, V1 regression evidence, downstream execution/test records, recovery records, earlier Phase 5 summaries, and required test fixtures. (Target under 50 MB achieved). |
| **Source PDFs Companion** | `trading-ai-v2-source-pdfs.zip` | **14.50 MB** | Canonical copies of all original corporate filing and credit rating PDFs used for Phase 5 V2/Qwen new-document testing and baseline evaluation, with verified SHA-256 hashes. |
| **Combined All-In-One** | `trading-ai-v2-review-all-in-one.zip` | **~57.0 MB** | Self-contained package combining the code/results archive and all canonical source PDFs together in one single file. |

---

## 2. Canonical Source PDF Registry

The table below lists each canonical PDF used in Phase 5 V2 testing and development. Exact SHA-256 checksums are verified against original source bytes:

| Company / Document | File Path in Workspace | Size | SHA-256 Checksum |
| :--- | :--- | :---: | :--- |
| **ICRA Venus Jewel** *(Development Reference)* | `sample_files/credit_ratings/ICRA_Venus_Jewel_2025-09-24.pdf` | 629,744 B | `ccb8bb6c12339cc6381bd84afe229662f341db26427def758acb97e03e9538aa` |
| **Everest Kanto Cylinder Q3 FY25** *(Presentation)* | `sample_files/presentations/Everest_Kanto_Cylinder_Q3_FY2025.pdf` | 2,238,912 B | `00a1a9e06699d796e54073a1c1c0f989dc58f31020497b9f53d862d5f913ede5` |
| **All Time Plastics Limited** *(Annual Report / Presentation)* | `artifacts/phase5/financial-evidence-report-v2/reports/all-time-plastics/original.pdf` | 3,994,541 B | `6117726fae64d4d33b9cda3c6f9cd414a98e3520bae3800bda2e72c859eb7a7a` |
| **Federal-Mogul Goetze** *(Credit Rating)* | `artifacts/phase5/financial-evidence-report-v2/reports/federal-mogul/original.pdf` | 775,949 B | `6109180f718fe063897b033b145b33b844c03ee64a00ccf6ae71082ec48a709c` |
| **Hinduja Global Solutions** *(Presentation)* | `artifacts/phase5/financial-evidence-report-v2/reports/hinduja-global/original.pdf` | 2,667,929 B | `610bca2db810442c8bf9acadedfb5e7eee2462274c970b7c3241992bf27246c5` |
| **Hindustan Unilever** *(Annual Report / Presentation)* | `artifacts/phase5/financial-evidence-report-v2/reports/hindustan-unilever/original.pdf` | 5,059,458 B | `61037c55bf0eb93dde4d6186eb253480241ba72aaeb475b476bc2869723adecb` |
| **Kusumgar Corporates** *(Credit Rating)* | `artifacts/phase5/financial-evidence-report-v2/reports/kusumgar/original.pdf` | 224,072 B | `61134d51c2ab3d35bd91dd3e2e4e83855b258d39372e3fcb74dea9fc12392368` |
| **Solar Industries India** *(Credit Rating)* | `artifacts/phase5/financial-evidence-report-v2/reports/solar-industries/original.pdf` | 487,904 B | `6115e6506b8813c667abaa8544ac6bd65aab0b8481409abbf732d010c3cbf8a9` |

*Note: In the full raw workspace, `presentation-610bca/original.pdf` is byte-identical to `hinduja-global/original.pdf`, and `reproducibility/` contains exact copies of `all-time-plastics` and `solar-industries`.*

---

## 3. Included Components in `trading-ai-v2-review.zip`

1. **Current Codebase & Configurations**:
   - `scrapper/`: Core document processing pipeline, document intelligence research (`temporal.py`, `disclosures.py`, `charts.py`, `native_layout.py`, `evidence.py`, `claim_candidates.py`, etc.), runtime identity, configuration, and test suites.
   - Root project configs: `Dockerfile`, `compose.yml`, `compose.development.yml`, `compose.browser-recovery.yml`, `README.md`, `TRADING_AI_*.md`, `scripts/`, `ops/`.
   - Test fixtures required for test suites (including `scrapper/tests/fixtures/documents`, `research`, `screener`, `trendlyne`).

2. **Phase 5 Financial Evidence Report V2**:
   - Location: `artifacts/phase5/financial-evidence-report-v2/`
   - Engineering Decision: `ACCEPTANCE_DECISION.md` (*FAIL — NEW-DOCUMENT EVIDENCE UNSAFE*)
   - Engineering Report: `REPORT.md`
   - Architecture & Contracts: `ARCHITECTURE.md`, `contracts.py`, `admission.py`
   - Execution & Reproduction: `RUNNER.md`, `REPRODUCE.md`, `runner.py`, `discovery.py`, `evaluate.py`, `freeze.py`, `render.py`, `select_inputs.py`
   - Unit Tests: `test_v2.py` (All 13 tests passing)
   - Evaluation CSVs:
     - `coverage-results.csv`
     - `element-coverage-results.csv`
     - `financial-errors.csv`
     - `new-document-results.csv`
     - `source-linked-review-bundle.csv`
   - Evidence JSONs & Logs:
     - `evaluation-summary.json`
     - `source-review.json`
     - `selection-screen.json`
     - `browser-verification.json`
     - `implementation-freeze.json`, `revision2-freeze.json`, `revision3-freeze.json`
     - Per-document execution logs (`all-time-plastics.log`, `solar-industries.log`, etc.)
   - Primary Review Bundle & UI:
     - `review-bundle.html` (Primary source-linked review interface)
     - `index.html`, `report-browser.png`
   - Per-Document Structured Evidence (`reports/<document_name>/`):
     - `REVIEW.md`
     - `canonical.json`, `discovery.json`, `evidence-records.jsonl`, `execution.json`, `source-inventory.json`, `withheld-and-rejected.jsonl`, `financial-evidence-report.json`

3. **Phase 5 Financial Evidence Report V1 (Regression Baseline)**:
   - Location: `artifacts/phase5/financial-evidence-report-v1/`
   - Deliverables: `REPORT.md`, `ACCEPTANCE_DECISION.md`, `ARCHITECTURE.md`, `REPRODUCE.md`, `regression-test-results.md`
   - Code & Harness: `admission.py`, `common.py`, `evaluate.py`, `generate.py`, `ingest.py`, `render.py`, `test_report.py`, `verify.py`
   - Evidence: `evidence-records.jsonl`, `financial-evidence-report.json`, `financial-evidence-report.html`, `evaluation-summary.json`, `validation-results.csv`, `source-coverage.csv`, `withheld-and-rejected.jsonl`

4. **Phase 5 Qwen Financial Table Understanding (Latest Experiment)**:
   - Location: `artifacts/phase5/qwen-financial-table-understanding/`
   - Engineering Decision: `ACCEPTANCE_DECISION.md` (*PARTIAL — PROMISING BUT UNRELIABLE*)
   - Engineering Report: `REPORT.md`
   - Architecture & Contracts: `ARCHITECTURE.md`, `component.py`
   - Execution & Reproduction: `REPRODUCE.md`, `verify.py`, `evaluate.py`, `run.py`, `finish_report.py`
   - Unit & Regression Tests: `test_component.py` (11 tests pass; 87 total regression tests verified)
   - Evaluation & Review Deliverables:
     - `table-comparison.csv`
     - `financial-errors.csv`
     - `evaluation-summary.json`, `final-summary.json`
     - `model-results.jsonl`, `runs/`
     - `review-bundle.html`, `review-protocol.md`, review images (`holdout-2-original-review.png`, `unseen-2-original-review.png`)

5. **Previous Experiment Summaries**:
   - `artifacts/phase5/financial-evidence-downstream-execution/`: Downstream executed comparison, report, acceptance decision (`REGRESSION`), `financial-comparison.csv`, `financial-errors.csv`, `model-results.csv`, raw responses, request metadata, and unit tests (`test_execution.py`).
   - `artifacts/phase5/financial-evidence-downstream-test/`: Matched downstream test design, report, acceptance decision (`BLOCKED`), prepared requests, inputs, references, and unit tests (`test_experiment.py`).
   - `artifacts/phase5/financial-evidence-recovery/`: Isolated table/grid prototype report, acceptance decision (`PARTIAL`), benchmark CSVs, coverage audit, error inventory, review queue, unit tests (`test_prototype.py`), word boxes, offsets, and Docling structured tables.
   - Earlier Phase 5 Milestones: `document-parsing-v10` (parser decisions/benchmarks), `pdf-visual-experiment` (visual findings, finalization records), `qwen-comparison-v9` (model comparison), and `architecture-validation` (executive summary & decision).

---

## 4. Excluded Items & Rationale

| Excluded Item | Location | Size Saved | Rationale |
| :--- | :--- | :---: | :--- |
| **Duplicate PDF Copies** | `reports/*/original.pdf` | **$\sim$17.5 MB** | Duplicates of the canonical PDFs stored separately in `trading-ai-v2-source-pdfs.zip` and the workspace. |
| **Redundant Reproducibility Dir** | `financial-evidence-report-v2/reproducibility/` | **$\sim$11.1 MB** | Identical duplicate of the `reports/all-time-plastics/` and `solar-industries/` report directories. |
| **Repeated Embedded HTML Reports** | `reports/*/financial-evidence-report.html` | **$\sim$18.2 MB** | 7 large (4–8 MB) standalone HTML documents containing duplicated CSS/script bundles. The primary root review interface `review-bundle.html` and primary JSON ledgers (`financial-evidence-report.json`) are retained. |
| **Python Wheel Binaries** | `docs/acquisition-readiness/baselines/**/*.whl` | **$\sim$9.2 MB** | Pre-downloaded binary wheels for historical pip environments; unnecessary for inspecting V2 source and decisions. |
| **Pre-run Hash Snapshots** | `protected-before.json` across Phase 5 | **$\sim$6.7 MB** | Large 6–7 MB temporary pre-run file inventory dumps used only for pre/post git status assertions. |
| **Rendered Page PNGs** | `financial-evidence-recovery/pages/` | **$\sim$45.2 MB** | 128 rendered page images from an earlier prototype. The original PDFs are preserved, and page images can be re-rendered on demand using Poppler's `pdftoppm`. |
| **Non-V2 Sample PDFs** | `sample_files/` & `sample_files-2/` (remainder) | **$\sim$18.0 MB** | Filings from older experiments not utilized in V2 evaluation. Canonical copies of all V2 test documents are preserved. |

---

## 5. Verification & Reproduction Notes

- **Unit Test Execution**:
  All unit tests included in the review package execute successfully offline:
  ```sh
  # Run V2 unit tests (13 tests pass in < 0.1s):
  python3 -m unittest discover -s artifacts/phase5/financial-evidence-report-v2 -p 'test_*.py'

  # Run V1 unit tests (18 tests pass in < 0.3s):
  python3 -m unittest discover -s artifacts/phase5/financial-evidence-report-v1 -p 'test_*.py'

  # Run Downstream Execution unit tests (3 tests pass):
  python3 -m unittest discover -s artifacts/phase5/financial-evidence-downstream-execution -p 'test_*.py'

  # Run Qwen Financial Table Understanding unit tests (11 tests pass in < 0.01s):
  python3 -m unittest discover -s artifacts/phase5/qwen-financial-table-understanding -p 'test_*.py'

  # Run Phase 5 Scrapper core intelligence tests (83+ tests pass):
  pytest scrapper/tests/test_phase5_*.py
  ```

- **Reproduction Dependencies**:
  - Python: 3.11+ (tested on Python 3.14)
  - Packages: `pdfplumber`, `pypdf`, `Pillow`, `pytest`
  - If re-running the full runner on external source PDFs, point `--pdf` to the canonical PDF files provided in `trading-ai-v2-source-pdfs.zip`.

