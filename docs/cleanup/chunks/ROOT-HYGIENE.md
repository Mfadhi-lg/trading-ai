# ROOT-HYGIENE — bounded workspace/root hygiene

Status: **COMPLETE for the bounded workspace/root hygiene scope**, 7 October 2026. Unsafe relocations were kept and documented. No application refactor or production activation occurred.

## Exact starting state

Branch: `main`. Baseline HEAD: `c3ddbbb9c298d07b8b46a362996251417282476a`. Staged changes: none.

The complete baseline records Git status, every nonignored untracked path, ignored status, root file metadata, canonical application SHA256 hashes and retained data metadata. Secret/session/token contents were not read or printed.

[Baseline JSON](../baselines/ROOT-HYGIENE/baseline.json); [exact Git status before](../baselines/ROOT-HYGIENE/status-before.txt).

Pre-existing tracked edits are `artifacts/icra-download-test/icra-recovery.log` and `docs/cleanup/chunks/C28.1.md`; both are preserved. No application-code modifications or staged changes existed. Untracked cleanup archives/evidence, research plan, candidate, recovery evidence and ZIP are enumerated in the baseline.

Disk usage (KiB):

```text
57460	cleanup-candidate
1167660	OLD
13176932	artifacts
0	exchange-inbox
92	.pytest_cache
8852	trading-ai-share-2026-10-07.zip
```

## Canonical-source proof

Before hygiene, all **1,097 files** in C27's final application projection matched the integrated bytes and modes exactly. HEAD is the C28.1 integration commit. README, Dockerfile, Compose, root launchers, CLI/import paths and C26–C28 evidence identify `scrapper/` as canonical.

Static search of `scrapper/`, `ops/`, `scripts/`, `.github/`, Compose, Dockerfile and root launchers found **no application/operator dependency** on `cleanup-candidate/`, `a2-review/`, `b1-review/` or `b2-review/`. The materializer is retained reproduction tooling, not a runtime dependency on an existing candidate. Final directory traversal finds exactly one application directory named `scrapper`: the canonical `scrapper/`.

[Candidate comparison](../baselines/ROOT-HYGIENE/candidate-audit.json); [canonical tree check](../baselines/ROOT-HYGIENE/canonical-tree-check.json).

## Exact moves and removals

- `cleanup-candidate/` was removed from the active checkout only after installing and fully reading/verifying the external candidate archive. All **1,618 regular files**, including the full historical `scrapper/`, A2/B1/B2 trees, 387 documentation/evidence files, migration manifest and diagnostic scripts, are retained with original paths, modes and SHA256 hashes.
- `trading-ai-share-2026-10-07.zip` was copied to the sibling archive, its full bytes/mode/mtime were verified, then its active-root copy was removed.
- `.pytest_cache/` was archived and removed. It is pytest-generated cache; the reference audit found no repository-specific consumer. `.gitignore` now excludes it.
- `.DS_Store` was archived and removed from the root. Nested macOS metadata was left untouched.
- `exchange_securities_master.csv` was archived and its root duplicate removed. `resources/exchange/exchange_securities_master.csv` already held the exact same SHA256 (`2fe36df55b50243c2059fad458e5315eb0f0d1248b5afca48f7361b21a419e21`). The existing locator prefers that resource, which remains unchanged. There is no Python, import/bootstrap, identity or CSV-content change; the existing root fallback code is retained.
- `TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN.md` was renamed to [docs/current](../../current/TRADING_AI_MASTER_RESEARCH_AND_DOCUMENT_INTELLIGENCE_PLAN.md). Its content was not edited by this task. The rename preserved the bytes/hash observed at the time of the move. An intervening local document edit was observed: baseline 67,340 bytes, rename 73,608 bytes, later current/test snapshot 73,672 bytes. The latest version was preserved; the initial 67,340-byte version is retained inside the archived share ZIP. [Move record](../baselines/ROOT-HYGIENE/documentation-move.json) and [document observations](../baselines/ROOT-HYGIENE/documentation-observation.json) distinguish these states rather than claiming unchanged baseline content.
- `.screener_csrf` was removed **only from Git's index** with `git rm --cached`; its local file, mode, size and mtime remain unchanged. The existing ignore rule now applies. This staged deletion does not rewrite Git history. Token contents were not read or printed.

Other edits are limited to `.gitignore`, README and the current reference-import guide, plus this task's evidence. The pre-existing recovery-log and C28.1-note edits were preserved exactly. No application Python, SQL/migration, resource, launcher, Dockerfile or Compose file was edited.

## External archive and recovery

Exact destination: `/Users/mohamedfadhi/Fadhi/trading-ai-retained-cleanup-archive-2026-10-07/`.

