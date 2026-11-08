"""Promote accepted Screener document-link metadata into D01 artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from calendar import monthrange
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from ..db.repo import utc_now


PROMOTER_VERSION = "1.1.0"
PROMOTABLE = {
    "concall_transcript": "transcript",
    "concall_ppt": "presentation",
    "concall_recording": "recording",
    "credit_rating": "credit_rating",
}


def _exact_event_date(value: str | None) -> str | None:
    """Parse only a source label that carries an explicit day, month, and year."""

    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def _result_period_end(value: str | None) -> str | None:
    """Convert the accepted ``YYYY-MM`` result period to its exact period end."""

    if not value or value in {"not_applicable", "1970-01"}:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise ValueError(f"Invalid resolved result period: {value!r}") from exc
    return f"{parsed.year:04d}-{parsed.month:02d}-{monthrange(parsed.year, parsed.month)[1]:02d}"


def call_matches_result_window(label: str | None, period_end: str | None) -> bool:
    if not label or not period_end:
        return False
    try:
        call = datetime.strptime(label.strip(), "%b %Y")
        quarter = datetime.strptime(period_end, "%Y-%m-%d")
    except ValueError:
        return False
    gap = (call.year - quarter.year) * 12 + call.month - quarter.month
    return 1 <= gap <= 3


def url_fiscal_year_conflicts(url: str | None, period_end: str | None) -> bool:
    """Reject a URL explicitly filed under a different financial year's Q1–Q4."""
    if not url or not period_end:
        return False
    try:
        quarter = datetime.strptime(period_end, "%Y-%m-%d")
    except ValueError:
        return False
    match = re.search(r"(?<!\d)(20\d{2})-(\d{2})/q[1-4](?:/|$)",
                      unquote(urlsplit(url).path), re.I)
    if not match:
        return False
    start_year = quarter.year - 1 if quarter.month == 3 else quarter.year
    return (int(match[1]), int(match[2])) != (start_year, (start_year + 1) % 100)


def _normalized_http_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    if parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))


def _accepted_links(
    conn: sqlite3.Connection,
    *,
    document_type: str,
    parse_id: int | None,
    limit: int | None,
    verified_company_id: int | None = None,
) -> list[sqlite3.Row]:
    if verified_company_id is not None:
        if parse_id is None:
            raise ValueError("Document-only promotion requires an exact parse ID")
        sql = """SELECT link.*, ? AS company_id, NULL AS accepted_at_utc,
                        parse.blob_id, parse.parser_name AS source_parser_name,
                        parse.parser_version AS source_parser_version,
                        COALESCE(fetch.final_url,fetch.requested_url) AS discovery_page_url
                   FROM parse_artifacts parse JOIN document_links link USING (parse_id)
                   JOIN fetch_observations fetch ON fetch.blob_id=parse.blob_id
                    AND fetch.company_id=? AND fetch.source='screener'
                    AND fetch.resource_kind='company_page'
                  WHERE parse.parse_id=? AND parse.parse_status='ok'
                    AND link.document_type=?
                  ORDER BY fetch.fetch_id DESC,link.document_link_id"""
        rows = list(conn.execute(sql,(verified_company_id,verified_company_id,parse_id,document_type)))
        # The same immutable blob may have several fetch events. Preserve each
        # document link once, using the most recent verified fetch URL.
        rows = sorted({row["document_link_id"]: row for row in reversed(rows)}.values(),
                      key=lambda row: row["document_link_id"])
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            rows = rows[:limit]
        return rows
    sql = """
        SELECT link.*, accepted.company_id, accepted.accepted_at_utc,
               parse.blob_id, parse.parser_name AS source_parser_name,
               parse.parser_version AS source_parser_version,
               COALESCE(
                 (SELECT fetch.final_url
                    FROM fetch_observations AS fetch
                   WHERE fetch.company_id=accepted.company_id
                     AND fetch.source='screener'
                     AND fetch.blob_id=parse.blob_id
                   ORDER BY fetch.fetch_id DESC LIMIT 1),
                 (SELECT fetch.requested_url
                    FROM fetch_observations AS fetch
                   WHERE fetch.company_id=accepted.company_id
                     AND fetch.source='screener'
                     AND fetch.blob_id=parse.blob_id
                   ORDER BY fetch.fetch_id DESC LIMIT 1)
               ) AS discovery_page_url
          FROM accepted_page_versions AS accepted
          JOIN parse_artifacts AS parse ON parse.parse_id=accepted.parse_id
          JOIN document_links AS link ON link.parse_id=accepted.parse_id
         WHERE accepted.source='screener' AND link.document_type=?
    """
    parameters: list[Any] = [document_type]
    if parse_id is not None:
        sql += " AND accepted.parse_id=?"
        parameters.append(parse_id)
    sql += " ORDER BY link.document_link_id"
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        sql += " LIMIT ?"
        parameters.append(limit)
    return list(conn.execute(sql, parameters).fetchall())


