# Phase 4 targeted TLS/retry repair — attempt03 prepared, NOT EXECUTED

12October2026 preparation evidence. **Subsequent authorized execution: attempt03 PASS, actual21-page lifecycle validated, scheduling still disabled. [Outcome](PHASE4_CANARY_ATTEMPT03_OUTCOME.md). The consumed package must not be replayed.** Phase3 remains COMPLETE; Phase4 remains IN PROGRESS. Both consumed canary packages and attempt84702 remain retained. No production queue/history/sidecar/document writes, certificate-validation bypass, worker activation, image rebuild or dependency change occurred in this repair.

## Confirmed TLS cause

The issuer at`www.sacheerome.com`/103.25.128.128 sends **one certificate only**, the leaf for`sacheerome.com` with SAN`www.sacheerome.com`. Its issuer is **RapidSSL TLS RSA CA G1**, omitted from the server's chain. Both explicit Certifi and Debian system-root handshakes fail at depth0 with unable-to-get-local-issuer. Host Python Certifi fails identically. No proxy/CA override environment is configured. Host/container Certifi2026.07.22 bundles match exactly: SHA256`9cc2a774b5198dcff14d9be1e66091f538975d867ce029a96bce15a55dfd730f`,121 certificates. The system bundle has150 certificates and does not supply the missing intermediate either.

Dockerfile pins Python3.12.14, installs the hashed dependency lock and OS packages. The existing image reports OpenSSL3.5.7; the lock specifies Certifi2026.7.22, Requests2.34.2 and urllib32.7.0. Existing roots are sufficient: the intermediate verifies to **DigiCert Global Root G2** already trusted by Certifi. This is a server chain-completion problem, not an outdated/mismatched Certifi bundle, missing system root or observed proxy certificate.

The public intermediate was obtained from`https://cacerts.digicert.com/RapidSSLTLSRSACAG1.crt` over verified HTTPS, with no redirect and a64KiB bound. DER SHA256`4422e963ee53cd58cc9f85cd40bf5ffec0095fdf1a154535661c1c06bcadc69b`; packaged PEM SHA256`48dc5154ee401053fcc4d22eff1881d81af249c27751820feb6c30ae623e9cf2`. Its signature/path to the existing root, issuer leaf signature/purpose/hostname and current validity were verified. Wrong-hostname verification rejects the same leaf with error62.

Evidence: [container stores/chain](../../artifacts/phase4/tls-repair/docker-tls.json), [host Python comparison](../../artifacts/phase4/tls-repair/host-python-tls.json), [chain/hostname checks](../../artifacts/phase4/tls-repair/verified-chain.json), [actual corrected context](../../artifacts/phase4/tls-repair/actual-context-verification.json). The first OpenSSL intermediate-only check also searched implicit system stores; [explicitly disabling those stores rejects the chain without a root](../../artifacts/phase4/tls-repair/openssl-no-root.txt), as does the actual SSL-context regression. No unverified certificate became a trust anchor.

## Small correction through existing transport

`documents/download/tls.py` installs a host-scoped Requests HTTPS adapter only for`www.sacheerome.com`. It starts from the normal urllib3 client context, retains CERT_REQUIRED/hostname checking and the unchanged `verify=certifi.where()`, loads the hash-pinned verified intermediate as a chain-building candidate and explicitly **disables partial-chain trust**. A trusted root is still required. No certificate lookup/fetch happens at runtime; no global Certifi/system store is changed; HTTP adapter retries remain0.

The existing standalone downloader installs this adapter before its request. The existing isolated shared-HTTP child installs the same adapter on its own Session, preserving process/deadline/permit fencing. Other hosts keep their existing transport. Existing source-access refusals, redirect validation, response/media/hash bounds and canonical URLs remain unchanged. Dockerfile and locked dependencies are unchanged; the existing image is reused with the existing read-only source-overlay method and sealed public certificate asset.

## Exact retry admission

