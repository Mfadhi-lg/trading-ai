# C03.1 — recoverable review archives

These archives preserve review work. They authorize no application of patches,
deployment, migration, rule freeze or copied-tree deletion. The outer manifest
and this record govern status; embedded historical approval/freeze/runbook text
is preserved as evidence and does not override later supersession.

| Archive | Current disposition | Preserved work |
|---|---|---|
| A2.tar.gz | REVIEW_ONLY_NOT_ACTIVATED; access migration historically applied | Migration safety, immutable Compose proposal, access-block scheduler patch, tests and incident/release evidence |
| B1.tar.gz | REVIEW_ONLY_NOT_ACTIVATED; original-data reconciliation PARTIAL | Company-quarter feature interface/tests, retained 29-row evidence and three required B0 inputs |
| B2-draft.tar.gz | SUPERSEDED_PROVISIONAL_AUDIT_ONLY | Draft selector/tests, interrupted untested focus_v1, rule/decision/cohort history and supersession record |
| B2-discovery.tar.gz | RETROSPECTIVE_EXPLORATORY / PIT_NOT_VERIFIED | Discovery/export/analysis/verification scripts, exact Focus input, saved export, outputs and later review-v2 reconciliation |

Code anchor: main `db7564a109f1cb34ee4e7d5ea3427716e14cb51e` plus captured
dirty canonical bytes. B1 explicitly documents original base
`e71d1ee168d4a2b5c155aa6302347aeaad841e46`. A2 documented HEAD in its patch
builder rather than a hash: that same historical commit is a reconstructed
candidate, independently validated by patch application and exact resulting
unique bytes. B2 has no explicit base commit; comparison uses the B1 anchor
and treats b2-review as a sparse overlay, never a deletion of omitted files.
No branch ancestry is invented. C01.1's original temporary capture is now
absent; all 660 copied-tree files match the retained C02.2 source snapshot.
C02.1's source snapshot/bundle also remain separate retained evidence.

Each copied-tree archive has `original/`, `canonical/` and `review/` snapshots
limited to corresponding copied paths, two binary/mode-aware review patches,
`group-manifest.json`, and `artifacts/`. Supporting files are archived for
standalone reconstruction but classified separately from unique work. Current
canonical-only files omitted from a copied tree are not deletion proposals.
The canonical-to-review patch includes stale supporting copies; **it must not
be applied as a release patch**. The historical A2/B1 patches are separately
retained in artifacts and pass current-context checks; those checks do not
prove browser/schema/dependency compatibility or replace C04/C05 release work.

## Verify and reconstruct offline

Use Python's standard library; no dependencies, network or application imports
are needed. From this directory, verify file hashes:

```sh
python3 - <<'PY'
import hashlib,json
from pathlib import Path
for name,expected in json.load(open('checksums.json')).items():
    h=hashlib.sha256()
    with open(name,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    assert h.hexdigest()==expected,name
print('PASS')
PY
```

Extract each archive into its own **new private directory outside production
mounts**. Members are verified regular files with safe relative paths and no
environments/caches/secrets. Preserve permissions. For example, after unpacking
A2 into `$C03_A2`:

```sh
cp -R "$C03_A2/original" "$C03_A2/replay-original"
git -C "$C03_A2/replay-original" apply --check "$C03_A2/original-to-review.patch"
git -C "$C03_A2/replay-original" apply "$C03_A2/original-to-review.patch"
diff -qr "$C03_A2/replay-original" "$C03_A2/review"
```

Repeat with `canonical/` and `canonical-to-review.patch`, and B1/B2-draft.
Application is solely to reconstruction copies; original-to-review tests do
not change running code. The manifest records SHA256 and modes for every
source/input. `verification.json` records full archive-member bytes/sizes/modes
and all six patch replays, plus historical patch checks against both anchors.

For a full historical layout, copy `A2/review/` into a new `a2-review/`,
`B1/review/` into `b1-review/`, and `B2-draft/review/` into `b2-review/` under a
private root, then overlay all archive `artifacts/` directories under that root.
B1 includes `artifacts/b0-2026-09-29/{B0_SAMPLE_CHECK,B0_FIELD_AUDIT,B0_PIT_CLASSIFICATION}.csv`.
B2-discovery includes the complete 854,143,584-byte saved export and exact
`Q1 2027 FOCUS LIST.txt` in the draft archive. Keep the external root private.
The directory structure satisfies relative paths in the historical scripts.

Historical reproduction, only in that reconstructed root and an isolated
dependency-compatible runtime, is documented in the embedded A2/B1 handoffs
and B2 discovery README. Discovery arithmetic can be checked without source
requests using `python3 -B -m unittest test_analysis -v` from its directory,
then `python3 -B verify_outputs.py`. Running `analyze.py` or `review_v2.py`
rewrites only historical outputs in a disposable reconstruction; compare to
the archived originals and do not edit the canonical artifacts. The saved
export supports retrospective replay; it does not become PIT-certified data.
Do not execute incomplete/superseded focus_v1 or old threshold freeze commands.

## Regeneration and limits

`preserve.py --repo /absolute/captured/source --output /absolute/new/private/output`
recreates comparisons and archives using Git plus Python standard library.
It refuses output inside/above the source repository, prunes environments and
caches, validates embedded ZIP paths, and verifies every archive member. The
current validation uses the retained C02.2 source snapshot at the path recorded
in the manifest; that snapshot must remain available for regeneration.
Gzip packaging timestamps may differ; decoded bytes/modes and patch replay
are the recovery contract, and these exact delivered archives have fixed hashes.

No original database was queried and no financial reconciliation or application
test suite was rerun in C03.1. Prior test logs are historical evidence, not fresh
release certification. If future data comparison is selected, use a new copy
of the verified C02 restore and its read-only/fingerprint gates. Do not run
historical exporters against live/configured service databases.

No copied tree or old artifact was changed or deleted, no index/HEAD changed,
and no feature or service was activated. Rollback removes only the C03.1
additive documentation/archives; preserve all earlier dirty work. Next single
selection is C04.1, still unstarted.
