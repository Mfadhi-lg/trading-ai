# SS-01 v1: historical classification research import

## Latest Q2 owner correction (4 November 2026)

The owner replaced `NSE:AETHER` with `NSE:ATHERENERG` (Ather Energy Limited, company 717). The current `ss_q2_focus` view still has 117 unique stocks. Current exports and validation: `artifacts/subsector-ss01/q2-atherenerg-correction-20261104/`. The original Downloads TXT and November 3 snapshots remain unchanged.

## Current Q2 table: TradingView reconciliation (3 November 2026)

`ss_q2_focus` now matches the owner-authoritative TradingView file exactly: 117 unique stocks, corrected legal names and identity links. All 120 original assessment rows remain intact. See `artifacts/subsector-ss01/q2-tradingview-20261103/README.md` and its corrected current CSV and validation. Earlier exports below are historical snapshots.

## Q2 rows 101–120 addendum (3 November 2026)

The existing research database now includes 120 Q2 FY2027 source rows (118 focus entries). The latest append-only image source, combined exports, recovery snapshot and validation are documented in `artifacts/subsector-ss01/q2-2027-addendum-20261103/README.md`. The earlier v2 reports below describe the original 100-row source; use the new addendum validation for the current multi-source state.

## Q2 addendum (SS-01 v2, same research database)

The same destination now supports Q4/Q1 plus the Q2 FY2026–27 CSV. Use:

```sh
python3 -m scrapper.research.subsector_ss01.importer q2-dry-run
python3 -m scrapper.research.subsector_ss01.importer q2-apply
python3 -m scrapper.research.subsector_ss01.reconcile
python3 -m unittest scrapper.tests.test_subsector_ss01 scrapper.tests.test_subsector_ss01_q2 -v
# Explicit reversal, when requested (schema 002 remains; Q4/Q1 remain intact):
python3 -m scrapper.research.subsector_ss01.importer q2-rollback
```

The current addendum audit/report lives in `artifacts/subsector-ss01/v2`.
Migration `002_q2_assessments.sql` is additive; migration 001 is unchanged.
The original v1 ZIP and its manifest describe the frozen pre-Q2 state; the database
at the existing target now contains all three quarters. The v2 manifest covers the
current database. `v2/pre-q2.sqlite3` retains the pre-Q2 research snapshot.

The Q2 source has 100 original rows and 98 distinct stock labels. As explicitly
chosen by the owner, the operational `ss_q2_focus` view has one entry per verified
company or unresolved exact stock label. The two conflicting duplicate groups
(Bliss GVS and KRN) have **no selected assessment** until reviewed. Their raw
fields and original row evidence remain in `ss_mentor_assessments` and
`ss_q2_assessment_evidence`. No source CSV was edited.

All four raw assessment fields remain separate: `q2_results_raw`, `rating_raw`,
`guidance_raw`, `extraction_note_raw`. Empty CSV cells remain empty strings.
No assessment category mappings or negative labels were introduced. The raw `Sl No`
and `Stock` cells, ordinal record number and physical CSV line range are retained.
The source hash and `ss_sources.source_id` identify the exact snapshot. Import
recording time appears only in `ss_import_batches.recorded_at_utc`; decision and
historical availability timestamps remain NULL without independent evidence.

Q2 identity resolution accepts exact symbols and verified source symbols after
case/punctuation/space normalization, exact legal names after removing only a
trailing Ltd/Limited suffix, and exact previously resolved full source names.
Candidate IDs are unioned; competing exact identities cannot be silently chosen.
Unique short-name prefixes and fuzzy suggestions are **review-only**, never verified
matches. Multiple plausible prefix candidates are explicitly ambiguous.

Q4/Q1 assignments are referenced by ID in `ss_inherited_subsector_evidence`.
`ss_q2_subsector_status` accepts a reused label only when every prior assignment
agrees on the exact label; conflicting labels, missing prior labels and unresolved
identities remain distinct. No new Q2 label is invented or written into historical
`ss_assignments`. A reused quarterly label is not proof it was available at the
Q2 mentor decision time. All unknown dates remain excluded from dated ML queries.

Empty `ss_documents`, `ss_extracted_facts`, `ss_fact_citations` and
`ss_assessment_fact_links` support future results, metrics, agency reports,
presentations, transcripts, guidance and cited facts. Mentor `Rating` text is never
an extracted agency rating. `q2_queries.sql` demonstrates conservative availability
filters across judgment, documents, facts and citations; no extraction or ML ran.

