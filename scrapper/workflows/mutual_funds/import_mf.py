"""Local official-AMC workbook planning, import, acceptance, and export."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from ...config import AMC_WORKBOOK_SNAPSHOTS, DB_PATH, MF_IMPORT
from ...db.database import connect, init_db
from ...db.mutual_funds.changes import refresh_changes_for_accepted_month
from ...db.mutual_funds.features import generate_stock_month_features
from ...db.mutual_funds.scheme_acceptance import finalize_ppfas_import
from ...db.mutual_funds.holdings import persist_ppfas_holdings
from ...db.mutual_funds.workbook_inventory import (
    persist_inventory,
    persist_ppfas_structure,
    persist_ppfas_structure_failure,
)
from ...exports.mf_workbook_json_export import write_mf_workbook_export
from ...db.repo import utc_now
from ...sources.amc_workbook.detector import WorkbookInspection, inspect_workbook
from ...sources.amc_workbook.adapters import get_workbook_adapter
from ...sources.amc_workbook.ppfas import (
    PPFASStructureError,
    PPFASWorkbook,
    normalize_ppfas_holdings,
    parse_ppfas_workbook,
)

PUBLISHER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MONTH_RE = re.compile(r"^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$")
IMPORT_PLAN_SCHEMA_VERSION = "1.6.0"


@dataclass(frozen=True)
class ProposedImport:
    publisher: str
    disclosure_month: str
    inspection: WorkbookInspection
    proposed_action: str
    proposed_destination: str
    structure: PPFASWorkbook | None = None
    structure_error: PPFASStructureError | None = None

    def to_dict(self) -> dict:
        payload = {
            "publisher": self.publisher,
            "requested_disclosure_month": self.disclosure_month,
            **self.inspection.to_dict(),
            "proposed_action": self.proposed_action,
            "proposed_destination": self.proposed_destination,
        }
        if self.structure is not None:
            payload["structure_inventory"] = self.structure.inventory_dict()
            adapter = get_workbook_adapter(self.publisher)
            if adapter is None:
                raise RuntimeError(f"Missing adapter for parsed publisher {self.publisher}")
            holdings = adapter.normalize(self.structure)
            payload["normalization_inventory"] = {
                "holding_count": len(holdings),
                "primary_holding_count": sum(
                    item.source_table_kind == "primary_portfolio" for item in holdings
                ),
                "derivative_holding_count": sum(
                    item.source_table_kind == "derivatives" for item in holdings
                ),
                "valid_isin_count": sum(
                    item.isin_validation_status == "valid" for item in holdings
                ),
                "missing_isin_count": sum(
                    item.isin_validation_status == "missing" for item in holdings
                ),
                "invalid_isin_count": sum(
                    item.isin_validation_status.startswith("invalid") for item in holdings
                ),
            }
        if self.structure_error is not None:
            payload["structure_error"] = {
                "code": self.structure_error.code,
                "message": str(self.structure_error),
                "details": self.structure_error.details,
            }
        return payload


def normalize_publisher(value: str) -> str:
    publisher = value.strip().lower()
    if not PUBLISHER_RE.fullmatch(publisher):
        raise ValueError("Publisher must be a lowercase-safe identifier")
    return publisher


def validate_month(value: str) -> str:
    month = value.strip()
    if not MONTH_RE.fullmatch(month):
        raise ValueError("Disclosure month must use YYYY-MM")
    return month


def _candidate_files(path: Path, *, recursive: bool) -> list[Path]:
    path = Path(path)
    if path.is_file() or path.is_symlink():
        return [path]
    if not path.is_dir():
        raise ValueError(f"Import path does not exist or is not a directory: {path}")
    iterator = path.rglob("*") if recursive else path.iterdir()
    candidates = sorted(
        (
            candidate for candidate in iterator
            if (candidate.is_file() or candidate.is_symlink())
            and candidate.suffix.lower() in {".xls", ".xlsx"}
            and not candidate.name.startswith(".")
        ),
        key=lambda candidate: candidate.as_posix(),
    )
    if not candidates:
        raise ValueError(f"No .xls or .xlsx workbook files found under: {path}")
    return candidates


def _destination(
    inspection: WorkbookInspection,
    publisher: str,
    month: str,
    *,
    snapshot_root: Path,
    import_root: Path,
) -> Path:
    extension = inspection.extension if inspection.extension in {".xls", ".xlsx"} else ".bin"
    root = (
        snapshot_root
        if inspection.proposed_state == "inbox"
        else import_root / "quarantine"
    )
    return root / publisher / month / f"{inspection.content_sha256}{extension}"


def _known_inventory(
    db_path: Path,
) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[str, str]]:
    db_path = Path(db_path)
    if not db_path.is_file():
        return {}, {}
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT mif.publisher, mif.content_sha256, mif.import_state,
                      mwb.local_path
               FROM mf_import_files AS mif
               JOIN mf_workbook_blobs AS mwb
                 ON mwb.workbook_blob_id=mif.workbook_blob_id"""
        ).fetchall()
        blobs = conn.execute(
            "SELECT content_sha256, local_path FROM mf_workbook_blobs"
        ).fetchall()
    except sqlite3.Error:
        return {}, {}
    finally:
        if "conn" in locals():
            conn.close()
    return (
        {
            (str(row["publisher"]), str(row["content_sha256"])): (
                str(row["import_state"]), str(row["local_path"])
            )
            for row in rows
        },
        {str(row["content_sha256"]): str(row["local_path"]) for row in blobs},
    )


