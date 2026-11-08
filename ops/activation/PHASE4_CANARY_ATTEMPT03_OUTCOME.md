# Phase 4 attempt03 — PASS: bounded production document lifecycle VALIDATED

Executed exactly once on **12October2026,10:17:11–10:20:16IST**, exit0, **185.25seconds including host setup**, within300seconds. Sacheerome company4320/artifact81417/job144466; original scope unchanged. No automatic scheduling, additional historical work, production retry, migration, release review or worker activation occurred. Phase3 COMPLETE; Phase4 IN PROGRESS pending unattended proof.

[Machine-readable verification](../../artifacts/phase4/canary-attempt-03/verification.json), [runtime receipt](../../artifacts/phase4/canary-attempt-03/runtime.log), [launcher/owner outcome](../../artifacts/phase4/canary-attempt-03/outcome.json), [durable read-only verification](../../artifacts/phase4/canary-attempt-03/verification/production-inspection.json).

## Actual acquisition and storage

New canonical download attempt **84703**: HTTP **200**, declared/detected **application/pdf**, **634,502bytes**, elapsed4,363ms, **zero redirects**, no error. Final URL equals the exact authorized issuer URL. Response evidence includes Content-Length634502, ETag`"9ae86-6537ba48c39c0"`, and Last-Modified`Fri,05Jun2026 06:19:27GMT`.

Expected and archived SHA256 match:

```text
f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3
```

Container path:`/documents/artifact_content/f6/f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3.pdf`.

[Stored original PDF](</Volumes/My Passport/passport/trading-ai/artifact_content/f6/f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3.pdf>). Blob **144864**, canonical content **78206**, origin **publisher_original**. [Post-storage hash/size and existing-file verification](../../artifacts/phase4/canary-attempt-03/archive-verification.json).

Source **101555** is linked through the new attempt/source provenance row. Its identity, discovery-page evidence, URL, classification and original observed timestamp remain unchanged; only last_checked_at changed. No artifact/scope/queue identity was duplicated. Corrected TLS modules and the verified intermediate were sealed and checked before execution; Certifi/certificate/hostname verification remained enabled, partial-chain trust disabled, publisher restrictions unchanged.

## Queue and automatic processing transitions

Job144466: **RETRYABLE_FAILURE/attempt1 → fenced LEASED/attempt2 → SUCCEEDED/attempt2**. Backoff expired naturally before admission; no reset or timestamp forgery. Old failure attempt **84702** remains byte-for-byte equal as a decoded record and retains source101555 provenance. One new permit was released after the successful request; queue lease is released. Canonical success—not a manual hold-clear—settled next-attempt/error/host-health state.

Saved content78206 entered the durable inbox via the existing database trigger at04:48:40UTC. The automatic selector/materializer created **work1**, using explicit parser`document-structure`1.1.0/Tesseract5.5.0/English/PSM6/native-usability-v1 identity. No manual content/work-ID selection was supplied. The canary's selection stayed fenced to artifact81417 alone; its existing older discovery date accounts for the receipt's priority3 label, without admitting other historical contents.

Work1 references **extraction33641**, persisted **ok** at04:48:42UTC. Exactly **21 pages**, ordered1–21, all21 native-text usable, **0 OCR pages/0 unusable pages**, **50,450 selected-text characters**. Five required validations passed. **732 elements** persisted:708text blocks and24images. Extraction ownership is RELEASED. No processing failure was recorded.

The runtime's final content dictionary was captured before extraction and still displays`not_started`; the subsequent immutable database query verifies the actual content status **ok** and the linked recorded work/extraction. This is a stale logging object, not an unprocessed database result.

## Exact scoped database changes

**768 new rows** across13existing tables, accounted for by the selected lineage:

