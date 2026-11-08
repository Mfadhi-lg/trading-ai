# Phase 4 exact canary attempt02 — STOP: TLS certificate validation

Executed once after the user's separate authorization, starting **12October2026 00:26:17IST** (11October18:56:17UTC) and ending00:27:38IST with exit1. Sacheerome artifact81417/download job144466; original one-document/one-extraction scope,10MiB/zero redirects/300seconds/zero financial writes were unchanged. No automatic retry, alternative acquisition, guard bypass, background worker or historical campaign occurred.

## Actual failure

The repaired preservation fingerprint succeeded. The canonical guarded downloader then recorded attempt **84702**, result`network_error`, with:

```text
SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: unable to get local issuer certificate
```

No HTTP status or validated document body was obtained. The canary correctly stopped with `No saved validated content; processing not attempted`. The TLS trust/issuer-chain cause inside the permitted Docker transport is not yet diagnosed; no change to certificate validation or source policy was made. “Max retries exceeded” is the library's exception text; the durable queue records exactly **one** canary attempt, not an automatic canary replay.

[Runtime receipt](../../artifacts/phase4/canary-attempt-02/runtime.log), [traceback](../../artifacts/phase4/canary-attempt-02/runtime.stderr), [launcher outcome](../../artifacts/phase4/canary-attempt-02/outcome.json).

## Verified durable state and preservation

[Machine-readable verification](../../artifacts/phase4/canary-attempt-02/verification.json), [read-only production records/fingerprints](../../artifacts/phase4/canary-attempt-02/verification/production-inspection.json), [archive samples](../../artifacts/phase4/canary-attempt-02/archive-after-failure.json).

- Job144466 transitionedPENDING/attempt0 → **RETRYABLE_FAILURE/attempt1**, with lease released. The captured claim token remains provenance/fencing evidence.
- Attempt84702 is associated with canonical artifact81417 and source **101555** through the append-only attempt-source link. Source identity/URL remained unchanged. Response headers/redirect evidence are empty; status/media/bytes/content hash/blob/content IDs areNULL because TLS verification failed.
- Exactly one acquisition host permit was recorded and is **RELEASED**. Existing canonical host-health/backoff was updated for this failed selected source; its cumulative block count is not four canary requests.
- Queue next-attempt time is **00:42:37IST** on12October; the host cooldown runs until **01:27:37IST**, which also constrains future admission. Neither was cleared, shortened or overridden. No automatic document worker was activated to consume this retry state.
- **Zero** canonical contents for the artifact/expected hash; zero processing-inbox/work/extraction/page rows. No original document bytes were saved. Expected SHA256 and21-page production extraction were therefore **not achieved**. Earlier offline results are retained evidence, not a substitute production success.
- Actual post-run SHA256 fingerprints of **accepted_page_versions**, **financial_observations**, and **exchange_source_access_events** match all three pre-canary fingerprints exactly. This confirms preservation of the protected financial history and BSE access holds. SQL authorizers also continued to deny financial writes. The DB itself changed only through the authorized failure/claim/provenance/host records; no claim that its entire file stayed byte-identical is made.
- All four previously sampled archive originals still match their SHA256/size; the physical storage identity hash is unchanged. An initial host file lookup failed transiently; rechecking the retained blob paths succeeded without filesystem repair, remount, deletion or file changes. External USB/APFS UUID remains`60E1204B-CB98-48C4-9398-1D2FBAC9B44A`.
- Fresh physical reserve checks passed before the attempt. The authorized capacity metadata was published once. No original was overwritten, deleted or duplicated; no artifact/scope/queue identity was created. The consumed attempt02 receipt is preserved and cannot be silently replayed.

## Next, before unattended activation

Resolve the TLS certificate-validation failure through the existing permitted Docker downloader, retaining certificate verification, source restrictions, shared permits and backoff. Prepare a separately authorized bounded attempt against the same artifact/job and its **actual existing retry state**; do not reset it toPENDING/attempt0 or reuse this consumed package. A successful download/storage/provenance/inbox/work/21-page extraction must be demonstrated before controlled unattended scheduling. Previously documented issuer/official-source, long-tail and unchanged-URL revision integration work remains.

No implementation or production retry was initiated in response to this failure. Phase3 remains COMPLETE; Phase4 remains **IN PROGRESS — DOWNLOAD CANARY STOPPED**.