- `cleanup-candidate.tar.gz`: SHA256 `8514e12167e6e774f82b2f50467908921d51f2992d5a17a296c0f1ed3add3c7c`.
- `root-local-retention.tar.gz`: SHA256 `4403e7b9dd98b3262c3526e25ac1781d2e6223ad6bd8644e69ad04d14f96412d`; seven files preserve root CSV, Finder metadata and pytest cache.
- Original share ZIP, unchanged.
- `MANIFEST.json`: SHA256 `f79959dd817fb7164b241bb1ea4ee5980c0adbdb8741291c7735812156a14189`; records SHA256, original root-relative path, byte count, mode and mtime for every archived file.
- `README.md`: recovery instructions and ownership.

[Retained archive manifest](../../../../trading-ai-retained-cleanup-archive-2026-10-07/MANIFEST.json); [in-repository manifest copy](../baselines/ROOT-HYGIENE/archive-manifest.json); [verified install](../baselines/ROOT-HYGIENE/archive-install.json).

Candidate classifications: **898** files have identical bytes at the same canonical path; **55** differ at an existing canonical path; **665** have candidate-only paths. Among the 720 differing/candidate-only files, **682** have independently verified identical bytes at another canonical/evidence path or in the C03 review archives. The other **38** were not assumed redundant: the complete new archive preserves them. A2 372 files, B1 282 and B2 6 are all included. The dated candidate exactly matched its original materialization manifest before archival. No unique bytes were discarded.

To recover, validate the archive and member SHA256 values against `MANIFEST.json`, then extract into a separate review directory. Do not extract over canonical application source or production mounts. Historical evidence that names the former candidate path remains immutable and is routed by this record to the archived root-relative member. Prior chunk archives, patches, manifests, harnesses and reports remain at their original `docs/cleanup/` locations.

## Intentionally retained root paths

- `OLD/`: **937 hashed files**, mostly PDF/XLSX/PNG/webarchive and historical focus inputs; one historical `PLAN.md`, no Python source files. No redundant bytes were independently established in the bounded comparison. Current `scrapper/research/focus/build_p0_governance.py` reads `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA` and `OLD/RESULTS/2026/Q4`, with `--repository-root` defaulting to the working directory. Moving these inputs would change current default behavior, so the entire tree stays. [OLD audit](../baselines/ROOT-HYGIENE/OLD-audit.json).
- `artifacts/`: existing intended campaign/research/source-evidence ownership. A0/A2/B0/B1/B2 and coverage directories retain immutable or review-only evidence; document-acquisition retains unique captured data; Focus retains governance evidence; ICRA retains recovery/runtime evidence, including the previously edited log; link-refresh retains the facade imported by ownership tests. The large B2 local-only export is already governed by C03/C26 retention. Generated nested caches/metadata were classified but not selected for removal. No evidence or intermediate payload was proven obsolete, so none was blanket-deleted or moved. [Per-group classification](../baselines/ROOT-HYGIENE/artifacts-audit.json).
- `exchange-inbox/`: currently empty, but production/development Compose bind it as `/inbox:ro`. Moving it would alter a declared input mount. Keep its existing operator-input path.
- `.scraper_shutdown_state`: current wrapper default; `SCRAPER_SHUTDOWN_STATE_PATH` supports another configured path, but no externally configured destination was selected or verified. Keep the recorded wake set at the existing location.
- `.screener_session`: Compose defaults its secret source to this root path; the configured override is `SCREENER_SESSION_FILE`. Keep the local source unchanged. `.screener_csrf` also stays locally because external/session-tool consumers are not proven absent; it is now ignored and untracked.
- `.env`: untouched, ignored and not committed; its contents were not read or printed.
- `TRADING_AI_CLEANUP_EXECUTION.md`: root control path is recorded by historical cleanup tooling, frozen controls and evidence. Its bytes remain unchanged.
- `TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md`: historical product direction, retained at the fixed path used by `docs/architecture/2026-10-01/collect_inventory.py` and historical manifests/links. Moving it would require altering retained reproduction tooling/evidence.

No active process depended on a path that was moved. Read-only Docker inventory showed no running containers before or after. Existing stopped production containers remained stopped; no service was started, stopped, rebuilt, recreated or deployed for hygiene.

## Verification and limits

