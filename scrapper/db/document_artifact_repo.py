"""Persistence for D01 logical document artifacts and discovery provenance."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .repo import utc_now
from .artifact_reconciliation_repo import ensure_discovered_artifact_expectations
from ..config import SNAPSHOTS
from ..sources.documents.discovery import (
    DISCOVERY_PARSER_NAME,
    DISCOVERY_PARSER_VERSION,
    DiscoveredArtifactCandidate,
    DiscoveryPage,
    DiscoveryPlan,
)


@dataclass(frozen=True)
class DiscoveryPersistenceResult:
    company_id: int
    discovery_page_ids: tuple[int, ...]
    artifact_ids: tuple[int, ...]
    artifact_source_ids: tuple[int, ...]
    created_artifact_count: int
    reused_artifact_count: int
    created_source_count: int
    reused_source_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "discovery_page_ids": list(self.discovery_page_ids),
            "artifact_ids": list(self.artifact_ids),
            "artifact_source_ids": list(self.artifact_source_ids),
            "created_artifact_count": self.created_artifact_count,
            "reused_artifact_count": self.reused_artifact_count,
            "created_source_count": self.created_source_count,
            "reused_source_count": self.reused_source_count,
        }


def _ensure_company(
    conn: sqlite3.Connection, *, symbol: str, company_name: str
) -> int:
    row = conn.execute(
        """SELECT company_id FROM security_identifiers
           WHERE identifier_type='NSE' AND identifier_value=? AND is_current=1""",
        (symbol.upper(),),
    ).fetchone()
    if row is not None:
        return int(row["company_id"])
    cursor = conn.execute(
        "INSERT INTO companies(current_legal_name, status) VALUES (?, 'active')",
        (company_name,),
    )
    company_id = int(cursor.lastrowid)
    conn.execute(
        """INSERT INTO security_identifiers
           (company_id, identifier_type, identifier_value, is_current)
           VALUES (?, 'NSE', ?, 1)""",
        (company_id, symbol.upper()),
    )
    return company_id


def _store_discovery_blob(
    conn: sqlite3.Connection,
    *,
    page: DiscoveryPage,
    symbol: str,
    observed_at_utc: str,
    snapshot_root: Path,
) -> int:
    sha256 = hashlib.sha256(page.content).hexdigest()
    if sha256 != page.content_sha256:
        raise ValueError("Discovery page bytes changed after plan construction")
    suffix = page.content_path.suffix.lower()
    if suffix not in {".html", ".htm", ".json"}:
        suffix = ".html"
    destination = (
        snapshot_root
        / "artifact_discovery"
        / symbol.upper()
        / page.source
        / f"{sha256}{suffix}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != page.content:
            raise RuntimeError(f"Immutable discovery snapshot mismatch: {destination}")
    else:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(page.content)
        os.replace(temporary, destination)
    conn.execute(
        """INSERT OR IGNORE INTO response_blobs
           (content_sha256, content_type, encoding, byte_size, local_path,
            first_observed_at_utc, created_at_utc)
           VALUES (?, 'text/html', 'utf-8', ?, ?, ?, ?)""",
        (
            sha256,
            len(page.content),
            str(destination.resolve()),
            observed_at_utc,
            observed_at_utc,
        ),
    )
    row = conn.execute(
        "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (sha256,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to store immutable discovery page")
    return int(row["blob_id"])


def _persist_page(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    page: DiscoveryPage,
    blob_id: int,
    observed_at_utc: str,
) -> int:
    conn.execute(
        """INSERT OR IGNORE INTO artifact_discovery_pages
           (company_id, discovered_from_source, discovery_page_url,
            publisher_claim, blob_id, parser_name, parser_version,
            first_observed_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            page.source,
            page.page_url,
            page.publisher_claim,
            blob_id,
            DISCOVERY_PARSER_NAME,
            DISCOVERY_PARSER_VERSION,
            observed_at_utc,
        ),
    )
    row = conn.execute(
        """SELECT discovery_page_id FROM artifact_discovery_pages
           WHERE company_id=? AND discovered_from_source=?
             AND discovery_page_url=? AND blob_id=?
             AND parser_name=? AND parser_version=?""",
        (
            company_id,
            page.source,
            page.page_url,
            blob_id,
            DISCOVERY_PARSER_NAME,
            DISCOVERY_PARSER_VERSION,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist discovery page provenance")
    return int(row["discovery_page_id"])


def _existing_artifact_for_candidate(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    artifact_type: str,
    artifact_key: str,
    document_url: str,
) -> int | None:
    row = conn.execute(
        """SELECT artifact_id FROM document_artifacts
           WHERE company_id=? AND artifact_type=? AND artifact_key=?""",
        (company_id, artifact_type, artifact_key),
    ).fetchone()
    if row is not None:
        return int(row["artifact_id"])
    # An exact document URL is strong identity evidence even when two source
    # pages phrase the title differently. Never merge across artifact types.
    row = conn.execute(
        """SELECT artifact.artifact_id
           FROM document_artifacts AS artifact
           JOIN artifact_sources AS source
             ON source.artifact_id=artifact.artifact_id
           WHERE artifact.company_id=? AND artifact.artifact_type=?
             AND source.document_url=?
           ORDER BY artifact.artifact_id LIMIT 1""",
        (company_id, artifact_type, document_url),
    ).fetchone()
    return int(row["artifact_id"]) if row is not None else None


def _validate_existing_artifact_identity(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    candidate: DiscoveredArtifactCandidate,
) -> None:
    existing = conn.execute(
        """SELECT artifact_type, event_date, period_end, fiscal_year,
                  fiscal_quarter
           FROM document_artifacts WHERE artifact_id=?""",
        (artifact_id,),
    ).fetchone()
    if existing is None:
        raise RuntimeError(f"Unknown existing artifact: {artifact_id}")
    for field in (
        "artifact_type",
        "event_date",
        "period_end",
        "fiscal_year",
        "fiscal_quarter",
    ):
        old_value = existing[field]
        new_value = getattr(candidate, field)
        if old_value is not None and new_value is not None and old_value != new_value:
            raise ValueError(
                f"Discovery identity conflict for artifact {artifact_id}: "
                f"{field} was {old_value!r}, candidate has {new_value!r}"
            )


def persist_discovery_plan(
    conn: sqlite3.Connection,
    plan: DiscoveryPlan,
    *,
    observed_at_utc: str | None = None,
    snapshot_root: Path = SNAPSHOTS,
) -> DiscoveryPersistenceResult:
    """Persist a plan atomically and without mutating prior discovery evidence."""

    observed_at_utc = observed_at_utc or utc_now()
    created_artifacts = 0
    reused_artifacts = 0
    created_sources = 0
    reused_sources = 0
    page_ids: dict[int, int] = {}
    artifact_ids: list[int] = []
    source_ids: list[int] = []
    with conn:
        company_id = _ensure_company(
            conn, symbol=plan.symbol, company_name=plan.company_name
        )
        for page in plan.pages:
            blob_id = _store_discovery_blob(
                conn,
                page=page,
                symbol=plan.symbol,
                observed_at_utc=observed_at_utc,
                snapshot_root=snapshot_root,
            )
            page_ids[page.page_position] = _persist_page(
                conn,
                company_id=company_id,
                page=page,
                blob_id=blob_id,
                observed_at_utc=observed_at_utc,
            )

        for candidate in plan.candidates:
            artifact_id = _existing_artifact_for_candidate(
                conn,
                company_id=company_id,
                artifact_type=candidate.artifact_type,
                artifact_key=candidate.artifact_key,
                document_url=candidate.document_url,
            )
            if artifact_id is None:
                transcript_status = "not_applicable"
                if candidate.artifact_type == "transcript":
                    transcript_status = "official_candidate"
                elif candidate.artifact_type == "recording":
                    transcript_status = "recording_fallback_candidate"
                cursor = conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id, artifact_key, artifact_type, publisher,
                        title_raw, event_date, period_end, fiscal_year,
                        fiscal_quarter, published_at_utc, first_observed_at_utc,
                        acquisition_status, transcript_preference_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)""",
                    (
                        company_id,
                        candidate.artifact_key,
                        candidate.artifact_type,
                        candidate.publisher_claim if candidate.is_primary_source else None,
                        candidate.title_raw,
                        candidate.event_date,
                        candidate.period_end,
                        candidate.fiscal_year,
                        candidate.fiscal_quarter,
                        candidate.published_at_utc,
                        observed_at_utc,
                        transcript_status,
                    ),
                )
                artifact_id = int(cursor.lastrowid)
                created_artifacts += 1
            else:
                _validate_existing_artifact_identity(
                    conn, artifact_id=artifact_id, candidate=candidate
                )
                reused_artifacts += 1
            artifact_ids.append(artifact_id)

            evidence = json.dumps(
                {
                    "classification_basis": candidate.classification_basis,
                    "identity_basis": candidate.identity_basis,
                    "discovery_page_sha256": candidate.discovery_page_sha256,
                    "parser_name": DISCOVERY_PARSER_NAME,
                    "parser_version": DISCOVERY_PARSER_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO artifact_sources
                   (artifact_id, discovery_page_id, discovered_from_source,
                    discovery_page_url, document_url, publisher_claim,
                    exchange_announcement_id, source_position, source_title_raw,
                    classification_evidence_json, is_primary_source,
                    first_observed_at_utc, last_checked_at_utc,
                    availability_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available')""",
                (
                    artifact_id,
                    page_ids[candidate.page_position],
                    candidate.discovered_from_source,
                    candidate.discovery_page_url,
                    candidate.document_url,
                    candidate.publisher_claim,
                    candidate.exchange_announcement_id,
                    candidate.source_position,
                    candidate.title_raw,
                    evidence,
                    int(candidate.is_primary_source),
                    observed_at_utc,
                    observed_at_utc,
                ),
            )
            if conn.total_changes > before:
                created_sources += 1
            else:
                reused_sources += 1
            row = conn.execute(
                """SELECT artifact_source_id FROM artifact_sources
                   WHERE artifact_id=? AND discovered_from_source=?
                     AND discovery_page_url=? AND document_url=?""",
                (
                    artifact_id,
                    candidate.discovered_from_source,
                    candidate.discovery_page_url,
                    candidate.document_url,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("Failed to persist artifact source")
            source_ids.append(int(row["artifact_source_id"]))
    ensure_discovered_artifact_expectations(
        conn,
        company_id=company_id,
        artifact_ids=artifact_ids,
        observed_at_utc=observed_at_utc,
    )
    return DiscoveryPersistenceResult(
        company_id=company_id,
        discovery_page_ids=tuple(page_ids[position] for position in sorted(page_ids)),
        artifact_ids=tuple(artifact_ids),
        artifact_source_ids=tuple(source_ids),
        created_artifact_count=created_artifacts,
        reused_artifact_count=reused_artifacts,
        created_source_count=created_sources,
        reused_source_count=reused_sources,
    )


def register_artifact_content(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    blob_id: int,
    media_type: str,
    is_official: bool,
    content_origin: str = "publisher_original",
    created_at_utc: str | None = None,
) -> int:
    """Register already-validated immutable bytes (the D02 handoff surface)."""

    blob = conn.execute(
        """SELECT content_sha256, byte_size FROM response_blobs WHERE blob_id=?""",
        (blob_id,),
    ).fetchone()
    if blob is None:
        raise ValueError(f"Unknown response blob: {blob_id}")
    existing = conn.execute(
        "SELECT content_id, artifact_id FROM artifact_contents WHERE content_sha256=?",
        (blob["content_sha256"],),
    ).fetchone()
    if existing is not None:
        if int(existing["artifact_id"]) != artifact_id:
            raise ValueError(
                "Identical content is already attached to a different logical artifact"
            )
        return int(existing["content_id"])
    cursor = conn.execute(
        """INSERT INTO artifact_contents
           (artifact_id, blob_id, media_type, content_sha256, byte_size,
            extraction_status, is_official, content_origin, created_at_utc)
           VALUES (?, ?, ?, ?, ?, 'not_started', ?, ?, ?)""",
        (
            artifact_id,
            blob_id,
            media_type,
            blob["content_sha256"],
            int(blob["byte_size"]),
            int(is_official),
            content_origin,
            created_at_utc or utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def set_preferred_artifact_content(
    conn: sqlite3.Connection, *, artifact_id: int, content_id: int
) -> None:
    """Move only the preferred pointer; immutable content rows are untouched."""

    content = conn.execute(
        """SELECT is_official FROM artifact_contents
           WHERE content_id=? AND artifact_id=?""",
        (content_id, artifact_id),
    ).fetchone()
    if content is None:
        raise ValueError("Preferred content must belong to the logical artifact")
    transcript_status = conn.execute(
        "SELECT artifact_type FROM document_artifacts WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if transcript_status is None:
        raise ValueError(f"Unknown artifact: {artifact_id}")
    preference = "not_applicable"
    if transcript_status["artifact_type"] == "transcript":
        preference = "official_preferred" if content["is_official"] else "machine_preferred"
    with conn:
        conn.execute(
            """UPDATE document_artifacts
               SET preferred_content_id=?, acquisition_status='validated',
                   transcript_preference_status=?
               WHERE artifact_id=?""",
            (content_id, preference, artifact_id),
        )
