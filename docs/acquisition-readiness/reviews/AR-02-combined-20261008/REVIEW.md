# Combined AR-02 release review — ACCEPT

Signed off: **Codex /root**, 2026-10-08T12:41:23.518379+00:00. Decision: **ACCEPT for the exact immutable package's technical and evidence review**. Production activation is **NOT_STARTED / NOT_AUTHORIZED**. No substantive blocking code/package findings.

Reviewer disclosure: this is a fresh verification pass by the same Codex assistant that prepared the package, using newly written read-only archive checks. It is not a separate-agent or human review. No separation-of-duties acceptance is claimed; any activation requirement for an independent reviewer distinct from the preparer remains outstanding. This written AI sign-off is integrity-sealed, not a human or cryptographic identity signature.

## Exact accepted technical review target

- Image: `trading-ai-ar02-combined-release@sha256:4206ea61254ec1c490e5284d1aac0361c08b084c7e1186a86141a42c1a2c0b08`.
- Platform manifest: `sha256:c12eb5317a2393c0272d8fb3552ed24df3e9c77e954b2bcca94552b87c20d622`.
- Config: `sha256:2c89cacd6a04b14021fc14ecc6614ff9d941ee4f8fcbf5c4c75c219753de048c`.
- Archive SHA256: `aff9a394c3ee49f8ebf85b234df7fc3ed594f8e10be22f02137f6408b659cef5`; 693,940,736 bytes.
- Combined package SHA256SUMS hash: `46965ab5e380794dff07060feda9ee8c76f2e9921189885253e282c8525c68f4`.
- Source projection: `aed3d89a082d7fe9852e69096169657f1f6875f66e29ad84345272536e6a3b56`.
- Source manifest SHA256: `795dcd1dcdafb2c00956ab884133e8913f4784f40089873ef5b04fa92734c9dd`.
- Base: `c3ddbbb9c298d07b8b46a362996251417282476a` plus combined patch `cbcd98d53b6b2e7e6e05d445a0b3a807e34a8e14c41653f78599161f349e9650`.

The reviewed package is unchanged. Its preparer PENDING status fields are preserved as original sealed preparation evidence; this separate review record records the technical decision without rewriting the immutable review target or claiming a distinct reviewer.

## Checklist outcome

1. **Identity and archive PASS.** Recomputed all56 sealed package checksums, authoritative archive size/hash, OCI index/platform/config and all13 compressed layer digests. Independently checked every uncompressed layer against config diff_ids and both SBOM/provenance statement subjects against the platform manifest. Labels bind exact base/patch/projection/version. Verified actual archive application hashes, all643 files, with no extra application files or links. The check reads OCI tar bytes directly without loading or running any image.
2. **Intended source changes PASS.** Compared645 context files to a fresh git archive of accepted C28.1. Exactly universe.py and schedule.py change and two focused test files are added. No removed paths; Dockerfile, dependency locks, migration files, hold logic, endpoints and policy are unchanged. Against original AR-02.1 only schedule.py and test_ar022_access_block.py differ. Inspected full combined.patch and related real source/guard/dispatcher paths. IST conversion uses the observation instant, rejects naive timestamps and preserves future-date/row-floor guards. Alert field is additive, null when unblocked, and dispatcher's existing non-ok alert path receives it; it cannot resume a hold.
3. **Tests and reuse PASS.** Final retained image proof runs ten tests in2.507s with exit0, packaged source hashes, pip check, inert CLI help and fail-closed runtime identity. Reviewed test assertions and injection paths: NSE IST boundary/true future/malformed/invalid-period/header-only guards and BSE403 durable isolation/operator action/NSE accepted result/no later BSE retry. No guard override or artificial fetch success. Network-none proof uses read-only image, bounded resources, ephemeral storage and only evidence read-only; no production/source overlay. Five test-support wheels match the accepted test lock and hashes, installed offline on tmpfs. First pytest import failure retained and transparently superseded. Reused94-test and authentic261/266 replay, reviewed migration/recovery and completed AR-01.3 parent proof. Verified135 retained evidence entries; did not rerun completed tests/builds/restores/migrations.
4. **Preservation and activation boundary PASS.** Rechecked760 preexisting path identities including original AR-02.1 OCI/package/evidence and unrelated working-tree edits/deletion; no drift. Prior ledger bytes retained. Verified all21 Compose services against accepted configuration: only image reference/config-file paths differ; digest locked, authorized profiles, restart=no, no build. Core/document identities are authorized:false, sentinels are proposed files only. Retained default-services output is empty and exact before/after15-container inventories show all exited. These are historical inventories, not fresh activation preflight. Reviewed BSE operator runbook and activation boundary. No production database inspection or state assertion inferred from offline tests.
5. **Sign-off recorded.** Decision ACCEPT for technical/evidence scope; exact digest and checksum-index hash bound above and in review.json. Reviewer-identity limitation disclosed, production authorization false, and activation conditions retained.

## Findings and limitations

- **R1 — nonblocking evidence-path qualification:** reused-evidence.json includes one old hash under the current ledger path. Independently reproduced it from `baselines/AR-02-combined-20261008/ledger-before.md` (SHA25686da1b945d3d9423a76e7c2f6c029fc3197c8dd455a89664e579d75d5f851e23). The final current ledger is separately sealed. No hash waiver or package edit; verification.json records the exact resolution.
- **R2 — nonblocking test dependency:** the first attempt lacks pytest because runtime excludes test dependencies. Final valid attempt uses hash-pinned test-only wheels without changing the release. This is test setup, not an application failure.
- Reviewer separation is not established by this same-session review. A distinct reviewer must supply acceptance wherever that activation gate requires it. No such acceptance is fabricated.
- Local digest and attestation integrity checks do not claim registry publication or a publisher identity signature. Moving OS repositories do not guarantee future byte-identical rebuilds; this export is authoritative.
- Mock alert callback proof does not establish actual operator delivery; that remains a production acceptance requirement. Current legitimate NSE filings, BSE-only coverage/access restoration and operational heartbeats remain unproven. Historical stopped/schema075/RELEASED observations do not replace fresh safety checks.

## Activation remains outstanding

Obtain any required distinct-reviewer acceptance; separately authorize a bounded procedure with exact source/period and request/write/time/concurrency caps, one owner, named recovery authority, stop thresholds and in-flight handling. Fresh image/config/schema/owner/lease/sidecar/data-baseline checks must precede it. NSE must accept legitimate current input without bypass; header-only input stays held. Prove actual operator alert delivery and unaffected NSE progress with BSE holds intact. Access restoration additionally needs official permission/configuration evidence and a separately authorized bounded valid-result recheck; the underlying denial cause remains UNKNOWN.

Preserve accepted history/bytes/timestamps/watermarks/holds/queues/RELEASED receipt. No migration replay,068 restore or C05.3 restart. Future business writes require reviewed forward recovery. Review-only rollback leaves production untouched and retains all exports.

**Stopped after review.** No image modification/rebuild, Docker command, service startup, production access, exchange request, BSE hold change or AR-02.3 execution. Acquisition remains RED / NOT READY TO FREEZE.

Evidence: verification.json, verify.py and this directory's SHA256SUMS. The review checksum index seals these added records independently of the unchanged release package.
