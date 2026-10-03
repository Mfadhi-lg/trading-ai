"""Automatic acquisition queueing for documents on accepted Screener pages."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db.document_download_queue_repo import materialize_download_queue
from ..db.repo import utc_now
from .credit_rating_downloads import enqueue_credit_ratings_for_accepted_parse
from .promote_document_links import promote_document_links


# Screener's concall notes/AI summaries are deliberately absent: they are a
# generated secondary surface, not a publisher document. Recordings are
# promoted for metadata/evidence only and are never automatically queued.
CONCALL_DOCUMENT_TYPES = (
    "concall_transcript",
    "concall_ppt",
    "concall_recording",
)
AUTOMATIC_CONCALL_DOCUMENT_TYPES = ("concall_transcript", "concall_ppt")
CONCALL_ARTIFACT_TYPES = ("transcript", "presentation")
AUTOMATIC_CONCALL_SCOPE_ID = "accepted-screener-concall"


def _accepted_company_identity(
    conn: sqlite3.Connection, *, parse_id: int
) -> tuple[int, str]:
    rows = conn.execute(
        """SELECT DISTINCT accepted.company_id
             FROM accepted_page_versions AS accepted
            WHERE accepted.source='screener' AND accepted.parse_id=?""",
        (parse_id,),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"Expected one company for accepted Screener parse {parse_id}; "
            f"found {len(rows)}"
        )
    company_id = int(rows[0]["company_id"])
    identifier = conn.execute(
        """SELECT identifier_value FROM security_identifiers
            WHERE company_id=? AND is_current=1
              AND identifier_type IN ('NSE', 'BSE')
            ORDER BY CASE identifier_type WHEN 'NSE' THEN 0 ELSE 1 END,
                     identifier_value
            LIMIT 1""",
        (company_id,),
    ).fetchone()
    label = str(identifier["identifier_value"]) if identifier else f"company:{company_id}"
    return company_id, label


def _register_automatic_scope(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    company_label: str,
    artifact_ids: list[int],
) -> dict[str, Any]:
    """Increment the one system-owned scope used by continuous acquisition."""

    now = utc_now()
    types_json = json.dumps(CONCALL_ARTIFACT_TYPES, separators=(",", ":"))
    policy_json = json.dumps(
        {"pdf_only": False, "recording_fallback_only": False},
        sort_keys=True,
        separators=(",", ":"),
    )
    existing = conn.execute(
        "SELECT * FROM artifact_download_scopes WHERE scope_id=?",
        (AUTOMATIC_CONCALL_SCOPE_ID,),
    ).fetchone()
    if existing is not None and (
        existing["scope_kind"] != "custom"
        or existing["artifact_types_json"] != types_json
        or existing["policy_json"] != policy_json
    ):
        raise ValueError(
            f"Automatic download scope {AUTOMATIC_CONCALL_SCOPE_ID!r} "
            "has an incompatible definition"
        )
    placeholders = ",".join("?" for _ in artifact_ids)
    owned = conn.execute(
        f"""SELECT COUNT(*) FROM document_artifacts
             WHERE company_id=? AND artifact_id IN ({placeholders})""",
        (company_id, *artifact_ids),
    ).fetchone()[0]
    if int(owned) != len(artifact_ids):
        raise ValueError(
            "Automatic scope artifacts do not all belong to the accepted company"
        )

    before = conn.total_changes
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO artifact_download_scopes
               (scope_id, scope_kind, description, artifact_types_json,
                policy_json, created_at_utc, updated_at_utc)
               VALUES (?, 'custom', ?, ?, ?, ?, ?)""",
            (
                AUTOMATIC_CONCALL_SCOPE_ID,
                "Automatic primary transcript and presentation acquisition from accepted Screener pages",
                types_json,
                policy_json,
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO artifact_download_scope_companies
               (scope_id, company_id, symbol)
               VALUES (?, ?, ?)""",
            (AUTOMATIC_CONCALL_SCOPE_ID, company_id, company_label),
        )
        conn.executemany(
            """INSERT OR IGNORE INTO artifact_download_scope_artifacts
               (scope_id, artifact_id) VALUES (?, ?)""",
            [
                (AUTOMATIC_CONCALL_SCOPE_ID, artifact_id)
                for artifact_id in artifact_ids
            ],
        )
        conn.execute(
            """UPDATE artifact_download_scopes SET updated_at_utc=?
                WHERE scope_id=?""",
            (now, AUTOMATIC_CONCALL_SCOPE_ID),
        )
    return {
        "scope_id": AUTOMATIC_CONCALL_SCOPE_ID,
        "company_id": company_id,
        "artifact_count": len(artifact_ids),
        "created_row_count": conn.total_changes - before,
    }


def enqueue_concall_documents_for_accepted_parse(
    conn: sqlite3.Connection,
    *,
    parse_id: int,
    include_queue_counts: bool = True,
    resolved_result_period: str | None = None,
    verified_company_id: int | None = None,
) -> dict[str, Any]:
    """Promote concall links, queueing only PPT and official transcripts."""

    promotions = {
        document_type: promote_document_links(
            conn,
            document_type=document_type,
            parse_id=parse_id,
            resolved_result_period=resolved_result_period,
            verified_company_id=verified_company_id,
        )
        for document_type in CONCALL_DOCUMENT_TYPES
    }
    artifact_ids = sorted(
        {
            int(artifact_id)
            for document_type, promotion in promotions.items()
            if document_type in AUTOMATIC_CONCALL_DOCUMENT_TYPES
            for artifact_id in promotion["artifact_ids"]
        }
    )
    if not artifact_ids:
        return {
            "parse_id": parse_id,
            "scope_id": None,
            "promotions": promotions,
            "queue": {
                "scope_id": None,
                "total_job_count": 0,
                "status_counts": {},
                "created_job_count": 0,
                "invalid_target_count": 0,
            },
        }

    if verified_company_id is None:
        company_id, company_label = _accepted_company_identity(conn, parse_id=parse_id)
    else:
        company_id = verified_company_id
        identifier = conn.execute("""SELECT identifier_value FROM security_identifiers
             WHERE company_id=? AND is_current=1 AND identifier_type IN ('NSE','BSE')
             ORDER BY CASE identifier_type WHEN 'NSE' THEN 0 ELSE 1 END LIMIT 1""",
             (company_id,)).fetchone()
        company_label = identifier[0] if identifier else f"company:{company_id}"
    scope = _register_automatic_scope(
        conn,
        company_id=company_id,
        company_label=company_label,
        artifact_ids=artifact_ids,
    )
    queue = materialize_download_queue(
        conn,
        scope_id=AUTOMATIC_CONCALL_SCOPE_ID,
        artifact_ids=artifact_ids,
        include_queue_counts=include_queue_counts,
    )
    return {
        "parse_id": parse_id,
        "scope_id": AUTOMATIC_CONCALL_SCOPE_ID,
        "scope": scope,
        "promotions": promotions,
        "queue": queue,
    }


def enqueue_documents_for_accepted_parse(
    conn: sqlite3.Connection,
    *,
    parse_id: int,
    include_queue_counts: bool = True,
    resolved_result_period: str | None = None,
) -> dict[str, Any]:
    """Queue every primary research document exposed by one accepted parse."""

    return {
        "parse_id": parse_id,
        "credit_rating": enqueue_credit_ratings_for_accepted_parse(
            conn,
            parse_id=parse_id,
            include_queue_counts=include_queue_counts,
        ),
        "concall": enqueue_concall_documents_for_accepted_parse(
            conn,
            parse_id=parse_id,
            include_queue_counts=include_queue_counts,
            resolved_result_period=resolved_result_period,
        ),
    }


def enqueue_documents_for_all_accepted_parses(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Idempotently backfill queues for all accepted Screener company parses."""

    parse_ids = tuple(
        int(row["parse_id"])
        for row in conn.execute(
            """SELECT DISTINCT parse_id FROM accepted_page_versions
                WHERE source='screener' ORDER BY parse_id"""
        )
    )
    created_concall = 0
    created_credit_rating = 0
    for parse_id in parse_ids:
        result = enqueue_documents_for_accepted_parse(
            conn,
            parse_id=parse_id,
            include_queue_counts=False,
        )
        created_concall += int(result["concall"]["queue"]["created_job_count"])
        created_credit_rating += int(
            result["credit_rating"]["queue"]["created_job_count"]
        )
    return {
        "accepted_parse_count": len(parse_ids),
        "created_concall_job_count": created_concall,
        "created_credit_rating_job_count": created_credit_rating,
    }
