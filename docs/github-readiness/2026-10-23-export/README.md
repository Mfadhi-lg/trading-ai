# Safe GitHub export preparation — 23 October 2026

**Reviewed local candidate prepared. Private GitHub publication: BLOCKED.**

The candidate is [`../../../github-source-candidate-20261023`](../../../github-source-candidate-20261023/README.md), with independent local Git history at `272a1da16ab56afee737e3e83efb3cce32b0f8f1`. It contains 725 files, totaling 5,873,901 bytes. No remote is configured. This review continued the existing preparation and used the 22 October audit; it did not repeat the production inventory or perform broad cleanup.

- [Integrity report](INTEGRITY_REPORT.md): exact snapshot, checks, test results, limitations and preservation evidence.
- [Publication decision](PUBLICATION_DECISION.md): blockers and requirements before publication can be reconsidered.
- [Credential handling](CREDENTIAL_REMEDIATION.md): exposure and owner-authorized remediation, without secret values or credential hashes.
- [Final inclusion list](publication-inclusion-list.json): exact publication paths, sizes and SHA256 identities. This supersedes the initial capture list for final export verification.
- [Original-to-export mapping](original-to-export.json): all 725 destination paths, source identities, export identities and transformations.
- [Exclusion reasons](exclusion-summary.json) and [path-level exclusions](exclusions-from-prior-audit.csv): reuse the previous audit census; all paths outside the final manifest are excluded by default.

The candidate retains compatibility source, all 75 canonical migrations and the historical migration identity. Required Focus `OLD/` inputs remain preserved in the original repository and explicitly documented as restricted external dependencies. Their bytes are excluded from publication; Focus is not certified as runnable from this source snapshot alone.

Verified results: 92 core tests passed, 50 host fixture tests passed, 10 shell-routing cases explicitly deselected, three shell files passed syntax checks, and migration lineage verification passed. Core and host checks were repeated from an isolated materialization of the committed tree. The full historical suite remains outside the acceptance claim.

This evidence directory is local review material and is **not** part of the publication manifest. Do not publish the parent repository or recursively add this workspace. No production files were deleted, production databases opened, credentials rotated, original Git history rewritten or remote operation performed. Concurrent source edits remain untouched.
