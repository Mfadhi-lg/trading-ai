#!/usr/bin/env python3
"""Validate the reproduced C05.3 production and development boundaries."""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "ops/docker/release/c05.3"
EXPECTED_PRODUCTION_SERVICES = 16
TOPOLOGY = ROOT / "docs/cleanup/baselines/C23.5/topology-manifest.json"
if not TOPOLOGY.exists():
    TOPOLOGY = ROOT / "docs/cleanup/baselines/C23.4/topology-manifest.json"
if not TOPOLOGY.exists():
    TOPOLOGY = ROOT / "docs/cleanup/baselines/C23.3/topology-manifest.json"
if not TOPOLOGY.exists():
    TOPOLOGY = ROOT / "docs/cleanup/baselines/C23.2/topology-manifest.json"
if not TOPOLOGY.exists():
    TOPOLOGY = ROOT / "docs/cleanup/baselines/C23.1/topology-manifest.json"
EXPECTED_BROWSER_SERVICES = 4
IDENTITY_TARGET = "/run/trading-ai/runtime-identity.json"
PRODUCTION_DATA_TARGETS = {"/data", "/documents"}


class ContractError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"expected object in {path.relative_to(ROOT)}")
    return value


def compose_config(*files: str) -> dict:
    env = os.environ.copy()
    env.update({
        "TRADING_AI_DOCUMENT_HOST_PATH": "/tmp/c052-production-documents",
        "SCREENER_SESSION_FILE": "/tmp/c052-screener-session",
        "BROWSER_RATING_RUN_ID": "offline-release-audit",
    })
    command = ["docker", "compose", "--profile", "*"]
    for filename in files:
        command.extend(("-f", filename))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(
        command, cwd=ROOT, env=env, check=False, capture_output=True, text=True
    )
    if result.returncode:
        raise ContractError(result.stderr.strip() or "docker compose config failed")
    return json.loads(result.stdout)


def mounts(service: dict) -> list[dict]:
    return service.get("volumes", [])


def config_targets(service: dict) -> set[str]:
    return {item["target"] for item in service.get("configs", [])}


def validate_identities() -> str:
    manifest = load_json(RELEASE / "release-manifest.json")
    release = manifest["release"]
    release_id = release["release_id"]
    image_id = release["platform_manifest_digest"]
    image_reference = release["compose_repository_digest"]
    if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image_reference):
        raise ContractError("production image reference is not digest-qualified")
    operator_manifests = dict(manifest.get("operator_manifests", {}))
    if TOPOLOGY.exists():
        topology = load_json(TOPOLOGY)
        released_hash = hashlib.sha256((RELEASE / "release-manifest.json").read_bytes()).hexdigest()
        if topology.get("base_release_manifest_sha256") != released_hash:
            raise ContractError("manual topology has a different release base")
        expected_inputs = {"compose.yml", "compose.development.yml"}
        if topology.get("chunk") == "C23.5":
            expected_inputs.add("compose.browser-recovery.yml")
        if set(topology.get("operator_manifests", {})) != expected_inputs:
            raise ContractError("topology may override only its reviewed Compose inputs")
        if topology.get("production_services") != 17 or topology.get("manual_service") != "cli":
            raise ContractError("unexpected manual topology contract")
        operator_manifests.update(topology["operator_manifests"])
    for relative, expected in operator_manifests.items():
        actual = "sha256:" + hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ContractError(f"operator manifest hash drifted: {relative}")
    build = manifest["build"]
    for key, relative in (("dockerfile_sha256", build["dockerfile"]),
                          ("runtime_lock_sha256", build["runtime_lock"])):
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != build[key]:
            raise ContractError(f"release build input hash drifted: {relative}")

    identities = {
        "core": load_json(RELEASE / "runtime-identity-core.json"),
        "documents": load_json(RELEASE / "runtime-identity-documents.json"),
    }
    expected_targets = {
        "core": {"db": "/data/scraper.db", "store": "/data", "documents": "/data/snapshots"},
        "documents": {"db": "/data/scraper.db", "store": "/data", "documents": "/documents"},
    }
    for name, identity in identities.items():
        if identity.get("environment") != "production" or identity.get("authorized") is not True:
            raise ContractError(f"{name} identity is not authorized production")
        if identity.get("release_id") != release_id or identity.get("image_id") != image_id:
            raise ContractError(f"{name} identity does not match release manifest")
        if identity.get("targets") != expected_targets[name]:
            raise ContractError(f"{name} identity targets drifted")

    sentinels = {
        "store-sentinel.json": ("/data", "store"),
        "core-documents-sentinel.json": ("/data/snapshots", "documents"),
        "external-documents-sentinel.json": ("/documents", "documents"),
    }
    for filename, (path, role) in sentinels.items():
        sentinel = load_json(RELEASE / filename)
        if sentinel != {
            "environment": "production", "path": path,
            "release_id": release_id, "roles": [role],
        }:
            raise ContractError(f"release sentinel drifted: {filename}")
    return image_reference


