# Latest: 12 October routine integrations prepared; activation pending

[Current rollout report and exact initial authorization scope](PHASE4_ROUTINE_ROLLOUT.md) supersede the remaining-integration list below. Phase3 COMPLETE; Phase4 IN PROGRESS until actual new unattended lifecycle proof. Scheduling remains disabled.

# Phase 4 — implementation in progress; authorized production canary STOPPED

11 October 2026 baseline. **12October latest update: attempt03 PASS; bounded production lifecycle VALIDATED, scheduling DISABLED. [Actual outcome and remaining path](PHASE4_CANARY_ATTEMPT03_OUTCOME.md).** **Earlier update: the exact authorized canary exited1 before download because its fingerprint used rowid on accepted_page_versions (WITHOUT ROWID). No document/processing writes occurred. [Actual outcome and narrow verification](PHASE4_CANARY_OUTCOME.md). The preparation narrative below is retained evidence; the consumed package must not be replayed. **Correction update:**8 regression tests and the repaired read-only production preflight passed; [separate same-scope retry package and command](PHASE4_FINGERPRINT_CORRECTION.md) were separately authorized; attempt02 STOPPED at TLS certificate validation. [Actual outcome](PHASE4_CANARY_ATTEMPT02_OUTCOME.md). No further retry was initiated.** Phase 3 remains COMPLETE. Its scheduled configuration/source hashes still match the installed dispatcher manifest. No migrations, production document-byte writes, backups, release rebuilds or standing workers were executed in this phase. The one authorized capacity-metadata publication occurred during the failed canary. Existing financial/cap schedules were not edited.

## Actual lifecycle and working owners

An accepted company queue item tied to an official NSE/BSE result calls `ensure_result_artifact_expectations` in `db/screener_discovery_repo.py`. The immutable filing supplies event/period identity. Unknown epoch-sentinel periods do not open follow-up campaigns; current eligibility gates presentations/transcripts. Four document surfaces have separate policy dispositions, not an assertion that every company publishes them.

Accepted Screener parses already call `enqueue_documents_for_accepted_parse` in `workflows/screener/ingest.py`. The promoter preserves discovery-page blob/hash, document-link identity, URL, publisher claims and observed/publication timestamps. Source labels and fiscal-year conflicts constrain period assignment. Presentations/transcripts enter the existing incremental `accepted-screener-concall` scope; ratings use their existing queue; recordings are metadata only. No routine manifest was actually needed on this path.

D09 follow-up reconciliation already has fenced expectation leases, persisted availability outcomes, source refresh, eligibility filtering and bounded checks at +2h/+6h/+12h/+24h, daily through day7 and weekly through week6. It distinguishes expected publication, not offered/applicable, source not covered, inaccessible and retryable discovery failures. Exhaustion is not proof a company never publishes a document. Its old due ordering could prioritize historical dates over new arrivals. The dormant periodic executor additionally waits on company backlog; it is not the appropriate production activation route.

Canonical download claiming preserves source restrictions, per-host policy/circuit cooldowns, dedup, stale-lease recovery, fencing/renewal and shared host permits. Validation checks status, declared/detected media and content bytes. `db/documents/download.py` persists response headers, redirects, source evidence and immutable hashes; temporary files are flushed/fsynced before atomic persistence. Existing destinations/blob rows are checked rather than overwritten. Recovery adapters include NSE/BSE/issuer IR, but their automatically built targets primarily concern blocked rating artifacts; they are not a universal presentation/transcript crawler.

Migration072's trigger saves each newly inserted content in `document_processing_inbox` in the same transaction. `reconcile_processing`, `document_processing_work`, the legacy extraction fence and `run_processing` already implement versioned identity, cache recovery, PDF/PPT/PPTX/permitted rating HTML extraction, outcome/history persistence and claim release. Their consumers previously required explicit IDs or a fixed-quarter campaign. Pre072 archive contents have no inbox entries until deliberately reconciled.

## Implemented connections

