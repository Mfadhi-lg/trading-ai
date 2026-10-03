# C05.3 reproduced release candidate

This directory records the independently reproduced, attested OCI release used
by production Compose. It is **not activated**. C06 remains the separate
production-activation boundary.

Two `--no-cache` BuildKit builds used the pinned Dockerfile/base image, hashed
Python lock, Linux ARM64 platform, baseline commit `SOURCE_DATE_EPOCH`, and OCI
`rewrite-timestamp=true`. Their platform manifest, image config, and all layer
digests are byte-identical. SBOM and maximum-mode provenance are attached to
the authoritative OCI index. Attestation manifests themselves vary by design
because provenance records each build's execution times; both attest the same
reproduced platform-manifest subject.

The reproduction gate found and fixed two sources of volatile bytes: pip's
install-time bytecode and apt/font/loader log/cache files. The runtime package
set and capabilities are unchanged.

Production Compose now uses the exact local OCI index reference and the C05.3
runtime identities. Before any separately authorized C06 start, the prepared
`external-documents-sentinel.json` must still be placed at
`/documents/.trading_ai_runtime_identity.json` in the existing external store.

The authoritative OCI archive is an exported release artifact, not committed
to Git. Its exact size and SHA256 are in `release-manifest.json`; retain/export
it through the approved image registry or artifact store before C06.
