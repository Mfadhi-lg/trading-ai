# Export integrity review

## Reviewed object

Candidate: `github-source-candidate-20261023/` relative to the original repository. Reviewed tip: `272a1da16ab56afee737e3e83efb3cce32b0f8f1`; independent root: `44d306254a0230f6abfbbcad81a3f248be8d8a2e`. The root has no parents. Two commits and 726 reachable blobs contain 5,904,895 blob bytes across history. The tree has 725 tracked files totaling 5,873,901 bytes. The largest file/blob is 104,524 bytes. There are no configured remotes, object alternates, old-history parents or working-tree changes. `git fsck --full --no-reflogs` completed without findings. See `fresh-history.json` and `commit-integrity.json`.

The existing export was preserved and completed. No original `.git` directory was copied or rewritten. The candidate was not rebuilt from a drifting live tree during continuation.

## Inclusion, mapping and exclusions

`publication-inclusion-list.json` is the final explicit inclusion authority. The committed path set and every committed file hash match it exactly. `original-to-export.json` covers exactly the same 725 destinations, with source and export hashes where applicable; `mapping-verification.json` records the comparison.

The mapping comprises 714 byte-preserving copies, three export-only modifications, seven generated export-only files and one extracted historical SQL member. Copies include current scraper source and tests, compatibility facades, host/acquisition adapter source and tests, all canonical migration SQL, dependency requirements/locks, source-policy manifests, selected architecture/package documentation, CLI helpers, Docker build source and the public CA certificate. Historical adapter code is retained to preserve dependencies; inclusion is not deployment acceptance.

The three modifications are the migration manifest, lineage checker and lineage tests. They permit the historical SQL checksum to be verified from a plain source file instead of publishing its containing archive; legacy archive fixture support remains. `export-only.patch` records them. The extracted historical SQL member was validated against the original manifest checksum. The seven generated files are the candidate README, publication boundaries, ignore rules, minimal CI workflow, environment template, Compose example and guarded core regression runner. No production source was edited for these changes.

Operational databases/sidecars/backups, downloaded publisher content, original credentials, authorization seals, installed plists, operational Compose configurations, generated evidence, financial/reference exports, archives/images, native/model binaries, caches and unnecessary historical copies are excluded. Prior-audit path classifications are in `exclusions-from-prior-audit.csv`, with counts in `exclusion-summary.json`. That CSV is not a new inventory. Unlisted paths, including later additions, are excluded by default.

Focus depends on three historical tabular inputs under `OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA/` and Q4 evidence under `OLD/RESULTS/2026/Q4/`. Those inputs remain untouched locally. `docs/PUBLICATION_BOUNDARIES.md` inside the candidate records exact filenames and the supported separate `--repository-root` mechanism. Publishing their data requires separate clearance; the export deliberately makes no complete Focus reproducibility claim.

## Secret and sensitive-file review

Every candidate file was scanned without size skips for known local credential values and heuristic credential/private-key patterns. No known local credential value was copied. Three URL-auth pattern matches are synthetic refusal-test inputs, individually reviewed in `secret-candidate-review.json`. The complete reachable candidate blob closure was separately checked for known local credentials, private keys and provider/URL-auth patterns; only those same three synthetic tests matched. No confirmed credential was found in the candidate.

The hidden-file list contains only `.dockerignore`, `.gitignore` and `.github/workflows/source-regressions.yml`. No symlink, database/archive/document binary magic or file over 1 MiB was found. The retained PEM is a parsed public CA certificate, not a private key. See `secret-scan.json`, `static-integrity.json` and `commit-integrity.json`. Heuristics and known-value comparisons cannot prove absence of every unknown or encoded secret.

The historical credential in the original repository remains an outstanding owner action; see `CREDENTIAL_REMEDIATION.md`. It does not occur in the fresh candidate history. Remote exposure and server-side validity were not probed.

## Imports, dependencies and portability

AST parsing/compilation found no syntax issues or unresolved static local package imports. Local script imports and the optional deployment renderer fallback were reviewed separately; see `conditional-deployment-imports.json`. This is static resolution, not an import or execution test of every module.

Five optional research/transcription dependencies have no supplied lock declaration: `pyarrow`, `faster_whisper`, `huggingface_hub`, `mlx` and `mlx_vlm`. Other identified third-party imports correlate to supplied locks. Browser/runtime tools such as Chrome, Playwright browser binaries, OCR and ffmpeg remain external prerequisites. No package or binary was downloaded during this review.

There are 14 machine-path occurrences across source, examples and tests, including live adapter paths, identity guards and synthetic test paths. They are enumerated by file/line in `static-integrity.json`. Safety guards were preserved. The candidate is not portable production deployment configuration. The static scan also records 84 absent literal data/evidence references; some are output destinations, others are real required inputs. Focus data, exchange master observations, deployment authorization/release seals and publisher/financial test fixtures remain external or unavailable. Constructed paths and dynamic imports are not exhaustively resolved.

## Offline validation

The reviewed committed tree was materialized with local `git archive` into a disposable directory, without a remote operation or production mount. The core runner denies Python socket network operations, limits SQLite connections to memory or its temporary fixture root and blocks non-Python subprocesses. Applicable safe tests use synthetic/temporary fixtures. Python subprocesses inherit the guard. This is a test safety guard, not an OS sandbox for arbitrary untrusted native code.

- Core regression target: **92 passed, zero failures/errors/skips**, including migration, ownership, permits, runtime paths, reporting periods and financial observation/acceptance checks. See `from-commit/offline-tests.json` and log.
- Migration verification: **75 canonical migrations and one historical identity**, checksums and prefix-collision policy verified. See `from-commit/migration-lineage.json`.
- Host regression target: **50 passed, 10 explicitly deselected**. See `host-tests.json` and log. The excluded cases execute shell subprocesses and are outside the runner's subprocess policy.
- Shell source: **three files passed `bash -n`**; this verifies syntax only. See `shell-syntax.json`.

The initial host run exposed seven failures because historical fixtures lacked prerequisites added to `wake()`. The local review harness now supplies a synthetic temporary session marker and mocks console identity, Chrome presence and sealed-image configuration, while the existing fixtures mock operational subprocesses. This changes the review harness only; candidate and production application/test source are unchanged. The final host result was reproduced from the committed-tree materialization. These tests do not validate an active session, Docker, launchd or real storage.

The full historical suite was not accepted: retained test source includes imports that eagerly require excluded publisher/B0/operational fixtures, and optional research dependencies remain incomplete. Existing initial test logs are diagnostic evidence, not passing acceptance results. CI configuration has not been exercised on GitHub or a fresh network-provisioned machine.

## Preservation and concurrent work

The original HEAD and index hash still match preparation baselines (`preservation.json`). No original production/application files were intentionally written, no database contents changed, no credentials revoked and no remote operations performed. Live databases were neither opened nor frozen; this report does not claim a new production backup or whole-database integrity check.

Four selected original source paths differ from initial source identities: `ops/host/acquisition_lifecycle.py`, `ops/host/acquisition_queries.py`, `ops/host/acquisition_terminal.py` and `ops/host/test_acquisition_terminal.py`. Two earlier captures are recorded in `concurrent-capture.json`. The candidate remains frozen at its manifest hashes, and later original edits were neither restored nor overwritten. It represents the recorded snapshot, not a claim to include every latest concurrent change. Any refresh requires a new explicit mapping/hash review and applicable tests.