Source-specific rollback removes Q2 import data only, within a transaction. It
retains the additive schema and earlier history, and refuses to delete assessments
if future factual links reference them. The original audit supports rollback even
if the original CSV is subsequently unavailable. The apply path is idempotent;
changed reviewed plans for an already imported source require explicit reversal.

## Original Q4/Q1 workflow

Run from the repository root with Python 3 (stdlib only):

```sh
python3 -m unittest scrapper.tests.test_subsector_ss01 -v
python3 -m scrapper.research.subsector_ss01.importer dry-run
python3 -m scrapper.research.subsector_ss01.importer apply
python3 -m scrapper.research.subsector_ss01.reconcile
```

The authorized destination is **only** `artifacts/subsector-ss01/v1/research.sqlite3`.
The CLI has no arbitrary write-target option. Existing targets must carry the SS-01
research purpose marker and matching canonical master fingerprint. Source inputs,
identity master, importer code and migration must match the saved dry-run audit.
The research migration is deliberately outside the runtime migration directory.
No production imports, runtime initialization, acquisition services or network calls occur.

Primary classifications come exclusively from the two owner-supplied paths:

- `/Users/mohamedfadhi/Downloads/2026 q4.csv`
- `artifacts/b2-2026-10-01/Q1 2027 FOCUS LIST.txt`

`OLD` files and governance outputs are context, never replacement classifications.
Q4's `Category` and Q1's `###` headings are retained as `source_category` definitions
in `ss_subsectors`. This is a source vocabulary, not an invented detailed taxonomy.
Exact raw labels and normalized whitespace/case labels coexist; normalization never
merges definitions. The source formats do not separately define sectors, industries
or finer subsectors. No sector tables exist in the inspected canonical master;
the nullable reserved `sector_id` remains NULL.

Canonical `companies`, `security_identifiers` and `source_identifiers` schemas and
rows are copied unchanged from the archived AR-01.3C database (4,428 companies).
The two-company local store is not used. A compact logical identity snapshot and
its SHA-256 are included; the 14 GB archive is read-only and is not copied.
The logical fingerprint covers the copied table schemas/rows and BSE alias evidence,
not the entire acquisition database file. Its file size/mtime are also checked.

Resolution unions exact exchange identifiers, verified Screener symbols and BSE
mnemonic version evidence. A source's exchange prefix is preserved even when
Screener evidence does not assert exchange membership. All competing company IDs
cause quarantine. The explicit underscore-to-hyphen symbol alias is audited.
No fuzzy company-name matching occurs. Normalizing `Limited`/`Ltd` produces review
suggestions only. Historical symbol effectiveness at quarter end is not established
by the later identity snapshot, and is explicitly marked unknown.

`ss_sources` stores paths, content hashes and original bytes. `ss_rows` stores original
CSV line ranges or one-based TXT comma-token references, exact row text, raw labels,
symbols, names and resolution evidence. Unresolved rows remain queryable.
`ss_memberships` deduplicates company/quarter membership; its evidence table preserves
every appearance. `ss_assignments` deduplicates source-version/company/label facts;
`ss_assignment_evidence` retains repeated source rows. New source hashes append
versions rather than overwriting history. Conflicting source labels coexist.

Quarter ends describe the results period, **not** classification availability.
`known_at`, `valid_from` and `valid_to` remain NULL without dated evidence.
As-of-date queries therefore return no dated assignments for these inputs.
Quarter-filtered evidence is available separately. Do not backfill these dates
from filenames, filesystem timestamps, later snapshots or current classifications.

Q1's Focus List is stored as `focus_reference` evidence, with unknown availability.
The Q4 CSV does not explicitly assert a mentor decision, so its decision remains
`unknown`. Supporting screenshots titled Q4 FOCUS LIST are inventoried, but their
title is not silently propagated as a row-specific, dated decision. No rejection
is inferred from absence, legacy rejected-candidate filenames or unmatched symbols.

See `queries.sql` for exact-label membership, quarterly labels, both-quarter
companies, historical as-of queries and complete evidence. Open the delivered
database read-only, e.g. `sqlite3 'file:artifacts/subsector-ss01/v1/research.sqlite3?mode=ro'`.

The compact review archive contains the database, frozen primary inputs, identity
snapshot, complete dry-run audit, reconciliation, tests, migration and queries.
Re-execution of the CLI uses the original repository paths and archived master;
the ZIP itself supports read-only database review and running the synthetic tests.
No market-data acquisition or index construction is part of SS-01.