def _record_promotion(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    artifact_id: int | None,
    artifact_source_id: int | None,
    status: str,
    reason: str,
    normalized_url: str | None,
    artifact_type: str | None,
    promoted_at_utc: str,
) -> None:
    conn.execute(
        """INSERT INTO document_link_promotions
           (document_link_id, artifact_id, artifact_source_id, promotion_status,
            promotion_reason, normalized_url, artifact_type, promoter_version,
            promoted_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(document_link_id) DO UPDATE SET
             artifact_id=excluded.artifact_id,
             artifact_source_id=excluded.artifact_source_id,
             promotion_status=excluded.promotion_status,
             promotion_reason=excluded.promotion_reason,
             normalized_url=excluded.normalized_url,
             artifact_type=excluded.artifact_type,
             promoter_version=excluded.promoter_version,
             promoted_at_utc=excluded.promoted_at_utc""",
        (
            int(row["document_link_id"]), artifact_id, artifact_source_id,
            status, reason, normalized_url, artifact_type, PROMOTER_VERSION,
            promoted_at_utc,
        ),
    )


def promote_document_links(
    conn: sqlite3.Connection,
    *,
    document_type: str = "credit_rating",
    dry_run: bool = False,
    limit: int | None = None,
    parse_id: int | None = None,
    promoted_at_utc: str | None = None,
    resolved_result_period: str | None = None,
    verified_company_id: int | None = None,
) -> dict[str, Any]:
    """Promote accepted links or document-only links from a verified fetch."""

    if document_type not in PROMOTABLE:
        raise ValueError(f"Unsupported promotable document type: {document_type}")
    artifact_type = PROMOTABLE[document_type]
    promoted_at_utc = promoted_at_utc or utc_now()
    rows = _accepted_links(
        conn, document_type=document_type, parse_id=parse_id, limit=limit,
        verified_company_id=verified_company_id,
    )
    # Screener groups each call's links under a month label and renders the
    # newest call first.  When this parse was collected for a known result
    # period, bind only that first group to the result event.  Older links on
    # the same page must retain their historical identity.
    result_period_end = _result_period_end(resolved_result_period)
    current_group_label = rows[0]["period_label_raw"] if rows else None
    counts: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    artifact_ids: set[int] = set()
    if dry_run:
        for row in rows:
            normalized = _normalized_http_url(row["source_url"])
            if row["availability_status"] != "available" or not row["source_url"]:
                counts["SKIPPED_UNAVAILABLE_SLOT"] += 1
            elif normalized is None:
                counts["QUARANTINED_INVALID_URL"] += 1
            elif not row["discovery_page_url"]:
                counts["QUARANTINED_IDENTITY_AMBIGUOUS"] += 1
            else:
                counts["PROMOTABLE"] += 1
                hosts[urlsplit(normalized).hostname or ""] += 1
        return {
            "dry_run": True,
            "document_type": document_type,
            "artifact_type": artifact_type,
            "parse_id": parse_id,
            "input_count": len(rows),
            "status_counts": dict(sorted(counts.items())),
            "host_counts": dict(sorted(hosts.items(), key=lambda item: (-item[1], item[0]))),
        }

    with conn:
        for row in rows:
            normalized = _normalized_http_url(row["source_url"])
            event_date = _exact_event_date(row["period_label_raw"])
            period_end = (
                result_period_end
                if document_type.startswith("concall_")
                and row["period_label_raw"] == current_group_label
                and call_matches_result_window(row["period_label_raw"], result_period_end)
                and not url_fiscal_year_conflicts(normalized, result_period_end)
                else None
            )
            if row["availability_status"] != "available" or not row["source_url"]:
                status, reason = "SKIPPED_UNAVAILABLE_SLOT", "source_link_unavailable"
                _record_promotion(
                    conn, row=row, artifact_id=None, artifact_source_id=None,
                    status=status, reason=reason, normalized_url=normalized,
                    artifact_type=artifact_type, promoted_at_utc=promoted_at_utc,
                )
                counts[status] += 1
                continue
            if normalized is None:
                status, reason = "QUARANTINED_INVALID_URL", "non_http_or_invalid_url"
                _record_promotion(
                    conn, row=row, artifact_id=None, artifact_source_id=None,
                    status=status, reason=reason, normalized_url=None,
                    artifact_type=artifact_type, promoted_at_utc=promoted_at_utc,
                )
                counts[status] += 1
                continue
            discovery_url = row["discovery_page_url"]
            if not discovery_url:
                status, reason = "QUARANTINED_IDENTITY_AMBIGUOUS", "accepted_blob_has_no_fetch_url"
                _record_promotion(
                    conn, row=row, artifact_id=None, artifact_source_id=None,
                    status=status, reason=reason, normalized_url=normalized,
                    artifact_type=artifact_type, promoted_at_utc=promoted_at_utc,
                )
                counts[status] += 1
                continue

            conn.execute(
                """INSERT OR IGNORE INTO artifact_discovery_pages
                   (company_id, discovered_from_source, discovery_page_url,
                    publisher_claim, blob_id, parser_name, parser_version,
                    first_observed_at_utc)
                   VALUES (?, 'screener', ?, NULL, ?, ?, ?, ?)""",
                (
                    int(row["company_id"]), discovery_url, int(row["blob_id"]),
                    "screener-document-link-promotion", PROMOTER_VERSION,
                    row["first_observed_at_utc"],
                ),
            )
            page = conn.execute(
                """SELECT discovery_page_id FROM artifact_discovery_pages
                   WHERE company_id=? AND discovered_from_source='screener'
                     AND discovery_page_url=? AND blob_id=?
                     AND parser_name='screener-document-link-promotion'
                     AND parser_version=?""",
                (int(row["company_id"]), discovery_url, int(row["blob_id"]), PROMOTER_VERSION),
            ).fetchone()
            if page is None:
                raise RuntimeError("Failed to persist accepted Screener discovery page")

            artifact_key = "url:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            existing = conn.execute(
                """SELECT artifact.artifact_id
                     FROM document_artifacts AS artifact
                     LEFT JOIN artifact_sources AS source USING (artifact_id)
                    WHERE artifact.company_id=? AND artifact.artifact_type=?
                      AND (artifact.artifact_key=? OR source.document_url=?)
                    ORDER BY artifact.artifact_id LIMIT 1""",
                (int(row["company_id"]), artifact_type, artifact_key, normalized),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id, artifact_key, artifact_type, publisher,
                        title_raw, event_date, period_end, published_at_utc, first_observed_at_utc,
                        acquisition_status, transcript_preference_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', 'not_applicable')""",
                    (
                        int(row["company_id"]), artifact_key, artifact_type,
                        row["publisher"], row["title_raw"], event_date, period_end,
                        row["source_published_at_utc"], row["first_observed_at_utc"],
                    ),
                )
                artifact_id = int(cursor.lastrowid)
                counts["created_artifact"] += 1
            else:
                artifact_id = int(existing["artifact_id"])
                counts["reused_artifact"] += 1
                current_event_date = conn.execute(
                    "SELECT event_date, period_end FROM document_artifacts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                if event_date and current_event_date["event_date"] is None:
                    conn.execute(
                        "UPDATE document_artifacts SET event_date=? WHERE artifact_id=? AND event_date IS NULL",
                        (event_date, artifact_id),
                    )
                    counts["filled_event_date"] += 1
                elif event_date and current_event_date["event_date"] != event_date:
                    counts["event_date_conflict_preserved"] += 1
                if period_end and current_event_date["period_end"] is None:
                    conn.execute(
                        "UPDATE document_artifacts SET period_end=? WHERE artifact_id=? AND period_end IS NULL",
                        (period_end, artifact_id),
                    )
                    counts["filled_period_end"] += 1
                elif period_end and current_event_date["period_end"] != period_end:
                    counts["period_end_conflict_preserved"] += 1

            evidence = json.dumps(
                {
                    "document_link_id": int(row["document_link_id"]),
                    "original_document_type": row["document_type"],
                    "period_label_raw": row["period_label_raw"],
                    "event_date": event_date,
                    "period_end": period_end,
                    "event_date_basis": (
                        "document_links.period_label_raw_exact_date"
                        if event_date else None
                    ),
                    "reporting_scope": row["reporting_scope"],
                    "publisher_claim": row["publisher"],
                    "target_url_host": urlsplit(normalized).hostname,
                    "promotion_rule": (
                        f"{'verified_document_refresh' if verified_company_id is not None else 'accepted_screener'}_{row['document_type']}_link"
                    ),
                    "promoter_version": PROMOTER_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO artifact_sources
                   (artifact_id, discovery_page_id, discovered_from_source,
                    discovery_page_url, document_url, publisher_claim,
                    source_position, source_title_raw,
                    classification_evidence_json, is_primary_source,
                    first_observed_at_utc, last_checked_at_utc, availability_status)
                   VALUES (?, ?, 'screener', ?, ?, ?, ?, ?, ?, 0, ?, ?, 'available')""",
                (
                    artifact_id, int(page["discovery_page_id"]), discovery_url,
                    normalized, row["publisher"], int(row["position"]),
                    row["title_raw"], evidence, row["first_observed_at_utc"],
                    promoted_at_utc,
                ),
            )
            counts["created_source" if conn.total_changes > before else "reused_source"] += 1
            source = conn.execute(
                """SELECT artifact_source_id FROM artifact_sources
                   WHERE artifact_id=? AND discovered_from_source='screener'
                     AND discovery_page_url=? AND document_url=?""",
                (artifact_id, discovery_url, normalized),
            ).fetchone()
            if source is None:
                raise RuntimeError("Failed to persist promoted artifact source")
            _record_promotion(
                conn, row=row, artifact_id=artifact_id,
                artifact_source_id=int(source["artifact_source_id"]),
                status="PROMOTED", reason=("verified_document_refresh_link" if verified_company_id is not None else "accepted_screener_link"),
                normalized_url=normalized, artifact_type=artifact_type,
                promoted_at_utc=promoted_at_utc,
            )
            counts["PROMOTED"] += 1
            artifact_ids.add(artifact_id)
            hosts[urlsplit(normalized).hostname or ""] += 1

    return {
        "dry_run": False,
        "document_type": document_type,
        "artifact_type": artifact_type,
        "parse_id": parse_id,
        "input_count": len(rows),
        "artifact_ids": sorted(artifact_ids),
        "status_counts": dict(sorted(counts.items())),
        "host_counts": dict(sorted(hosts.items(), key=lambda item: (-item[1], item[0]))),
    }