def validate_production(config: dict, expected_count: int, image_reference: str) -> None:
    services = config.get("services", {})
    if len(services) != expected_count:
        raise ContractError(f"expected {expected_count} production services, found {len(services)}")
    for name, service in services.items():
        if service.get("image") != image_reference or "build" in service:
            raise ContractError(f"{name} does not use only the immutable release image")
        environment = service.get("environment", {})
        if environment.get("TRADING_AI_ENV") != "production":
            raise ContractError(f"{name} is not production-identified")
        if environment.get("TRADING_AI_RUNTIME_IDENTITY_FILE") != IDENTITY_TARGET:
            raise ContractError(f"{name} lacks the production runtime identity")
        if IDENTITY_TARGET not in config_targets(service):
            raise ContractError(f"{name} does not mount its runtime identity")
        for mount in mounts(service):
            source = str(mount.get("source", ""))
            target = mount.get("target")
            if target == "/app/scrapper" or source.endswith("/scrapper"):
                raise ContractError(f"{name} has a production source mount")


def validate_development(config: dict, expected_count: int) -> None:
    services = config.get("services", {})
    if len(services) != expected_count:
        raise ContractError(f"expected {expected_count} development services, found {len(services)}")
    if set(config.get("volumes", {})) != {"development-data", "development-documents"}:
        raise ContractError("development volume set is not isolated")
    if config.get("configs"):
        raise ContractError("development still mounts production runtime configs")
    for name, service in services.items():
        environment = service.get("environment", {})
        if environment.get("TRADING_AI_ENV") != "development":
            raise ContractError(f"{name} is not development-identified")
        if environment.get("SCRAPER_STORE_PATH") != "/development-data":
            raise ContractError(f"{name} does not use the isolated development store")
        if environment.get("SCRAPER_DOCUMENT_STORE_PATH") != "/development-documents":
            raise ContractError(f"{name} does not use the isolated development documents")
        if service.get("configs"):
            raise ContractError(f"{name} still mounts production runtime configs")
        targets = {item.get("target") for item in mounts(service)}
        if targets & PRODUCTION_DATA_TARGETS:
            raise ContractError(f"{name} still mounts a production data target")
        if not {"/development-data", "/development-documents", "/app/scrapper"} <= targets:
            raise ContractError(f"{name} lacks an isolated data or source mount")


def main() -> int:
    try:
        image_reference = validate_identities()
        expected_count = 17 if TOPOLOGY.exists() else EXPECTED_PRODUCTION_SERVICES
        validate_production(compose_config("compose.yml"), expected_count, image_reference)
        validate_development(
            compose_config("compose.yml", "compose.development.yml"),
            expected_count,
        )
        validate_production(
            compose_config("compose.browser-recovery.yml"),
            EXPECTED_BROWSER_SERVICES,
            image_reference,
        )
        validate_development(
            compose_config(
                "compose.browser-recovery.yml",
                "compose.browser-recovery.development.yml",
            ),
            EXPECTED_BROWSER_SERVICES,
        )
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({
        "status": "PASS",
        "production_services": expected_count,
        "browser_services": EXPECTED_BROWSER_SERVICES,
        "immutable_image": image_reference,
        "development_data_targets": ["/development-data", "/development-documents"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