`assert_retry_admissible` recognizes the frozen canonical **RETRYABLE_FAILURE/attempt1** state, including the unchanged next-attempt timestamp and captured prior claim token. It verifies the existing84702→source101555 provenance link, absence of a lease, genuine current UTC time, queue backoff, inherited host cooldown, shared permit/circuit deadlines, publisher restrictions and conflicting active work. It performs SELECTs only, first before work and again immediately before the canonical claim. The existing claimant increments to attempt2 normally; no reset, backoff shortening, timestamp rewriting or duplicate queue job is used.

The new plan retains the selected identity, expected bytes/hash/parser/pages and all original scope/bounds; it adds only exact retry-state and prior-attempt provenance expectations. Both older execution-started receipts remain intact. The consumed packages cannot be replayed.

## Focused validation

- **17 focused host tests PASS:**4 TLS cases,5 retry cases,8 fingerprint cases. Real localhost TLS tests demonstrate an omitted intermediate completed through the existing downloader, unchanged Certifi verification argument, unknown/missing roots and wrong hostnames rejected, pin corruption rejected, and certificate failures recorded without false success. Retry tests verify no early admission under queue/host/shared backoff, rejection of changed history/lease/provenance and canonical increment1→2 with the old attempt preserved. [Log](../../artifacts/phase4/tls-repair/focused-tests.log).
- **92 tests PASS in the existing Linux image**, including those17 plus bounded downloader, download-queue and isolated shared-executor compatibility/parent-death fencing. No production volume or external network was available to this test container. [Linux log](../../artifacts/phase4/tls-repair/linux-focused-tests-02.log). Initial compatibility runs exposed Linux-only host checks and then a guarded production-default test path; those test invocations were corrected to Linux with all fixture paths explicitly in/tmp. No production access or implementation guard was bypassed.
- The actual corrected client TLS context completed a live issuer handshake with root/hostname checks enabled and partial-chain trust off. It rejected a missing-root context; offline verification and localhost TLS reject incorrect hostname. These were **TLS-only diagnostics**, not an HTTP document GET or production lifecycle attempt.
- Existing extraction/storage/97-test lifecycle evidence is reused. No historical extraction or new release review was conducted.

## Actual production retry eligibility

[Read-only production preflight PASS](../../artifacts/phase4/tls-repair/production-retry-preflight/production-inspection.json), observed **12October09:51:04IST** (04:21:04UTC). Artifact81417/job144466 remains retryable/attempt1; prior attempt84702/source101555 are unchanged; no content for the artifact/expected hash exists; company4320 remains eligible. Queue due time00:42:37IST and host cooldown01:27:37IST have both expired naturally. Shared next-request/block/circuit deadlines have expired; no active publisher lease/permit prevents admission. No timestamp was changed to produce eligibility.

Current eligibility is **TRUE at that observation**, not a reservation or future override. Every execution rechecks state, genuine time, source policy and ownership. DB/sidecar snapshots remained unchanged across the immutable/query-only read; schema/file/control identities and physical storage/sample-hash checks passed. Source requests0, claims0, writes0. The sealed runtime/CA configuration matches the prepared package. [Host outcome](../../artifacts/phase4/tls-repair/production-retry-preflight/production-inspection-outcome.json).

## Prepared one-shot activation command — separate authorization required

[Attempt03 plan](../../artifacts/phase4/canary-attempt-03/plan.json), [seal](../../artifacts/phase4/canary-attempt-03/manifest.json), [verification](../../artifacts/phase4/canary-attempt-03/preparation-verification.json).

Exactly one Sacheerome artifact81417/job144466; one download and one automatic artifact-fenced extraction; maximum10MiB response; zero redirects;300-second deadline; expected634,502bytes and SHA256`f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3`;21pages; zero financial writes; no background or historical-backlog processing. All original work/source restrictions and storage/claim guards remain. Runtime authorization is **false** and no attempt03 execution-started receipt exists.

Only after separate authorization:

```sh
python3 ops/activation/phase4_documents.py --attempt-dir artifacts/phase4/canary-attempt-03 --execute
```

A successful production storage/provenance/inbox/work/21-page outcome is still required before unattended activation. Previously documented controlled-scheduling/source-discovery/long-tail/revision integrations remain outside this targeted repair.