- response_blobs:1 (144864).
- artifact_download_attempts:1 (84703).
- artifact_download_attempt_sources:1 (84703→101555).
- artifact_download_queue_attempts:1 (144466→84703).
- artifact_contents:1 (78206).
- document_processing_inbox:1 (78206).
- document_processing_work:1 (work1).
- document_extractions:1 (33641).
- document_pages:21.
- document_page_elements:732.
- document_extraction_validations:5.
- document_extraction_claims:1 (new resource key, released).
- acquisition_host_permits:1 (released).

Five preexisting logical rows updated: queue144466 (success/count2/lease settlement); artifact81417 (preferred_content78206, validated acquisition, existing preference classifier); source101555 (last_checked_at only); the selected publisher's artifact_download_host_health (successful response settlement); its acquisition_host_state (normal permit/circuit success accounting). Newly inserted content/work/claim/permit rows were also finalized through their normal state updates; these are not extra inserted rows. No deletes, new artifacts/scopes/jobs, schema changes or financial-history writes were performed.

The SQL authorizer restricted mutations to document/permit tables, and exact document/processing count limits were checked at runtime. This is scoped lineage/accounting evidence, not a claim that a complete database byte-diff or SQL-statement audit was captured.

## Preservation and safety

Before/after full row fingerprints of **accepted_page_versions**, **financial_observations**, and **exchange_source_access_events** match exactly. BSE holds remain unchanged. Original failure84702 and both consumed earlier execution packages remain retained. External drive UUID/physical identity/sentinel and fresh reserve checks passed;185.25s run stayed within300s. New original hash/size and all four sampled existing originals match. No existing original was overwritten/deleted. Read-only post-verification itself left DB/WAL/SHM state unchanged; no manual sidecar deletion/checkpoint was performed.

Legacy source metadata remains honest: the source was discovered through Screener, so **is_official=0** is retained even though bytes came from issuer HTTPS. The existing preference classifier consequently records`machine_preferred`, while content_origin is`publisher_original`. Primary issuer/official discovery evidence remains a later integration task; this canary did not silently upgrade that flag or invent a quarter. Existing NULL reporting period and original source labels remain preserved. Detailed PPT/concall semantics and ML were not attempted.

Remaining historical processing backlog: **41,414 not-started presentation/transcript contents**. Inbox/work overall counts are now1/1, both corresponding to this completed canary. No historical backlog campaign ran.

## Expected lifecycle comparison and shortest remaining path

The bounded **retained source/identity → canonical queue claim → verified TLS/HTTP/media/hash → immutable archive → durable inbox → automatic versioned work → fenced extraction → persisted validated21-page outcome** is now proven in production. This is stronger than HTTP200 or a saved file alone. The new-filing/expectation/discovery handoff was already audited/tested, but a newly arriving current-quarter filing/document was not discovered by this retained-artifact canary; uninterrupted scheduled arrivals remain unproven.

1. Connect finite discovery/download/processing stage admissions to the existing host dispatcher with per-slot receipts, shared ownership, deadlines, fresh physical capacity and current/late priority. Keep historical allowance separately bounded/off initially and Phase3financial/cap schedules independent. **Do not activate this scheduling under the present authorization.**
2. Wire issuer IR and permitted official announcement sources to existing discovery/promotion/queue owners, retaining actual period/publication metadata and primary-source provenance. Preserve BSE API holds and prohibited rating-source restrictions.
3. Use the existing due-only delayed-publication checks; add a finite long-tail/previous-quarter final check after the existing cutoff. Preserve honest absence/unknown/blocked/retryable dispositions and bounded polling.
4. Add evidence-driven unchanged-URL revision acquisition: publication/ETag/Last-Modified and new byte hash must create an immutable version and automatic work under the same logical artifact, preserving old originals and outcomes. Current content-present dedup deliberately skips this case.

Then separately authorize controlled unattended activation and observe newly arriving current and late/revised documents completing the lifecycle without manual work-ID selection. Do not wait for the41,414-content historical backlog to finish. No remaining blocker was observed for this bounded PDF lifecycle; the four integrations/unattended observation remain Phase4 completion gates.