def plan_import(
    *,
    publisher: str,
    disclosure_month: str,
    path: Path,
    db_path: Path = DB_PATH,
    snapshot_root: Path = AMC_WORKBOOK_SNAPSHOTS,
    import_root: Path = MF_IMPORT,
) -> list[ProposedImport]:
    """Inspect exact file bytes without creating directories or opening SQLite read-write."""
    publisher = normalize_publisher(publisher)
    disclosure_month = validate_month(disclosure_month)
    known, known_blobs = _known_inventory(db_path)
    seen: set[tuple[str, str]] = set()
    proposals: list[ProposedImport] = []
    for candidate in _candidate_files(path, recursive=False):
        adapter = get_workbook_adapter(publisher)
        inspection = inspect_workbook(
            candidate,
            allow_ooxml_xls_extension=(
                adapter.allow_ooxml_xls_extension if adapter is not None else False
            ),
            allow_legacy_xls_parse=(
                adapter.allow_legacy_xls_parse if adapter is not None else False
            ),
        )
        structure = None
        structure_error = None
        if adapter is not None and inspection.proposed_state == "inbox":
            try:
                structure = adapter.parse(
                    candidate,
                    requested_publisher=publisher,
                    requested_disclosure_month=disclosure_month,
                )
            except PPFASStructureError as exc:
                structure_error = exc
                inspection = replace(
                    inspection,
                    proposed_state="quarantine",
                    decision_code=exc.code,
                    reason=str(exc),
                    inventory_status="structural_validation_failed",
                    sheets=(),
                )
        elif (
            publisher != "ppfas"
            and inspection.decision_code == "extension_signature_mismatch"
            and inspection.workbook_type == "xlsx"
            and candidate.suffix.lower() == ".xls"
        ):
            candidate_inspection = inspect_workbook(
                candidate, allow_ooxml_xls_extension=True
            )
            try:
                parse_ppfas_workbook(
                    candidate,
                    requested_publisher=publisher,
                    requested_disclosure_month=disclosure_month,
                )
            except PPFASStructureError as exc:
                if exc.code == "publisher_mismatch":
                    structure_error = exc
                    inspection = replace(
                        candidate_inspection,
                        proposed_state="quarantine",
                        decision_code=exc.code,
                        reason=str(exc),
                        inventory_status="structural_validation_failed",
                        sheets=(),
                    )
        key = (publisher, inspection.content_sha256)
        duplicate = key in known or key in seen
        if inspection.proposed_state == "quarantine":
            action = "quarantine"
        elif duplicate:
            action = "duplicate_observation"
        else:
            action = "inventory"
        destination = _destination(
            inspection, publisher, disclosure_month,
            snapshot_root=Path(snapshot_root), import_root=Path(import_root),
        )
        if inspection.content_sha256 in known_blobs:
            destination = Path(known_blobs[inspection.content_sha256])
        proposals.append(
            ProposedImport(
                publisher, disclosure_month, inspection, action,
                str(destination.resolve()),
                structure, structure_error,
            )
        )
        seen.add(key)
    return proposals


