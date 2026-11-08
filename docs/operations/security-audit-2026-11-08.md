# Repository security audit — issue #34

Audit date: 8 November 2026. **Partially remediated; keep #34 open.**
Base: `2a06cc10017352b143b2f96bf2e2cf838189830d` (`origin/main`).
Integration base advanced to `24eb48922d438d63a3cdfbc7aff18541932b3fdf` while
working (four unrelated operational files); operational regressions were rerun.
Implementation was prepared in an isolated worktree; the working checkout's
existing operational changes were excluded. No production database, acquisition
runtime, account credentials, retained data or existing Git history was changed.

## Credential findings and publication boundary

The [22 October report](security-backup.md) records a Screener CSRF credential in
local commit `e71d1ee168d4a2b5c155aa6302347aeaad841e46`. Rotation was deferred to
avoid interrupting acquisition. This audit does not establish expiry or
revocation and did not authenticate with historical credentials. Treat the
credential and associated session as exposed until the account owner completes
the maintenance procedure in that report. Do not include values or credential
digests in issues, logs or review evidence.

The exposed commit remains reachable through local `main`,
`fix-latest-2026-11-08`, `codex/tech-fix-01` and `chore/root-hygiene-pr` refs.
The PR branch starts from remote `origin/main`; the exposed commit is **not** its
ancestor. Only this explicit issue branch may be pushed. This does not sanitize
other refs, PR caches, forks or existing clones. No force push or historical
rewrite is authorized or performed here.

Gitleaks 8.30.1 scanned the isolated current tree with complete value redaction.
Its 110 generic-rule candidates were independently classified as 37 public
image signing fingerprints, 29 source/document hashes, 22 historical internal
queue lease identifiers, 14 research candidate identifiers, 5 fixture document
identifiers, 2 public reCAPTCHA site keys and 1 Git commit identifier. Queue lease
IDs were traced to `document_download_queue_repo.py` generation and lease-owner
checks; they do not demonstrate external credential revocation. No unresolved
external credential was identified among these current-tree candidates.

A complete **text-history** scan used `--all` against the available local refs
and remote-tracking refs. Gitleaks processed 63 commits with content changes
(72 total reachable commits), approximately 15.81 GB, and returned 1,095
candidates. These are detector candidates, not 1,095 confirmed secrets.
Independent review classified one as the confirmed historical Screener
credential, 18 as third-party S3 presigned document URLs with signing timestamps
and lifetimes already expired before this audit date, and 1,076 as hashes,
research/document identifiers, internal queue identifiers, public signing
fingerprints, public reCAPTCHA keys or extracted document prose. No credential
validation requests were made. This does not establish Screener revocation.
Ref inventory was compared with `git ls-remote --heads --tags origin`; no tags
were advertised. Arbitrary deleted/hidden refs were not fetched. Archive-deep
scanning was interrupted after decoder errors; full archive, encoded payload
and binary coverage is **not** claimed. An earlier 120-second text scan was
partial and was superseded by the completed 600-second-budget scan.

New `.gitleaks.toml` retains the default rules and adds detection for bare
Screener session/CSRF files, which generic assignment rules may miss. A synthetic
temporary credential file was detected with fully redacted results. The new CI
job scans every commit introduced by a PR or main push, including secrets added
and subsequently removed within that range. It does not certify old history.
No allowlist or historical baseline suppresses findings.

## Dependencies and remediation

`pip-audit 2.10.1` queried advisories for exact manifest versions without
installing those application dependencies (`--no-deps --disable-pip`). Initial
runtime and CI lock scans each returned 147 advisory records across four
packages. Counts include duplicate advisory records and are not unique CVE
counts. Both hash locks now change only these four package versions:

- certifi: 2026.7.22 → 2026.10.23 (minimum 2026.10.18).
- Pillow: 12.3.0 → 13.0.0.
- pypdf: 5.9.0 → 7.0.0.
- urllib3: 2.7.0 → 2.8.0.

The requirements ranges admit these patched versions. The active subsector
research pins also move pypdf 6.14.2 → 7.0.0 and certifi to 2026.10.23.
Runtime, test, host browser and active research inventories all returned **zero
known advisory records after remediation**. This is time-bounded advisory
evidence, not proof of absence of vulnerabilities or current deployed versions.
Machine-readable inventory hashes and remaining findings are in
[security-audit-2026-11-08.json](security-audit-2026-11-08.json).

