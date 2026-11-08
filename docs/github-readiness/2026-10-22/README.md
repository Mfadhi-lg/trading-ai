# Trading AI — GitHub readiness audit

Audit began 22 October 2026 IST and finished after midnight on 23 October. **Decision: do not push the present branch/history/index. Recommend a private repository with a separately reviewed source-only publication lineage.** This audit changed no existing Git, application, production, historical or experiment files; its additions are confined to this directory. No acquisition, migration, worker, model inference, upload or history rewrite was run.

## Eight decisions

1. **Canonical today:** the C28.1-integrated `scrapper/` source plus later individually sealed acquisition adapters, operated by `ops/activation/automatic_acquisition.py --tick` through the installed Aqua minute LaunchAgent. Production financial SQLite is the Docker volume `trading-ai_scraper-data:/data/scraper.db`; documents are on My Passport. The host `scrapper/store/scraper.db` is a different store. Root Compose describes retained maintenance roles, not the current automatic ownership topology.
2. **Verified:** C28.1 source integration/offline parity; historical schema075 migration/control-canary/restore evidence; actual unattended financial acceptances; bounded document canaries and a new rating PDF download with a fresh matching file hash. This audit passed **126 existing offline tests**, no skips. **A complete unattended new-document lifecycle and fresh generic presentation/transcript acquisition remain unproven.**
3. **Required legacy:** public CLI/facades, exchange identity/reconciliation/catch-up, historical selectors, distinct rating/browser/recovery paths, leases/policies/ownership guards and Focus historical inputs under `OLD/`. Old continuous scheduling loops are operationally superseded, but no whole Python module is newly approved for deletion.
4. **Exclude from GitHub:** secrets, all operational databases/sidecars/backups, downloaded content, exports, runtime state, images/OCI/harness archives, generated extraction payloads, samples and experiment outputs. Retain them locally and in verified restricted backups; exclusion does not authorize deletion. Synthetic fixtures require individual review rather than blanket binary exclusion.
5. **Retention/migration first:** consistent current DB + matching immutable-document backup to another device, restore proof, durable relocation of temporary release/reconstruction evidence, historical Focus input access, and a selected source manifest with its required safe controls/fixtures. Current local AR snapshots are dated recovery evidence, not a current backup.
6. **Private push ready? No.** A nonempty CSRF credential remains reachable from HEAD. Large committed blobs and staged OCI/TAR files also block a source-only push. Archives, redistribution rights and untracked source need final clearance; `.gitignore` cannot fix tracked objects/history. Earlier evidence reports the existing remote as private; this offline audit did not refresh remote visibility or establish remote exposure.
7. **Smallest safe cleanup:** retain this isolated audit package and, as a subsequent documentation-only change, repair current status routing to the latest evidence. No application/data cleanup is necessary to review these findings. A reviewed source allowlist in an independent export is the smallest useful Git preparation; do not apply broad untracking or the old worker-removal patch automatically.
8. **Finance-only development suitable? Yes, conditionally** for read-only exploratory features over accepted standalone data with explicit identity, scope, intervals, units, version/capture cutoff and sector applicability. It is not yet a certified whole-universe, historical PIT, bank-risk or investment-scoring dataset. Use the existing feature facade; keep screening, Focus labels and moat scoring separate.

## Package contents

- [Inventory and classifications](INVENTORY.md), with the exact local path census in `file-classification.csv` and sizes in `directory-summary.json` / `large-files.json`.
- [Canonical workflow and supersession](WORKFLOW.md).
- [Database and backup retention](DATABASE_RETENTION.md), full schema/count/hash metadata in `databases.json`.
- [GitHub manifest and safety findings](GITHUB_MANIFEST.md), redacted Git scan in `git-safety.json` and exact-index/local-source scan in `source-secret-scan.json`.
- [Acquisition verification and acceptance gaps](VERIFICATION.md), fresh tests and runtime identities in the accompanying JSON/logs.
- [Finance-only handoff](FINANCE_HANDOFF.md).
- [Proposed changes and approval boundaries](APPROVAL_BOUNDARIES.md).

Human reports are concise; the larger CSV/JSON appendices preserve the exhaustive local metadata census without copying production rows or document bytes. **The audit package itself is local review evidence, not automatically cleared for GitHub**: it contains private paths, source/object identities, schemas and aggregate coverage. Links to existing evidence describe inspected inputs, not files to copy blindly into a public or private repository.

Scope limits: no fresh production database connection or full corpus hash/integrity scan; no current backup created; no remote call. Heuristic secret scans cannot certify absence of secrets in compressed archives or unknown encodings. Database counts refer to the explicitly named immutable historical snapshots, never today's production totals.

Final worktree qualification: concurrent changes appeared after the baseline census (Control Center paths disappeared; scraper-monitor and new host terminal/query/observation source appeared). See `status-drift.json` and `concurrent-source-scan.json`. Five added/changed source files received a static heuristic scan with no matches, but no execution/compatibility acceptance. This audit did not change or restore those paths. HEAD and all516 acquisition seal hashes still match; the complete working tree was not stationary.