def plan_directory_import(
    *,
    path: Path,
    db_path: Path = DB_PATH,
    snapshot_root: Path = AMC_WORKBOOK_SNAPSHOTS,
    import_root: Path = MF_IMPORT,
) -> list[ProposedImport]:
    """Infer publisher/month from <root>/<publisher>/<YYYY-MM>/ and inspect read-only."""
    root = Path(path)
    proposals: list[ProposedImport] = []
    grouped: dict[tuple[str, str], list[Path]] = {}
    for candidate in _candidate_files(root, recursive=True):
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Workbook is outside directory import root: {candidate}") from exc
        if len(relative.parts) < 3:
            raise ValueError(
                "Directory imports require <publisher>/<YYYY-MM>/<workbook> layout: "
                f"{relative}"
            )
        publisher = normalize_publisher(relative.parts[0])
        month = validate_month(relative.parts[1])
        grouped.setdefault((publisher, month), []).append(candidate)
    for publisher, month in sorted(grouped):
        for candidate in sorted(grouped[(publisher, month)], key=lambda item: item.as_posix()):
            proposals.extend(
                plan_import(
                    publisher=publisher, disclosure_month=month, path=candidate,
                    db_path=db_path, snapshot_root=snapshot_root, import_root=import_root,
                )
            )
    # Duplicate decisions must also be deterministic within this directory plan.
    seen: set[tuple[str, str]] = set()
    seen_blob_destinations: dict[str, str] = {}
    fixed: list[ProposedImport] = []
    for proposal in proposals:
        key = (proposal.publisher, proposal.inspection.content_sha256)
        action = (
            proposal.proposed_action
            if proposal.proposed_action == "quarantine"
            else "duplicate_observation" if key in seen else proposal.proposed_action
        )
        destination = seen_blob_destinations.setdefault(
            proposal.inspection.content_sha256, proposal.proposed_destination
        )
        fixed.append(
            ProposedImport(
                proposal.publisher, proposal.disclosure_month, proposal.inspection,
                action, destination, proposal.structure, proposal.structure_error,
            )
        )
        seen.add(key)
    return fixed


def import_plan_payload(proposals: list[ProposedImport], *, dry_run: bool) -> dict:
    counts = {"inventory": 0, "duplicate_observation": 0, "quarantine": 0}
    for proposal in proposals:
        counts[proposal.proposed_action] += 1
    return {
        "schema_version": IMPORT_PLAN_SCHEMA_VERSION,
        "dry_run": dry_run,
        "file_count": len(proposals),
        "decision_counts": counts,
        "files": [proposal.to_dict() for proposal in proposals],
    }


