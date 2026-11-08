#!/usr/bin/env python3
"""Run the C05.3 image against isolated copied-database targets."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


MARKER = ".trading_ai_runtime_identity.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run(command: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, timeout=180)
    if result.returncode != expected:
        raise RuntimeError(
            f"exit {result.returncode}, expected {expected}: {' '.join(command)}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def restricted(image: str, mounts: list[str], environment: dict[str, str], args: list[str]):
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "256", "--memory", "1536m", "--cpus", "1",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
    ]
    for mount in mounts:
        command.extend(("--mount", mount))
    for key, value in environment.items():
        command.extend(("--env", f"{key}={value}"))
    return command + [image, *args]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--restored-db", required=True, type=Path)
    parser.add_argument("--small-clone-db", required=True, type=Path)
    args = parser.parse_args()

    restored_before = digest(args.restored_db)
    with tempfile.TemporaryDirectory(prefix="trading-ai-c053-restored-") as name:
        restored_root = Path(name)
        restored_clone = restored_root / "scraper.db"
        # macOS clonefile gives this large test an isolated copy-on-write file;
        # fail instead of silently making a 13 GB physical copy.
        run(["cp", "-c", str(args.restored_db), str(restored_clone)])
        restored_clone_before = digest(restored_clone)
        read_only = restricted(
            args.image,
            [f"type=bind,src={restored_root.resolve()},dst=/restore"],
            {"SCRAPER_DB_PATH": "/restore/scraper.db"},
            ["view", "tables"],
        )
        read_result = run(read_only)
        restored_clone_after = digest(restored_clone)
    restored_after = digest(args.restored_db)
    if restored_before != restored_after or restored_clone_before != restored_clone_after:
        raise RuntimeError("read-only restored database hash changed")

    with tempfile.TemporaryDirectory(prefix="trading-ai-c053-clone-") as name:
        root = Path(name)
        clone_db = root / "scraper.db"
        shutil.copy2(args.small_clone_db, clone_db)
        (root / "store").mkdir()
        (root / "documents").mkdir()
        marker = root / "store" / MARKER
        marker.write_text(json.dumps({"environment": "production"}), encoding="utf-8")
        clone_identity = root / "clone-identity.json"
        clone_identity.write_text(json.dumps({
            "environment": "test",
            "clone_id": "c05.3-container-clone",
            "root": "/clone",
            "source_marker_sha256": digest(marker),
        }), encoding="utf-8")
        mount = f"type=bind,src={root.resolve()},dst=/clone"
        environment = {
            "TRADING_AI_ENV": "test",
            "SCRAPER_DB_PATH": "/clone/scraper.db",
            "SCRAPER_STORE_PATH": "/clone/store",
            "SCRAPER_DOCUMENT_STORE_PATH": "/clone/documents",
        }
        refused = run(restricted(args.image, [mount], environment, ["init-db"]), expected=1)
        if "unverified production marker" not in refused.stdout + refused.stderr:
            raise RuntimeError("copied production marker did not fail closed")
        before_verified = digest(clone_db)
        environment["TRADING_AI_CLONE_IDENTITY_FILE"] = "/clone/clone-identity.json"
        accepted = run(restricted(args.image, [mount], environment, ["init-db"]), expected=0)
        after_verified = digest(clone_db)

    print(json.dumps({
        "status": "PASS",
        "image": args.image,
        "restored_db_sha256_before": restored_before,
        "restored_db_sha256_after": restored_after,
        "restored_clone_sha256_before": restored_clone_before,
        "restored_clone_sha256_after": restored_clone_after,
        "restored_tables_output_lines": len(read_result.stdout.splitlines()),
        "copied_marker_without_identity": "REFUSED",
        "verified_clone_identity": "ACCEPTED",
        "verified_clone_db_sha256_before": before_verified,
        "verified_clone_db_sha256_after": after_verified,
        "verified_clone_output": accepted.stdout.strip(),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
