# C04→C17.4 cumulative candidate materialization

Status: **MATERIALIZED and repeat-verified as an isolated source-tree projection**. The tree is not canonical `main` and no promotion is performed by this record.

## Durable inputs and reconstruction

The builder uses the in-repository source snapshot `docs/cleanup/baselines/cumulative-candidate/source-baseline.zip` (SHA256 `b272786bf279109edab8b6ecbdf4717773449afaf6fa18b2d8f0092e20fa47c9`) plus 50 checksum-locked chunk patches listed in [`composition.json`](baselines/cumulative-candidate/composition.json). It applies them in chunk order and records whether each file patch was applied, already present with an exact reverse match, or composed by the one declared C04.2 workflow rule. C06.1 and C07.1–C07.3 have no candidate patch; their disposition is recorded in the composition manifest. Environment and cache paths are excluded from the source export. The export contains no `artifacts/` payloads; the C17.4 dated facade is restored separately below. Collected data and the full chunk harness/evidence archives remain at their original repository locations outside this source-tree projection.

C17.4's historical harness contains the original `artifacts/rating-link-refresh-20261001/refresh.py` as `before/artifacts/rating-link-refresh-20261001/refresh.py`. The builder verifies its SHA256, restores it into the source tree, then applies the C17.4 patch. The final dated facade SHA256 is `26213b451546b401c1f029590202833e3d0c78f792cc1426fdbc0fa23118c247`, matching the retained candidate postimage.

The repeatable builder is [`materialize_cumulative_candidate.py`](../../scripts/cleanup/materialize_cumulative_candidate.py). It reads only the checked-in source snapshot, chunk patches, C17.4 harness and composition manifest. Its scratch Git index and repeat-build directory are created alongside the requested output inside the repository and removed after use; it does not use `/private/tmp`.

```sh
python3 scripts/cleanup/materialize_cumulative_candidate.py \
  --output cleanup-candidate/C04-C17.4 \
  --manifest docs/cleanup/baselines/cumulative-candidate/materialization.json \
  --verify-repeat
```

The builder refuses to overwrite an existing source tree or evidence file. A later replay uses fresh output and manifest paths.

The output directory is a single inspectable source tree with 1,618 regular files and 55,004,165 bytes. Two independent builds produced the same tree SHA256:

`8b44ba9cf7cf40625dc5cf7aedb14aba91237dedd5c16dbd34f6d703889bb714`

The full path/mode/size/content manifest and 331-entry patch trace are in [`materialization.json`](baselines/cumulative-candidate/materialization.json). The trace records 320 applied file patches, nine exact postimages already in the source snapshot, one C04.2 workflow composition, and the C17.4 facade restoration. This hash covers the source projection, including the dated facade; it does not hash separately retained project data or harness archives. No application test suite or production operation was run as part of this tree-reconstruction proof.

## Preserved history and promotion boundary

Every original chunk patch, evidence report, verification record, test/replay log and harness remains at its per-chunk path and retains its original bytes. The new composition manifest references those artifacts without replacing them. The materialized tree is kept separately at `cleanup-candidate/C04-C17.4` for review.

The later promotion is **C28.1 — promote the C27-frozen cumulative candidate into canonical `main`**. C27.1 first completes final parity and freezes the exact source-tree projection hash and file manifest, including any later selected overlays, plus a separate retention manifest for project data and generated artifacts. C28.1 then integrates the accepted source projection and verifies that the corresponding projection of `main` matches the frozen tree while retained data/artifact paths remain intact. It does not replace the whole repository with this source-only export. C28.1 is the only planned chunk that folds this candidate into `main`; the current materialization remains isolated until then.
