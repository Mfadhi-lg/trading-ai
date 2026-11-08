# Reporting-quarter metadata recovery

The monitor filters documents by `document_artifacts.period_end`. Saved Screener
call labels survived discovery, but the promoter originally assigned periods only
to the newest call group matching the accepted financial result. Historical
presentations, transcripts and recordings therefore appeared unassigned even
when their links or downloaded contents identified a reporting quarter.

Promotion version 1.2.0 recognizes explicit fiscal-quarter statements in source
links and titles. Extraction additionally checks the first three saved pages.
A verified period can be shared with companion documents only within the exact
same saved parse, reporting scope, company and original earnings-call group.
Existing assignments are preserved. Call months disambiguate explicit quarter
statements; publication or download dates do not supply reporting quarters.

Missing periods are filled with an append-only `artifact_period_corrections`
record containing the source fragment, parse/page references and original NULL
value. Because the existing audit schema requires a non-NULL old-period string,
its `old_period_end` is empty for these records and `evidence_json.old_period_end`
explicitly records JSON null. No schema or financial observations are changed.

Credit-rating reports have report dates rather than a required earnings quarter.
Their separate monitor count no longer inflates the warning about unassigned
presentations and calls. Non-quarterly analyst-day material can also remain
unassigned. The `unknown` filter remains available for review.

Automatic processing now also runs a recurring metadata recovery pass, including
when its extraction queue is idle. Each pass checks at most 100 unresolved
non-rating artifacts for up to five seconds, using saved links, extracted pages,
exact call companions and hash-verified native text from the first three pages
of local PDFs. PDF reads run in a separate process with a two-second timeout;
they do not download, run OCR or create extraction work. Existing assignments
remain unchanged and recovered periods retain the append-only correction audit.

The cursor lives beside the database in `document-period-recovery.json` and is
updated under the existing automatic processing owner after corrections commit.
A fixed upper artifact ID lets a sweep finish despite new arrivals. Completed
sweeps restart to revisit later evidence; interruption safely replays a batch.
Processing receipts include `period_recovery` checked/recovered counts. The
remaining unassigned count can still grow with company coverage or documents
whose available evidence does not establish a quarter.

Deployed on 7 November in `quarter-recovery-20261107-v2`. The live metadata-only
check examined 600 documents and recovered 85 quarters, with 85 corresponding
audit inserts and unchanged artifact totals. Continuous processing and its
download companion are enabled, with no document-stage stop markers. Validation:
51 tests and 34 subtests passed. Deployment and live-check receipts are retained
in `artifacts/automatic-quarter-recovery-20261107/`.

The evidence and transaction receipts are retained in
`artifacts/reporting-peak-repair-20261105/`. Three transactions corrected 35,603 documents across the archive: 16,674
from explicit source and saved-page evidence, 8,375 from additional header
formats and exact saved-call groups, and 10,554 from verified local PDF headers
and their companion documents. The final selected-company count was 4,392
unassigned call/presentation documents, with 4,292 rating reports tracked
separately. Counts can change as new companies and documents enter the system.
Local PDF-header evidence uses registered content hashes; the scans made no
source requests and never altered stored documents.

Final verification: 74 focused tests and 42 subtests passed. Configuration
hashes matched, admissions resumed, no stage stop markers remained, and a
financial batch accepted all ten companies after the session-token update.
The BSE/NSE capacity changes await their next eligible exchange slots.

Exchange intake now permits 2,000 new BSE queue entries per run. NSE retains
6,000 rows per quarter and allows 12,000 across its two-quarter fetch; its
100-entry bounded queue admission is unchanged. Original stopped-job receipts
are preserved when diagnosed failure markers are archived.
