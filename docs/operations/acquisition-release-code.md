# Acquisition release code consistency

`ops/activation/acquisition_release.py:prepare_release` copies `scrapper`,
`ops/activation`, `ops/host` and `experiments` from the same explicit reviewed
source tree (or the workspace when no source is supplied). All four must be
real source directories. A source release whose code directory is a symlink is
refused before creating the destination. Preparation never activates a release.

Previously `experiments` was a live symlink to the workspace while `scrapper`
was copied. PR #114 introduced a candle storage import of
`scrapper.db.candle_inventory`; an older pinned package lacks that module.
Updating workspace experiments could consequently break imports in an otherwise
unchanged release. Merely adding the missing module to an old package is not a
complete dependency fix. Experiment code and its package dependencies now stay
on the same copied source snapshot, including when `--source` selects a separate
reviewed checkout. Subsequent workspace edits cannot change either side.

Mutable queues, artifacts, receipts, documents, credentials and ownership locks
retain their existing workspace links/inodes. The copied experiment modules
resolve their code root inside the release; its `artifacts` link still resolves
to the existing workspace evidence. No evidence is copied into a new data store,
no backend is selected, and no transfer or subscription is initiated.

Existing prepared or installed releases are **not rewritten** by this source
change. Activation refuses the old linked-code layout before importing the
scheduler or changing a LaunchAgent/pointer. Remediation requires a newly
prepared complete release from reviewed source, reconciliation of its existing
manifest, and a separately authorized deployment. Quiesce both acquisition and
candle/recovery workers using their existing ownership and drain checks first;
do not switch code underneath running workers. Preserve the old release and
retained stops/evidence. Do not use a symlink replacement, a package overlay,
`sys.path` fallback or a manifest reseal to conceal mixed versions.

Offline regressions:

```sh
python3 -B -m unittest scrapper.tests.test_acquisition_release_dependencies scrapper.tests.test_postgresql_operations.NativeReleaseWiringTests -v
PYTHONPATH=ops/activation:ops/host python -B -m pytest -q -p no:cacheprovider ops/activation/test_protocol_simplification.py ops/host/test_acquisition_lifecycle.py
```

The isolated subprocess regression reproduces the exact missing
`candle_inventory` import with the former layout, then verifies that a workspace
experiment update cannot break an older copied package. A second regression
verifies that both sides come from `--source`, not one side from the workspace.
These tests do not open production storage or activate a release.

The older `test_automatic_acquisition.py` schedule test also reads retained
operator canary evidence that is not in a source-only checkout. It is outside
these synthetic release regressions; absence of those files does not authorize
access to production evidence.
