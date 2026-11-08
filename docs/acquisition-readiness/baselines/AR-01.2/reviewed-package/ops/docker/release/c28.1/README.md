# C28.1 AR-01.2 candidate

Offline immutable release built from exact reviewed commit
`c3ddbbb9c298d07b8b46a362996251417282476a`. Independent release review is pending;
production is not activated. The candidate excludes the uncommitted AR-02.1 patch.

Start with [the evidence record](../../../../docs/acquisition-readiness/chunks/AR-01.2.md),
[release manifest](release-manifest.json) and [rollout/recovery proposal](ROLLOUT_ROLLBACK.md).
The checksum index is `docs/acquisition-readiness/baselines/AR-01.2/SHA256SUMS`.
`compose.candidate.json` is a standalone Compose file. All services require
separate profiles; all production write identities are locked. Do not replace
root Compose or authorize identities as part of release review.

Exact build recipe (from repository root, with verified context retained):

```sh
docker buildx build --platform linux/arm64 --provenance mode=max --sbom=true \
  --build-arg SOURCE_DATE_EPOCH=1791345462 \
  --label org.opencontainers.image.revision=c3ddbbb9c298d07b8b46a362996251417282476a \
  --label org.opencontainers.image.version=c28.1-ar012-20261007 \
  --tag trading-ai-c281-release:ar012-20261007 \
  --metadata-file docs/acquisition-readiness/baselines/AR-01.2/build-metadata.json \
  --output type=oci,dest=/private/tmp/trading-ai-ar012-release.oci.tar,rewrite-timestamp=true \
  /private/tmp/trading-ai-ar012-context
```

Retain the authoritative archive before rebuilding. Moving OS repositories mean
future rebuilds need not produce the same digest. Any rebuilt payload needs its
own manifest, blob/source verification and review; a tag is never release identity.

Offline package checks:

```sh
python3 docs/acquisition-readiness/baselines/AR-01.2/verify_package.py
shasum -a 256 -c docs/acquisition-readiness/baselines/AR-01.2/SHA256SUMS
```

The smoke script is intended for a network-disabled ephemeral container with
the proof directory mounted read-only at `/proof`, no production mounts and
test storage at `/fixture`. Its retained final log records 17 passing tests.
