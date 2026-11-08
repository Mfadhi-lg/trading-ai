# AR-02.1 patched candidate

Separate immutable candidate containing the exact accepted C28.1 source plus
the retained AR-02.1 IST observation-date patch and test. Source identity is the
base commit **plus patch/projection hashes**, not the base commit alone.

See [release manifest](release-manifest.json),
[preparation evidence](../../../../docs/acquisition-readiness/chunks/AR-02.1-patched-release-20261008.md)
and [activation boundary](ACTIVATION_BOUNDARY.md). The authoritative OCI archive
is retained at `artifacts/acquisition-readiness/AR-02.1-release-20261008/release.oci.tar`.
Verify its recorded SHA256 before copying/loading it. Tags are convenience names;
the manifest's digest reference is the candidate identity.

All 21 services retain individual profiles, `restart:no`, identical resource,
mount and secret references, and zero default services. Runtime identities remain
`authorized:false`. No config/sentinel is installed in a production store.
Independent acceptance of AR-01.2 belongs to its previous digest and does not
accept this new candidate. Independent review of this exact package is pending.

The Python base and hash-enforced dependency lock are unchanged. OS repositories
are not snapshot pinned; the new SBOM and verified export describe this payload.
Do not claim future rebuilds will reproduce its digest.