Primary examples: [urllib3 HTTPS proxy TLS configuration](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77),
[pypdf infinite loop](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5849-834g-hvw5),
and [pypdf memory exhaustion](https://github.com/py-pdf/pypdf/security/advisories/GHSA-59jv-x5x2-gg4c).
The latter requires pypdf 7; a 6.x update alone would leave that finding open.

Remaining dependency surfaces:

- `docs/phase5/general-ai-experiment/requirements.runtime.txt` is a retained
  experiment environment snapshot. Its audit returned 72 records across
  certifi, filelock, Pillow and pypdf. It was preserved as historical evidence;
  it must be replaced by a tested current lock before using that environment.
- The vendored TradingView manifest pins requests 2.32.4; auditing that pin alone
  returned two duplicate records for `PYSEC-2026-2275` (fixed in 2.33.0). The
  other requirements are loose/unhashed, so a complete resolved transitive
  inventory is unknown. Do not edit a retained vendor snapshot to imply that
  its upstream revision was secure. A maintained integration needs its own
  patched lock and compatibility tests before activation.
- Retained artifact/release copies were not relocked or deployed. Existing
  pinned production images were not replaced; their installed-package and OS
  inventories were not inspected in this code task.

## Configuration and prevention

Both existing GitHub workflows now grant only `contents: read`, disable checkout
credential persistence, and pin checkout/setup-python to commits verified in the
official repositories. Existing job names, migration gates and isolated test
paths are preserved. The new workflow adds advisory checks on PRs, main pushes,
weekly schedules and manual dispatch; advisory/network failures fail the job.
It also uses checksum-verified Gitleaks 8.30.1 with fully redacted output on PRs
and main pushes. No raw scan artifacts are uploaded. The advisory tool itself
is version pinned; its bootstrap transitive dependencies are not hash locked.

Read-only GitHub API inspection found this repository public, with Dependabot
security updates, secret scanning and push protection disabled. This PR does
not alter repository settings. An owner should enable the available protections
and review any resulting alerts. CI scanning is not equivalent to GitHub push
protection and can be changed by source modifications that require review.

Targeted Git configuration inspection found the expected HTTPS origin,
heads-only fetch mapping and macOS keychain helper; no custom hooks path,
SSH command or TLS-verification override was returned for the queried keys.
This is not a full host/keychain/credential-store audit. Existing Docker source
configuration pins its base digest and uses a non-root account; it is not
evidence of the live image's security health.

## Verification and reproduction

Commands ran from the isolated repository, using a disposable Python 3.12.13
environment. Test commands used `TRADING_AI_ENV=test`,
`SCRAPER_STORE_PATH=/tmp/issue34-test-data/store` and
`SCRAPER_DB_PATH=/tmp/issue34-test-data/state/scraper.db`.

- PASS: `uv pip install --python /tmp/issue34-venv/bin/python --require-hashes -r scrapper/requirements.test.lock`;
  `uv pip check --python /tmp/issue34-venv/bin/python` (40 compatible packages).
- PASS: `uv tool run --from pip-audit pip-audit -r <manifest> --no-deps --disable-pip --format json --output <local-report>`
  for runtime/test locks, host browser lock and active research requirements.
- PASS: `/tmp/issue34-venv/bin/python -m unittest scrapper.tests.test_html_pdf_rendering scrapper.tests.test_credit_fresh scrapper.tests.test_credit_complete scrapper.tests.test_document_download_recovery scrapper.tests.test_d02_document_downloader -v`
  (50 tests).
- PASS: `/tmp/issue34-venv/bin/python scripts/audit/check_migration_manifest.py`;
  `/tmp/issue34-venv/bin/python -m unittest discover -s scripts/audit -p test_migration_lineage.py -v`
  (10 tests).
- PASS: `PYTHONPATH=ops/activation:ops/host /tmp/issue34-venv/bin/python -B -m pytest -q -p no:cacheprovider ops/activation/test_processing_pipeline.py ops/activation/test_paired_downloads.py ops/host/test_acquisition_terminal.py ops/host/test_monitor_usability.py`
  (39 tests initially; 42 passed after integration with current main).
- PASS: `/tmp/issue34-venv/bin/python -m unittest scrapper.tests.test_phase4_tls -v`
  (4 tests, rerun outside the sandbox to permit synthetic localhost socket binds).
- FAIL: `/tmp/issue34-venv/bin/python -m unittest discover -s scrapper/tests -t . -v`
  (1,635 tests; 6 failures, 73 errors, 73 skips). All 79 failure/error records
  were reproduced by rerunning the 77 affected test methods with the unmodified
  main dependency lock in a second disposable environment. These comprise
  Linux-only process fencing, macOS `/tmp` path assertions and two sandbox
  localhost bind errors. The two bind errors passed on the authorized rerun.
  No new failure was identified against that baseline. Linux CI remains required.
- PASS: `uv tool run --from actionlint-py actionlint .github/workflows/security-audit.yml .github/workflows/migration-lineage.yml .github/workflows/ml-readiness.yml`.
- COMPLETED WITH FINDINGS: `gitleaks dir --redact=100 --no-banner --report-format json --report-path <local-report> <worktree>`;
  `gitleaks git --log-opts='--all' --redact=100 --no-banner --report-format json --report-path <local-report> --timeout 600 --config .gitleaks.toml <worktree>`.
  Gitleaks exit 1 means candidates were found; it is not a clean scan result.
- PASS: independent review of dependency deltas, workflow logic, rule behavior,
  candidate classifications and test reports; no blocking implementation defect.
- PASS: all ten proposed changed/new files scanned with Gitleaks and the custom
  configuration, with no findings; `git diff --check`.

The expanded post-integration operational check also included
`ops/activation/test_guard_bse_repair.py`: 49 passed, 2 failed and 11 subtests
passed. Both failures require absent retained fixtures
`artifacts/bse-browser-results/2026-10-13-morning.{json,raw}` and reproduce with
the main dependency lock. Retained operational data was not copied to satisfy
tests. This additional check is not a clean pass.

## Remaining acceptance criteria and rollback

Issue #34 cannot close until owner-authorized credential revocation/renewal is
confirmed with redacted evidence. Follow the existing maintenance procedure;
deleting files or relying on age is insufficient. Full archive coverage and
hidden/deleted remote refs remain unverified. Optional environment remediation,
GitHub security-feature enablement and Linux CI results remain follow-ups.

No migration or data rollback is needed. Reverting this PR restores vulnerable
dependency pins and removes preventive controls; fix compatibility problems
forward where possible. Production deployment, credential changes and any
history rewrite require their own explicit authorization.