- **Full regression:** pinned C05.3 dependency image, network disabled, read-only isolated current source, disposable fixture stores and no production DB/corpus/secret mounts. `python -B -m unittest discover -s scrapper/tests -t . -v`: **1,302 tests passed in 190.425 seconds**, no exclusions. [Exact log](../baselines/ROOT-HYGIENE/full-regression.log), [commands/results](../baselines/ROOT-HYGIENE/container-results.json), [container isolation](../baselines/ROOT-HYGIENE/container-command.json).
- **Architecture/import side effects:** full suite's ownership/import contracts passed; the separate instrumented import check passed with DB/network/writable-open/directory/process/thread startup forbidden. [Import log](../baselines/ROOT-HYGIENE/import-side-effects.log).
- **Migration identities:** `scripts/audit/check_migration_manifest.py` passed against C22.3's manifest: 75 active identities and the distinct historical identity preserved. This is a read-only manifest audit; no production migration was applied. Fixture tests used disposable databases only. [Log](../baselines/ROOT-HYGIENE/migration-lineage.log).
- **Compile:** requested host `python3 -m compileall -q scrapper`, with bytecode redirected outside the repo, returned 1 because the pre-existing `.venv` contains Python 2 `olefile/olefile2.py` syntax (`0xFFFFFFFAL`). No dependency was changed. Canonical source without the local environment was then compiled with `python3 -m compileall -q scrapper` in the pinned isolated environment: **exit 0**. [Host limitation](../baselines/ROOT-HYGIENE/compile-host.json); [pinned compile](../baselines/ROOT-HYGIENE/compile-pinned.json).
- **CLI/launchers:** direct package help and monitor help passed. Actual root `./scraper --help` and `./scraper-monitor --help` returned 0 using an explicit temporary Compose project with isolated source, no network and no production mounts. Actual root `./scraper status` returned 0 as a read-only observation. [Wrapper results](../baselines/ROOT-HYGIENE/wrapper-results.json).
- **Compose:** `python3 -B scripts/audit/check_compose_release.py`: **PASS**, 17 main/4 browser definitions, pinned image, separate development targets. Docker/Compose service semantics and all four Compose files are unchanged. [Result](../baselines/ROOT-HYGIENE/compose-validation.json).
- **Documentation:** all local file/anchor links in the documents added/changed by this task pass. Immutable historical evidence is preserved; moved candidate paths use this archive index. [Link check](../baselines/ROOT-HYGIENE/documentation-links.json).
- **Preservation:** all **642 canonical `scrapper/` files** match baseline SHA256/mode/mtime. The two pre-existing dirty files retain their original hashes/modes/mtime. All **26,540 retained local data metadata entries** (24,691 artifacts, 937 OLD, 912 store, including local symlinks) retain path/size/mode/mtime; inbox remains empty. Root secret/runtime file metadata is unchanged. HEAD is unchanged. [Verification](../baselines/ROOT-HYGIENE/preservation-verification.json).
- **Reference data:** actual default locator resolves to the unchanged `resources/exchange/exchange_securities_master.csv`; no hidden import, bootstrap or identity behavior was introduced.
- **Git:** `git diff --check` passes; no application-code edits. Exact [before status](../baselines/ROOT-HYGIENE/status-before.txt) and [after status](../baselines/ROOT-HYGIENE/status-after.txt) are retained. No commit, rebase, squash or history rewrite was made. The working tree still includes the original unrelated edits and untracked evidence; this task does not claim a clean Git status.

Production DB/schema/content: **unchanged by this task**. No production database connection, migration command, corpus relocation or deployment occurred. Existing data and runtime path configuration stay in place. Metadata checks are not a full external document-corpus hash audit.

## Final HEAD, root and disk comparison

Baseline and final HEAD: `c3ddbbb9c298d07b8b46a362996251417282476a` on `main`.

Root entries decreased from **33 to 27**, including `.git`. Selected path disk usage, KiB before → after:

```text
cleanup-candidate/                   57,460 → 0 (external verified archive)
OLD/                             1,167,660 → 1,167,660 (retained input)
artifacts/                      13,176,932 → 13,176,932 (retained evidence/data)
exchange-inbox/                          0 → 0 (mount path retained)
.pytest_cache/                         92 → 0
trading-ai-share-2026-10-07.zip       8,852 → 0 (external verified copy)
sibling retained archive                0 → 26,960
```

The duplicate CSV and Finder metadata also left the root. The current research plan moved within the repository, so that move does not save total repository disk space. No Git-history size reduction is claimed.

```text
trading-ai/
├── .git/
├── .github/
├── artifacts/
├── docs/
├── exchange-inbox/
├── experiments/
├── OLD/
├── ops/
├── resources/
├── scrapper/
├── scripts/
├── .dockerignore
├── .env
├── .gitignore
├── .scraper_shutdown_state
├── .screener_csrf
├── .screener_session
├── compose.browser-recovery.development.yml
├── compose.browser-recovery.yml
├── compose.development.yml
├── compose.yml
├── Dockerfile
├── README.md
├── scraper
├── scraper-monitor
├── TRADING_AI_CLEANUP_EXECUTION.md
└── TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md
```

Remaining follow-ups are separate decisions: configure and verify external historical-input/runtime destinations before relocating OLD/inbox/state; maintain a durable backup of the sibling archive and existing local cleanup evidence; repair/update the local dependency environment separately if whole-venv host compilation is desired; review/document a future relocation of the two retained root control/planning files together with their historical tooling. No follow-up is executed by this pass. Stop after this report.