- `documents/priority.py`: date-driven completed-quarter priority with P1 new current-period, P2 newly discovered late/revised/unknown-period documents, and P3 at most one separately permitted historical item. Default historical allowance is zero. The selector never assigns document periods from the current quarter. Late capacity receives a reservation within the hard batch bound.
- `operations/workers/automatic_documents.py`: finite discovery through the existing D09 refresher, bounded queue repair and download selection through the canonical downloader. New work no longer sits behind oldest historical expectations. BSE API host is excluded; no API holds or publisher policies are cleared. Imports are inert; no service/schedule starts automatically.
- `enqueue_discovered_documents`: finite repair of eligible artifacts with stored source links but no canonical URL job, using the existing incremental scope. Existing jobs across scopes are reused; terminal URLs are not rearmed. Ratings/recordings are excluded.
- `automatic_processing_contents` and `run_automatic_processing`: bounded durable-inbox selection, exact version materialization, existing worker execution and persisted outcome, with no work-ID selection or fixed quarter. Eligibility is required. Unsupported audio/video and non-rating HTML stay in evidence, with no fabricated extraction success. Historical recovery can reconcile at most one legacy missing-inbox content when expressly budgeted. Existing extraction outcomes do not become implicit parser-upgrade/re-extraction work.
- Canonical download claimant/worker now accept an exact bounded job-ID fence for canaries and automatic batches. Empty selection claims nothing. Its stale/content maintenance stays within selected IDs; the existing source-policy predicates remain binding.
- Extraction/processing accept an optional connection factory so the canary can deny writes outside document/permit tables. Default/manual behavior and claim identities remain compatible.

Manual content-ID/quarter/campaign paths remain available for finite recovery. No new schema, queue or document system was created.

## Read-only production snapshot

At **2026-10-11T15:07:56.574300+00:00** (the precise timestamp is in the linked JSON), production has 83,265 artifacts: 36,568 presentations, 26,320 transcripts, 13,902 ratings and 6,475 recordings. There are 78,202 content rows: 35,534 presentations, 22,431 transcripts, 20,107 ratings and 130 recordings. These are content rows, not unique logical filing events.

Presentation extraction: 25,427 not started, 9,989 ok, 106 partial, 12 failed. Transcript extraction: 15,987 not started, 6,438 ok, six partial. Rating content: 3,020 not started, 17,046 ok, 41 partial. All 130 recording contents remain unsupported by this extraction lane. Inbox and processing-work counts are both **zero** in this observation: saved originals mostly predate the durable-inbox migration; no mass inbox backfill was attempted.

Presentation queue: 14,513 pending, 234 retryable, 35,532 succeeded, 17,862 skipped already-have-content, 3,339 skipped policy, 1,618 terminal failures. Transcript queue: 9,289 pending, 1,694 retryable, one leased, 22,429 succeeded, 12,532 skipped content, 1,287 skipped policy, 5,353 terminal. Rating queue: 20 pending, three retryable, one leased, 971 succeeded, 11,529 skipped content, 2,739 skipped policy, 121 terminal. Recording queue: 130 succeeded, 6,196 skipped policy, 24 terminal.

These queue rows are **not unique missing documents**: legacy scopes contain duplicate jobs and rows whose artifact already has content. No claim was made from either observed lease, and no hold was cleared. The 41,414 not-started presentation/transcript contents are the processing backlog; this does not gate new-content processing.

Complete type/state/media counts: [CSV](../../artifacts/phase4/production-counts.csv). [Read-only snapshot and exact timestamp](../../artifacts/phase4/inspection/20261011T150755/production-inspection.json). [Ownership/read-only outcome](../../artifacts/phase4/inspection/20261011T150755/production-inspection-outcome.json).

## Focused validation

**97 tests passed**, covering new current/late/historical selection and rollover, canonical discovery leases, exact canary claim isolation, versioned cache/fences, extraction/unsupported media, source-policy queues, filing expectations, ratings separation and storage guards. [Log](../../artifacts/phase4/focused-tests.log). A subsequent seven-test eligibility-selector rerun passed after tightening processing admission to confirmed eligible scope.

[Isolated lifecycle receipt](../../artifacts/phase4/offline-lifecycle.json): a synthetic issuer discovery-page fixture, four filing expectations, canonical queue creation and claim, validated original/hash, durable inbox, automatic work materialization, extraction **ok / 21 pages**, and a second tick **idle**. Original PDF was obtained once from the retained eligible issuer URL into temporary storage; no production archive/database writes. The fixture HTTP response and page are synthetic, explicitly identified; they are not production discovery proof. Failure-cache/crash-boundary/lease-expiry/stale-fence tests remain actual focused offline evidence.

## Exact production canary and execution command

**ATTEMPTED after explicit authorization; STOPPED before download. The following is the retained prepared scope, not permission to replay. See the linked actual outcome.**

One Sacheerome Limited issuer transcript: company4320, artifact81417, existing download job144466, retained scope`document-acquisition-full-v1`.

Source: `https://www.sacheerome.com/pdf/H2_FY_26_Earning_Call_Transcript.pdf`.

