#!/usr/bin/env python3
"""Check frozen migration identities without importing application code."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs/cleanup/baselines/C04.1/migration-manifest.json"
MIGRATIONS_DIR = ROOT / "scrapper/db/migrations"


class AuditError(Exception):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_archived_identity(item: dict[str, Any]) -> str:
    archive_path = ROOT / item["archive"]
    member = item["member"]
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            matches = [entry for entry in archive.getmembers() if entry.name == member]
            if len(matches) != 1 or not matches[0].isfile():
                raise AuditError(f"expected one regular archive member: {member}")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise AuditError(f"cannot read archive member: {member}")
            return sha256_bytes(stream.read())
    except (OSError, tarfile.TarError, KeyError) as exc:
        raise AuditError(f"cannot inspect archived identity {archive_path}: {exc}") from exc


def _prefix(filename: str) -> str:
    match = re.match(r"^(\d+)_", filename)
    if not match:
        raise AuditError(f"migration has no numeric prefix followed by underscore: {filename}")
    return match.group(1)


def inspect_database(
    db_path: Path,
    available_names: set[str],
    historical_names: set[str],
    expected_applied: list[str] | None = None,
    expected_pending: list[str] | None = None,
) -> dict[str, Any]:
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "schema_migrations" not in tables:
            raise AuditError("copied database has no schema_migrations table")
        applied_rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        applied_list = [row[0] for row in applied_rows]
        conn.close()
    except (OSError, sqlite3.Error) as exc:
        raise AuditError(f"cannot read migration ledger from {db_path}: {exc}") from exc

    if len(applied_list) != len(set(applied_list)):
        raise AuditError("schema_migrations contains duplicate version identities")
    applied = set(applied_list)
    unknown = sorted(applied - available_names - historical_names)
    pending = sorted(available_names - applied)
    if unknown:
        raise AuditError(f"applied ledger identities have no source or archived disposition: {unknown}")
    if expected_applied is not None and applied_list != sorted(expected_applied):
        raise AuditError("applied ledger filenames differ from the selected expected profile")
    if expected_pending is not None and pending != sorted(expected_pending):
        raise AuditError("pending migration filenames differ from the selected expected profile")
    return {
        "applied_count": len(applied_list),
        "applied_filenames": applied_list,
        "pending_count": len(pending),
        "pending_filenames": pending,
        "unknown_applied_filenames": unknown,
    }


def audit(manifest_path: Path, db_path: Path | None = None, profile: str | None = None) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read manifest {manifest_path}: {exc}") from exc

    entries = manifest.get("migrations")
    if not isinstance(entries, list) or not entries:
        raise AuditError("manifest migrations must be a non-empty list")
    filenames = [entry.get("filename") for entry in entries]
    if len(filenames) != len(set(filenames)):
        raise AuditError("manifest contains duplicate full-filename identities")
    if filenames != sorted(filenames):
        raise AuditError("manifest migrations are not in actual lexical filename order")

    expected_names = set(filenames)
    actual_paths = sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda path: path.name)
    actual_names = {path.name for path in actual_paths}
    missing = sorted(expected_names - actual_names)
    added = sorted(actual_names - expected_names)
    if missing or added:
        raise AuditError(f"canonical migration inventory drift; missing={missing}, added={added}")

    for entry in entries:
        filename = entry["filename"]
        expected_hash = entry.get("sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
            raise AuditError(f"invalid SHA256 in manifest for {filename}")
        actual_hash = sha256_file(MIGRATIONS_DIR / filename)
        if actual_hash != expected_hash:
            raise AuditError(f"released migration checksum changed: {filename}")
        _prefix(filename)

    historical = manifest.get("historical_identities", [])
    historical_names: set[str] = set()
    for item in historical:
        filename = item["filename"]
        if filename in expected_names or filename in historical_names:
            raise AuditError(f"duplicate full-filename identity in historical manifest: {filename}")
        historical_names.add(filename)
        if _read_archived_identity(item) != item.get("sha256"):
            raise AuditError(f"archived historical migration checksum changed: {filename}")

    identities_by_prefix: dict[str, set[str]] = {}
    for filename in expected_names | historical_names:
        identities_by_prefix.setdefault(_prefix(filename), set()).add(filename)
    actual_collisions = {
        prefix: sorted(names)
        for prefix, names in identities_by_prefix.items()
        if len(names) > 1
    }
    allowed_collisions = manifest.get("allowlisted_prefix_collisions", {})
    normalized_allowed = {
        prefix: sorted(names) for prefix, names in allowed_collisions.items()
    }
    if actual_collisions != normalized_allowed:
        raise AuditError(
            f"migration prefix collisions differ from narrow allowlist; "
            f"actual={actual_collisions}, allowed={normalized_allowed}"
        )

    result: dict[str, Any] = {
        "manifest": str(manifest_path.relative_to(ROOT)),
        "canonical_migration_count": len(entries),
        "canonical_filenames": filenames,
        "historical_identity_count": len(historical),
        "historical_filenames": sorted(historical_names),
        "allowlisted_prefix_collisions": actual_collisions,
    }

    if profile is not None:
        profiles = manifest.get("ledger_profiles", {})
        if profile not in profiles:
            raise AuditError(f"unknown ledger profile: {profile}")
        if db_path is None:
            raise AuditError("--profile requires --db")
        selected = profiles[profile]
        result["database"] = inspect_database(
            db_path,
            expected_names,
            historical_names,
            selected.get("expected_applied"),
            selected.get("expected_pending"),
        )
    elif db_path is not None:
        result["database"] = inspect_database(db_path, expected_names, historical_names)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--db", type=Path, help="Optional copied SQLite schema, opened read-only")
    parser.add_argument("--profile", help="Expected applied/pending filename set in the manifest")
    args = parser.parse_args()
    try:
        result = audit(args.manifest, args.db, args.profile)
    except AuditError as exc:
        print(f"migration audit failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