def _copy_immutable(source: Path, destination: Path, expected_sha256: str) -> None:
    def content_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if content_hash(destination) != expected_sha256:
            raise RuntimeError(f"Existing immutable destination has wrong bytes: {destination}")
        return
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as input_file, temporary.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        if content_hash(temporary) != expected_sha256:
            raise RuntimeError("Source workbook changed while it was copied")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def execute_import(
    proposals: list[ProposedImport],
    *,
    db_path: Path = DB_PATH,
    observed_at_utc: str | None = None,
    export_root: Path | None = None,
) -> dict:
    """Copy bytes, resume parsing, finalize supported imports, and export accepted data."""
    observed_at_utc = observed_at_utc or utc_now()
    conn = connect(db_path)
    results: list[dict] = []
    try:
        init_db(conn)
        for proposal in proposals:
            inspection = proposal.inspection
            # Reinspect immediately before copy to prevent path replacement races.
            current = inspect_workbook(
                Path(inspection.path),
                allow_ooxml_xls_extension=(
                    get_workbook_adapter(proposal.publisher).allow_ooxml_xls_extension
                    if get_workbook_adapter(proposal.publisher) is not None else False
                ),
                allow_legacy_xls_parse=(
                    get_workbook_adapter(proposal.publisher).allow_legacy_xls_parse
                    if get_workbook_adapter(proposal.publisher) is not None else False
                ),
            )
            if current.content_sha256 != inspection.content_sha256:
                raise RuntimeError(f"Workbook changed after planning: {inspection.path}")
            existing_blob = conn.execute(
                """SELECT local_path FROM mf_workbook_blobs
                   WHERE content_sha256=?""",
                (inspection.content_sha256,),
            ).fetchone()
            destination = (
                Path(str(existing_blob["local_path"]))
                if existing_blob is not None
                else Path(proposal.proposed_destination)
            )
            if existing_blob is None:
                _copy_immutable(Path(inspection.path), destination, inspection.content_sha256)
            elif not destination.is_file():
                raise RuntimeError(f"Immutable workbook blob is missing: {destination}")
            persisted = persist_inventory(
                conn, inspection, publisher=proposal.publisher,
                disclosure_month=proposal.disclosure_month,
                immutable_path=str(destination.resolve()), observed_at_utc=observed_at_utc,
            )
            if (
                proposal.structure is not None
                and persisted.import_state in {"inbox", "rejected"}
            ):
                persist_ppfas_structure(
                    conn,
                    import_file_id=persisted.import_file_id,
                    parsed=proposal.structure,
                )
                normalization_summary = persist_ppfas_holdings(
                    conn,
                    import_file_id=persisted.import_file_id,
                    holdings=(
                        get_workbook_adapter(proposal.publisher).normalize(proposal.structure)
                        if get_workbook_adapter(proposal.publisher) is not None
                        else normalize_ppfas_holdings(proposal.structure)
                    ),
                )
                finalization = finalize_ppfas_import(
                    conn,
                    import_file_id=persisted.import_file_id,
                    parsed=proposal.structure,
                    decided_at_utc=observed_at_utc,
                )
                change_refresh = (
                    refresh_changes_for_accepted_month(
                        conn,
                        publisher=proposal.publisher,
                        changed_month=proposal.disclosure_month,
                        generated_at_utc=observed_at_utc,
                    )
                    if finalization.import_state == "accepted"
                    else None
                )
                feature_refresh = (
                    tuple(
                        generate_stock_month_features(
                            conn,
                            publisher=proposal.publisher,
                            current_month=comparison.current_month,
                            generated_at_utc=observed_at_utc,
                        )
                        for comparison in change_refresh.comparisons
                    )
                    if change_refresh is not None else ()
                )
            elif proposal.structure_error is not None and not persisted.duplicate:
                persist_ppfas_structure_failure(
                    conn,
                    import_file_id=persisted.import_file_id,
                    error=proposal.structure_error,
                )
                normalization_summary = None
                finalization = None
                change_refresh = None
                feature_refresh = ()
            else:
                normalization_summary = None
                finalization = None
                change_refresh = None
                feature_refresh = ()
            current_state = str(
                conn.execute(
                    "SELECT import_state FROM mf_import_files WHERE import_file_id=?",
                    (persisted.import_file_id,),
                ).fetchone()["import_state"]
            )
            export_path = None
            if (
                export_root is not None
                and current_state == "accepted"
                and proposal.structure is not None
            ):
                export_path = write_mf_workbook_export(
                    conn,
                    persisted.import_file_id,
                    Path(export_root) / proposal.publisher / proposal.disclosure_month
                    / f"{inspection.content_sha256}.json",
                )
            results.append(
                {
                    **proposal.to_dict(),
                    "proposed_destination": str(destination.resolve()),
                    "import_file_id": persisted.import_file_id,
                    "workbook_blob_id": persisted.workbook_blob_id,
                    "import_observation_id": persisted.import_observation_id,
                    "import_state": current_state,
                    "duplicate": persisted.duplicate,
                    **(
                        {"normalization_summary": normalization_summary.to_dict()}
                        if normalization_summary is not None else {}
                    ),
                    **(
                        {"finalization": finalization.to_dict()}
                        if finalization is not None else {}
                    ),
                    **(
                        {"change_engine": change_refresh.to_dict()}
                        if change_refresh is not None else {}
                    ),
                    **(
                        {
                            "stock_feature_engine": [
                                item.to_dict() for item in feature_refresh
                            ]
                        }
                        if feature_refresh else {}
                    ),
                    **(
                        {"export_path": str(export_path.resolve())}
                        if export_path is not None else {}
                    ),
                }
            )
    finally:
        conn.close()
    state_counts = {
        state: sum(item["import_state"] == state for item in results)
        for state in ("inbox", "accepted", "rejected", "quarantine")
    }
    return {
        "schema_version": IMPORT_PLAN_SCHEMA_VERSION,
        "dry_run": False,
        "file_count": len(results),
        "state_counts": state_counts,
        "files": results,
    }
