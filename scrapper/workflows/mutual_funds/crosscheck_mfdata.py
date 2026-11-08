"""Local saved-response workflow for the optional M09 mfdata cross-check."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ...config import DB_PATH, MFDATA_EXPORTS, MFDATA_MAX_RESPONSE_BYTES, MFDATA_SNAPSHOTS
from ...db.database import connect, init_db
from ...db.mfdata_repo import persist_and_crosscheck_mfdata
from ...db.repo import utc_now
from ...exports.mfdata_crosscheck_json_export import write_mfdata_crosscheck_export
from ...sources.mfdata.adapter import parse_mfdata_holdings


def execute_mfdata_crosscheck(
    *,
    publisher: str,
    scheme_code: str,
    family_id: int,
    disclosure_month: str,
    path: Path,
    db_path: Path = DB_PATH,
    output: Path | None = None,
    snapshot_root: Path = MFDATA_SNAPSHOTS,
    observed_at_utc: str | None = None,
) -> dict:
    """Ingest one saved API response; this function never performs HTTP."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"mfdata response path must be a regular file: {path}")
    size = path.stat().st_size
    if size > MFDATA_MAX_RESPONSE_BYTES:
        raise ValueError(
            f"mfdata response exceeds {MFDATA_MAX_RESPONSE_BYTES} bytes: {size}"
        )
    raw_bytes = path.read_bytes()
    parsed = parse_mfdata_holdings(
        raw_bytes,
        requested_family_id=family_id,
        requested_disclosure_month=disclosure_month,
    )
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    immutable_path = (
        Path(snapshot_root) / publisher / disclosure_month / f"{content_hash}.json"
    )
    immutable_path.parent.mkdir(parents=True, exist_ok=True)
    if immutable_path.exists():
        if hashlib.sha256(immutable_path.read_bytes()).hexdigest() != content_hash:
            raise RuntimeError(f"Immutable mfdata snapshot path has changed: {immutable_path}")
    else:
        shutil.copyfile(path, immutable_path)
    observed_at = observed_at_utc or utc_now()
    conn = connect(db_path)
    try:
        init_db(conn)
        summary = persist_and_crosscheck_mfdata(
            conn,
            parsed=parsed,
            raw_bytes=raw_bytes,
            publisher=publisher,
            scheme_code=scheme_code,
            submitted_path=str(path.resolve()),
            immutable_path=str(immutable_path.resolve()),
            observed_at_utc=observed_at,
        )
        destination = output or (
            MFDATA_EXPORTS / publisher / scheme_code / disclosure_month
            / f"{content_hash}.json"
        )
        written = write_mfdata_crosscheck_export(
            conn, summary.mfdata_crosscheck_run_id, destination
        )
    finally:
        conn.close()
    return {**summary.to_dict(), "export_path": str(written.resolve())}