PDF cover: H2 FY26, publication/call date1June2026. The old link's `Jun2026` label is publication metadata, not evidence of a June financial quarter. Existing NULL`period_end` remains unchanged; no invented quarter correction or artifact identity is created. Retained Screener-source attribution remains intact; its current `is_primary_source=0` is not silently promoted to an official-source flag.

Expected bytes **634,502**, SHA256`f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3`, **21 pages**. Exactly one GET, zero redirects, at most10MiB response, one claim/download and one automatic artifact-scoped processing item; deadline300s. Existing retry/backoff policy is retained; this invocation never automatically replays. Zero new artifacts/scopes/queue jobs; at most one content/inbox/work/extraction row and21 pages plus existing provenance/validation/claim/permit records. One fresh capacity-metadata publication and canonical temporary/report files are included. Zero financial/market-cap/rating/recording writes or historical sweeps. Failed validation/extraction stops with receipts; HTTP200 is insufficient.

Parser contract: `document-structure`1.1.0, Tesseract5.5.0, English, `--psm6`, native usability rule`alphanumeric_3_replacement_ratio_0_05_v1`. It is recorded explicitly in the prepared plan and checked against the existing Docker image before ownership.

Prepared package: [plan](../../artifacts/phase4/canary/plan.json), [compose/mounts](../../artifacts/phase4/canary/compose.json), [source seal](../../artifacts/phase4/canary/manifest.json).

After authorization, activate this **one-shot canary only**:

```sh
python3 ops/activation/phase4_documents.py --execute
```

The adapter shares `.nse-owner.lock` with the installed scheduler, verifies production file/schema/control identities using retained evidence, takes existing DB owner locks, refuses active leases or changed selection/source/version, checks external identity/mounts/capacity/hash, and denies non-document/permit writes. It rechecks accepted financial observations and BSE access-event fingerprints after processing. It records runtime/queue/extraction receipts under`artifacts/phase4/canary/` and refuses package reuse after an attempt. It does not activate recurring workers.

Host preflight passed: external USB/APFS UUID`60E1204B-CB98-48C4-9398-1D2FBAC9B44A`, matching retained physical identity; total1,000,168,488,960bytes; free375,167,844,352bytes; reserve150,025,273,344bytes (max100GiB/15%). Four sampled existing archive hashes/sizes matched. Actual external sentinel remains c05.3; prepared runtime identity is supplied as a read-only Docker config, while a separate read-only physical mount verifies the underlying sentinel hash. Existing drive sentinel is not rewritten. Fresh measurements are repeated immediately before an authorized write. [Storage evidence](../../artifacts/phase4/canary/storage-preflight.json).

## Remaining before unattended completion

1. Authorize and run this exact canary, inspect its actual durable queue/content/provenance/processing/page outcomes and any failure before further activation.
2. Wire the bounded adapters into the existing host dispatcher with per-slot receipts, shared ownership, source session, physical capacity publication and finite runtime budgets. Proposed hourly discoveryHH:05/downloadHH:25/processingHH:35, max2 per stage, no replay. Historical allowance remains zero initially. A separately approved later allowance could reserve one item at02/08/14/20 hours, max4/day; it must never expand unused live capacity into additional historical work. Keep Phase3 financialHH:15/:45 and capHH:00 independent. No installed schedule was modified in this turn.
3. The current automatic discovery path refreshes accepted Screener metadata and acquires the linked official exchange/issuer documents. Arbitrary issuer IR landing-page discovery and permitted official document-announcement intake still need a finite connection to the existing saved-page/discovery adapters. Existing issuer recovery primarily addresses ratings. This is an integration gap, not a publisher-access permission to use unrestricted HTTP/browser acquisition.
4. Add a bounded long-tail/previous-quarter final check after the existing six-week follow-up ends; the current adapter preserves that cutoff and honestly records exhaustion. New older-quarter filings are prioritized, but this is not a guaranteed final check for an exhausted prior expectation.
5. Replacement **URLs** keep distinct source/artifact identities. Replacements at an unchanged URL with changed publication metadata need an evidence-driven version handoff; current content dedup deliberately skips an artifact with saved content. Do not erase a successful job or overwrite the original to simulate revision detection. The one-document canary does not prove this case.
6. Observe a new eligible live filing/document through the installed scheduled pipeline, including an actual delayed follow-up and new-content arrival. This historical retained canary proves bounded real storage/processing if successful; it cannot alone prove complete unattended current-quarter discovery or all source/format variants.

No external-drive or PDF-parser blocker was found in the measured checks. Live canary authorization and the discovery/scheduling/revision integrations above remain real completion gates. Phase4 is **in progress**, not complete. PPT chart interpretation, concall semantics, rating intelligence and ML remain in later phases.
