# AR-02 — combined immutable release candidate

8 October 2026. **PREPARED; package verification PASS; independent review PENDING; production activation/acceptance NOT_STARTED.** Authorized scope: combine the verified AR-02.1 NSE correction and AR-02.2 BSE operator-alert correction, focused offline image proof, preservation and review handoff only. Evidence classes SOURCE_ONLY, OFFLINE_IMAGE_BUILD, CONTROLLED_FIXTURE. No activation authority is supplied.

## Immutable identity

- Release: `c28.1-ar02-combined-20261008`, linux/arm64.
- Image: `trading-ai-ar02-combined-release@sha256:4206ea61254ec1c490e5284d1aac0361c08b084c7e1186a86141a42c1a2c0b08`.
- Platform manifest: `sha256:c12eb5317a2393c0272d8fb3552ed24df3e9c77e954b2bcca94552b87c20d622`.
- Config: `sha256:2c89cacd6a04b14021fc14ecc6614ff9d941ee4f8fcbf5c4c75c219753de048c`.
- Archive: `artifacts/acquisition-readiness/AR-02-combined-20261008/release.oci.tar`, 693,940,736 bytes; SHA256 `aff9a394c3ee49f8ebf85b234df7fc3ed594f8e10be22f02137f6408b659cef5`.
- Source base: accepted C28.1 `c3ddbbb9c298d07b8b46a362996251417282476a`, tree `eeb21f032e5bb4119279bc891c7342fa99eae7a1` plus exact combined patch SHA256 `cbcd98d53b6b2e7e6e05d445a0b3a807e34a8e14c41653f78599161f349e9650`; not an unmodified commit.
- Source projection: `aed3d89a082d7fe9852e69096169657f1f6875f66e29ad84345272536e6a3b56`; source-manifest SHA256 `795dcd1dcdafb2c00956ab884133e8913f4784f40089873ef5b04fa92734c9dd`.

[Release manifest](../../../ops/docker/release/ar02-combined-20261008/release-manifest.json), [authoritative OCI archive](../../../artifacts/acquisition-readiness/AR-02-combined-20261008/release.oci.tar) and [evidence checksum index](../baselines/AR-02-combined-20261008/SHA256SUMS) bind the review target. No registry publication occurred. OCI layers/config/platform/index/SBOM/provenance checks pass. Eleven dependency/base layers match accepted C28.1 exactly; only two application COPY/ownership layers differ. OS repositories remain moving, so the retained export is authoritative rather than a future byte-identical rebuild promise.

## Exact change and reused evidence

Isolated context from accepted commit has645 files; image application has643. Exactly four paths differ: universe.py, schedule.py and the two focused test modules. The only application behaviors added are observation-instant conversion to IST calendar date and the additive operator-action alert. Compared to original AR-02.1, only schedule.py and test_ar022_access_block.py differ. No removal, unrelated dirty file, secret, host application overlay, Dockerfile/lock/migration/hold change. See [source delta proof](../baselines/AR-02-combined-20261008/source-delta-verification.json) and combined.patch.

135 checksum entries reproduced across retained reviewed C28.1, original AR-02.1 package, AR-02.2 and AR-01.3 parent proof. Historical ledger hashes resolve to exact retained ledger-before copies; no mismatch was waived or historical index rewritten. AR-02.2 source/runbook hashes match its verified record. Original AR-02.1 archive, manifest, package and evidence remain byte-identical, including digest58060bbfeb8df338a3d3569c8f1489e0c1b784a1aa584f3b23a890e5dc5fe1ae. [Reuse index](../baselines/AR-02-combined-20261008/reused-evidence.json).

Reused unchanged proof: 94-test suite, authentic snapshot266/261 parser/guard replay, C28.1 integration, schema/migration/clone recovery and completed AR-01.3 A/B/C plus minutes0/5/15 closure. No full-suite rerun, original capture/replay, migration, restore or control canary repeated. Historical schema075/RELEASED facts are not fresh production preflight.

## New package proof

Ten focused packaged tests PASS: eight NSE date/guard cases plus two mixed-source BSE operator-alert cases. They prove valid NSE acceptance while BSE403 is durably held, explicit action delivered to the alert callback, no BSE re-request on later rung/day, unchanged hold history and null action when unblocked. Guard floor1 and header-only rejection remain unchanged.

The first image attempt passed the NSE cases but could not import the BSE helper because pytest is intentionally absent from production dependencies. That failed attempt is retained. Five test-only wheels from the existing hash-pinned test lock were then downloaded and retained; pip installs them offline with --require-hashes to /tmp/test-support only. The image is unchanged, no source is overlaid, and no helper or test was stubbed. Final network-none attempt runs both modules against packaged code. See image-proof.log, attempt-01-image-proof.log, test-support.lock and image-proof-command.json.

All643 packaged source hashes and zero extra application files pass; pip check, three inert CLI help paths and unidentified production-write rejection pass. Finite proof container: pull never, network none, no healthcheck, read-only root, capabilities dropped, no-new-privileges, bounded CPU/memory/PIDs, disposable fixture/tmp/data tmpfs and only review evidence mounted read-only. No production database, document store or credentials mounted.

All21 Compose roles locked to this digest; config differs only in image and candidate config-file paths. Default services zero; restart=no; authorized profiles and authorized:false identities retained. Before/after project inventories match with all15 containers exited. [Package verification](../baselines/AR-02-combined-20261008/package-verification.json). No operational service started. Initial dirty/untracked workspace bytes and unrelated master-plan deletion preserved; ledger before retained. No production data accessed; no live exchange request, hold/queue mutation or later chunk work.

## Review handoff and activation requirements

Next single smallest step: **one independent review of this exact combined package** using [review instructions](../../../ops/docker/release/ar02-combined-20261008/INDEPENDENT_REVIEW.md). Original AR-02.1 remains retained; its pending review is historical and is not silently accepted. Combined review covers both fixes together. No independent review is performed or fabricated here.

[Activation boundary](../../../ops/docker/release/ar02-combined-20261008/ACTIVATION_BOUNDARY.md) records outstanding requirements: independent ACCEPT, separately selected bounded current-result activation, fresh image/config/schema/owner/lease/sidecar/data baseline checks, named recovery authority and in-flight treatment. NSE needs a legitimate current snapshot passing guards. BSE needs actual operator alert delivery and unaffected NSE progress while existing holds remain; BSE-only coverage remains unproven. BSE access restoration additionally needs permission/configuration evidence and separately authorized bounded official recheck with valid results; denial cause remains UNKNOWN.

Offline rollback retains/supersedes only the new package. Production remains stopped on075; no migration replay, historical068 restore or C05.3 restart. No preparation-owned business claims exist; future writes require reviewed forward recovery preserving accepted history. Acquisition remains RED / NOT READY TO FREEZE. **Stop ready for independent review. AR-02.3 remains NOT_STARTED.**
