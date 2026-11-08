#!/usr/bin/env python3
"""Verify a C05.3 OCI export and optional independent payload reproduction."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "ops/docker/release/c05.3/release-manifest.json"


class OciError(RuntimeError):
    pass


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load_blob(archive: tarfile.TarFile, digest: str) -> bytes:
    algorithm, value = digest.split(":", 1)
    if algorithm != "sha256":
        raise OciError(f"unsupported digest: {digest}")
    member = archive.extractfile(f"blobs/sha256/{value}")
    if member is None:
        raise OciError(f"missing OCI blob: {digest}")
    data = member.read()
    if sha256(data) != digest:
        raise OciError(f"OCI blob checksum mismatch: {digest}")
    return data


def load_json(value: bytes, label: str) -> dict:
    result = json.loads(value)
    if not isinstance(result, dict):
        raise OciError(f"expected JSON object: {label}")
    return result


def inspect(path: Path, expected: dict | None = None) -> dict:
    with tarfile.open(path, "r") as archive:
        index_member = archive.extractfile("index.json")
        if index_member is None:
            raise OciError("missing index.json")
        root = load_json(index_member.read(), "index.json")
        descriptors = root.get("manifests", [])
        if len(descriptors) != 1:
            raise OciError("expected one named OCI index")
        oci_index_digest = descriptors[0]["digest"]
        image_index = load_json(load_blob(archive, oci_index_digest), "image index")
        platform = [
            item for item in image_index.get("manifests", [])
            if item.get("platform") == {"architecture": "arm64", "os": "linux"}
        ]
        attestations = [
            item for item in image_index.get("manifests", [])
            if item.get("annotations", {}).get("vnd.docker.reference.type")
            == "attestation-manifest"
        ]
        if len(platform) != 1 or len(attestations) != 1:
            raise OciError("expected one linux/arm64 image and one attestation manifest")
        platform_digest = platform[0]["digest"]
        image = load_json(load_blob(archive, platform_digest), "platform manifest")
        config_digest = image["config"]["digest"]
        load_blob(archive, config_digest)
        layers = [item["digest"] for item in image.get("layers", [])]
        for digest in layers:
            load_blob(archive, digest)

        attestation_digest = attestations[0]["digest"]
        attestation = load_json(load_blob(archive, attestation_digest), "attestation")
        if attestation.get("subject", {}).get("digest") != platform_digest:
            raise OciError("attestation subject does not match the platform manifest")
        predicates = {
            item.get("annotations", {}).get("in-toto.io/predicate-type")
            for item in attestation.get("layers", [])
        }
        required = {"https://spdx.dev/Document", "https://slsa.dev/provenance/v1"}
        if not required <= predicates:
            raise OciError("OCI export lacks SBOM or provenance attestation")
        for item in attestation.get("layers", []):
            load_blob(archive, item["digest"])

    result = {
        "oci_index_digest": oci_index_digest,
        "platform_manifest_digest": platform_digest,
        "config_digest": config_digest,
        "layers": layers,
        "attestation_manifest_digest": attestation_digest,
        "predicates": sorted(predicates),
    }
    if expected:
        release = expected["release"]
        for key in ("oci_index_digest", "platform_manifest_digest", "config_digest"):
            if result[key] != release[key]:
                raise OciError(f"release manifest mismatch: {key}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    primary = inspect(args.archive, expected)
    result = {"status": "PASS", "authoritative": primary}
    if args.compare:
        reproduced = inspect(args.compare)
        comparable = ("platform_manifest_digest", "config_digest", "layers")
        if any(primary[key] != reproduced[key] for key in comparable):
            raise OciError("independent OCI build payload does not reproduce")
        result["independent"] = reproduced
        result["payload_reproduced"] = True
        result["attestations_independently_generated"] = (
            primary["attestation_manifest_digest"]
            != reproduced["attestation_manifest_digest"]
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OciError, OSError, KeyError, ValueError, tarfile.TarError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        raise SystemExit(1)
