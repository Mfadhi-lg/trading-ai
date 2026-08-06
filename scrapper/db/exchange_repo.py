"""Durable exchange universe registry, filing discovery and queue bridge.

Every dated snapshot is preserved. Securities are never deleted: they are
effective-dated, marked absent, and kept queryable. Companies are unioned by
ISIN only; NSE symbols and BSE scrip codes stay in separate columns, and no code
path merges two securities from name text.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..config import SNAPSHOTS
from ..core.http import FetchResponse
from ..sources.exchange.corporate_changes import ParsedCorporateChanges
from ..sources.exchange.result_filings import ParsedResultFilings, ResultFiling
from ..sources.exchange.security_master import ParsedSecurityMaster, SecurityRecord
from ..sources.exchange.tabular import RejectedRow
from ..sources.screener.result_screen import normalize_company_path
from .repo import utc_now


DATASET_SUFFIX = {
    "nse_financial_results": ".csv",
    "bse_financial_results": ".csv",
    "nse_equity_master": ".csv",
    "nse_sme_master": ".csv",
    "nse_symbol_change": ".csv",
    "nse_name_change": ".csv",
    "bse_security_master": ".csv",
}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------
# Immutable evidence
# --------------------------------------------------------------------------


def store_exchange_blob(
    conn: sqlite3.Connection,
    response: FetchResponse,
    *,
    exchange: str,
    dataset: str,
    effective_date: str,
    observed_at_utc: str,
    snapshot_root: Path = SNAPSHOTS,
) -> int:
    sha256 = hashlib.sha256(response.content).hexdigest()
    destination = (
        snapshot_root / "exchange" / exchange.upper() / dataset / effective_date
        / f"{sha256}{DATASET_SUFFIX.get(dataset, '.txt')}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(response.content)
        os.replace(temporary, destination)
    conn.execute(
        """INSERT OR IGNORE INTO response_blobs
           (content_sha256, content_type, encoding, byte_size, local_path,
            first_observed_at_utc, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            sha256, response.content_type, response.encoding, len(response.content),
            str(destination.resolve()), observed_at_utc, utc_now(),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (sha256,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist exchange response blob")
    return int(row["blob_id"])


def record_exchange_fetch(
    conn: sqlite3.Connection,
    run_id: int,
    response: FetchResponse,
    *,
    exchange: str,
    blob_id: int,
    fetched_at_utc: str,
) -> int:
    cursor = conn.execute(
        """INSERT INTO fetch_observations
           (run_id, company_id, source, reporting_scope, requested_source_period,
            resolved_source_period, requested_url, final_url, fetched_at_utc,
            http_status, etag, last_modified, auth_mode, blob_id, fetch_status,
            resource_kind)
           VALUES (?, NULL, ?, 'not_applicable', 'not_applicable', 'not_applicable',
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, 'exchange_dataset')""",
        (
            run_id, exchange.upper(), response.requested_url, response.final_url,
            fetched_at_utc, response.status_code, response.etag,
            response.last_modified, response.auth_mode, blob_id, response.fetch_status,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def persist_snapshot(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    dataset: str,
    effective_date: str,
    source_label: str,
    requested_url: str | None,
    acquisition_mode: str,
    fetch_id: int | None,
    blob_id: int,
    parser_name: str,
    parser_version: str,
    source_encoding: str,
    observed_at_utc: str,
    headers: Iterable[str],
    accepted_row_count: int,
    rejected: Iterable[RejectedRow],
    validation_status: str = "passed",
) -> tuple[int, bool]:
    rejected = tuple(rejected)
    with conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO exchange_source_snapshots
               (exchange, dataset, effective_date, source_label, requested_url,
                acquisition_mode, fetch_id, blob_id, parser_name, parser_version,
                source_encoding, observed_at_utc, header_json, accepted_row_count,
                rejected_row_count, validation_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                exchange.upper(), dataset, effective_date, source_label, requested_url,
                acquisition_mode, fetch_id, blob_id, parser_name, parser_version,
                source_encoding, observed_at_utc, _json(list(headers)),
                accepted_row_count, len(rejected), validation_status,
            ),
        )
        created = cursor.rowcount == 1
        row = conn.execute(
            """SELECT snapshot_id FROM exchange_source_snapshots
               WHERE dataset=? AND effective_date=? AND blob_id=? AND parser_name=?
                 AND parser_version=?""",
            (dataset, effective_date, blob_id, parser_name, parser_version),
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist exchange snapshot")
        snapshot_id = int(row["snapshot_id"])
        if created and rejected:
            conn.executemany(
                """INSERT INTO exchange_source_rejected_rows
                   (snapshot_id, row_position, reason_code, reason_detail, raw_cells_json)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (snapshot_id, item.row_position, item.reason_code,
                     item.reason_detail, _json(list(item.cells)))
                    for item in rejected
                ],
            )
    return snapshot_id, created


# --------------------------------------------------------------------------
# Security registry
# --------------------------------------------------------------------------


def _record_event(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    snapshot_id: int | None,
    event_date: str,
    event_type: str,
    from_value: str | None,
    to_value: str | None,
    evidence: Mapping[str, Any] | None,
    now_utc: str,
) -> None:
    conn.execute(
        """INSERT INTO exchange_security_events
           (exchange_security_id, snapshot_id, event_date, event_type,
            from_value, to_value, evidence_json, recorded_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            exchange_security_id, snapshot_id, event_date, event_type,
            from_value, to_value, _json(dict(evidence or {})), now_utc,
        ),
    )


def _insert_version(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    snapshot_id: int,
    record: SecurityRecord,
    effective_date: str,
    now_utc: str,
) -> int:
    conn.execute(
        """INSERT INTO exchange_security_versions
           (exchange_security_id, snapshot_id, isin, name_raw, status, status_raw,
            series_or_group, instrument_raw, face_value_raw, listing_date,
            attributes_json, row_sha256, valid_from_date, recorded_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(exchange_security_id, valid_from_date) DO UPDATE SET
             snapshot_id=excluded.snapshot_id, isin=excluded.isin,
             name_raw=excluded.name_raw, status=excluded.status,
             status_raw=excluded.status_raw, series_or_group=excluded.series_or_group,
             instrument_raw=excluded.instrument_raw,
             face_value_raw=excluded.face_value_raw,
             listing_date=excluded.listing_date,
             attributes_json=excluded.attributes_json,
             row_sha256=excluded.row_sha256""",
        (
            exchange_security_id, snapshot_id, record.isin, record.name_raw,
            record.status, record.status_raw, record.series_or_group,
            record.instrument_raw, record.face_value_raw, record.listing_date,
            _json(dict(record.attributes)), record.row_sha256, effective_date, now_utc,
        ),
    )
    version_id = int(
        conn.execute(
            """SELECT version_id FROM exchange_security_versions
               WHERE exchange_security_id=? AND valid_from_date=?""",
            (exchange_security_id, effective_date),
        ).fetchone()["version_id"]
    )
    conn.execute(
        """UPDATE exchange_security_versions SET valid_to_date=?
           WHERE exchange_security_id=? AND valid_to_date IS NULL AND version_id<>?""",
        (effective_date, exchange_security_id, version_id),
    )
    return version_id


def _link_isin(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    record: SecurityRecord,
    effective_date: str,
    snapshot_id: int,
    now_utc: str,
) -> int | None:
    """Union securities across exchanges by ISIN, and only by ISIN."""
    if not record.isin:
        return None
    conn.execute(
        """INSERT INTO exchange_company_registry
           (isin, canonical_name_raw, first_observed_date, last_observed_date,
            created_at_utc, updated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(isin) DO UPDATE SET
             last_observed_date=MAX(last_observed_date, excluded.last_observed_date),
             updated_at_utc=excluded.updated_at_utc""",
        (record.isin, record.name_raw, effective_date, effective_date, now_utc, now_utc),
    )
    registry_company_id = int(
        conn.execute(
            "SELECT registry_company_id FROM exchange_company_registry WHERE isin=?",
            (record.isin,),
        ).fetchone()["registry_company_id"]
    )
    if record.exchange == "NSE":
        # The NSE master carries the security's registered company name; BSE
        # rows carry an abbreviated security name, so they never overwrite it.
        conn.execute(
            """UPDATE exchange_company_registry SET canonical_name_raw=?, updated_at_utc=?
               WHERE registry_company_id=?""",
            (record.name_raw, now_utc, registry_company_id),
        )
    current = conn.execute(
        """SELECT link_id, registry_company_id FROM exchange_company_links
           WHERE exchange_security_id=? AND is_current=1""",
        (exchange_security_id,),
    ).fetchone()
    if current is not None and int(current["registry_company_id"]) == registry_company_id:
        return registry_company_id
    if current is not None:
        conn.execute(
            """UPDATE exchange_company_links
               SET is_current=0, unlinked_from_date=? WHERE link_id=?""",
            (effective_date, int(current["link_id"])),
        )
    conn.execute(
        """INSERT OR IGNORE INTO exchange_company_links
           (registry_company_id, exchange_security_id, link_basis, linked_from_date,
            is_current, created_at_utc)
           VALUES (?, ?, 'isin', ?, 1, ?)""",
        (registry_company_id, exchange_security_id, effective_date, now_utc),
    )
    return registry_company_id


def apply_security_master(
    conn: sqlite3.Connection,
    parsed: ParsedSecurityMaster,
    *,
    snapshot_id: int,
    effective_date: str,
    now_utc: str,
) -> dict[str, int]:
    """Fold one dated master into the durable registry without losing history."""
    counts = {
        "added": 0, "unchanged": 0, "changed": 0, "reinstated": 0,
        "removed": 0, "stale_rows": 0, "linked": 0,
    }
    segments: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    with conn:
        for record in parsed.records:
            segments.add(record.segment)
            key = (record.exchange, record.segment, record.exchange_key)
            seen.add(key)
            existing = conn.execute(
                """SELECT * FROM exchange_securities
                   WHERE exchange=? AND segment=? AND exchange_key=?""",
                key,
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    """INSERT INTO exchange_securities
                       (exchange, segment, exchange_key, nse_symbol, bse_scrip_code,
                        current_isin, current_name_raw, current_status, is_present,
                        first_observed_date, first_observed_snapshot_id,
                        last_observed_date, last_observed_snapshot_id,
                        created_at_utc, updated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.exchange, record.segment, record.exchange_key,
                        record.nse_symbol, record.bse_scrip_code, record.isin,
                        record.name_raw, record.status, effective_date, snapshot_id,
                        effective_date, snapshot_id, now_utc, now_utc,
                    ),
                )
                security_id = int(cursor.lastrowid)
                version_id = _insert_version(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    record=record, effective_date=effective_date, now_utc=now_utc,
                )
                conn.execute(
                    "UPDATE exchange_securities SET current_version_id=? WHERE exchange_security_id=?",
                    (version_id, security_id),
                )
                _record_event(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    event_date=effective_date, event_type="added", from_value=None,
                    to_value=record.exchange_key,
                    evidence={"isin": record.isin, "name": record.name_raw},
                    now_utc=now_utc,
                )
                counts["added"] += 1
                if _link_isin(
                    conn, exchange_security_id=security_id, record=record,
                    effective_date=effective_date, snapshot_id=snapshot_id, now_utc=now_utc,
                ) is not None:
                    counts["linked"] += 1
                continue

            security_id = int(existing["exchange_security_id"])
            if effective_date < str(existing["last_observed_date"]):
                # An older snapshot is retained as evidence but never moves the
                # current pointer backward.
                counts["stale_rows"] += 1
                continue
            current_version = conn.execute(
                """SELECT * FROM exchange_security_versions
                   WHERE exchange_security_id=? ORDER BY valid_from_date DESC, version_id DESC
                   LIMIT 1""",
                (security_id,),
            ).fetchone()
            changed = current_version is None or str(current_version["row_sha256"]) != record.row_sha256
            if changed:
                version_id = _insert_version(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    record=record, effective_date=effective_date, now_utc=now_utc,
                )
                for event_type, before, after in (
                    ("isin_changed", current_version["isin"] if current_version else None, record.isin),
                    ("name_changed", current_version["name_raw"] if current_version else None, record.name_raw),
                    ("status_changed", current_version["status"] if current_version else None, record.status),
                ):
                    if current_version is not None and before != after:
                        _record_event(
                            conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                            event_date=effective_date, event_type=event_type,
                            from_value=before, to_value=after, evidence={}, now_utc=now_utc,
                        )
                if current_version is not None and (
                    current_version["isin"] == record.isin
                    and current_version["name_raw"] == record.name_raw
                    and current_version["status"] == record.status
                ):
                    _record_event(
                        conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                        event_date=effective_date, event_type="attribute_changed",
                        from_value=None, to_value=None,
                        evidence={"row_sha256": record.row_sha256}, now_utc=now_utc,
                    )
                counts["changed"] += 1
            else:
                version_id = int(current_version["version_id"])
                counts["unchanged"] += 1
            if not int(existing["is_present"]):
                _record_event(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    event_date=effective_date, event_type="reinstated", from_value=None,
                    to_value=record.exchange_key, evidence={}, now_utc=now_utc,
                )
                counts["reinstated"] += 1
            conn.execute(
                """UPDATE exchange_securities SET current_isin=?, current_name_raw=?,
                   current_status=?, current_version_id=?, is_present=1,
                   last_observed_date=?, last_observed_snapshot_id=?, updated_at_utc=?
                   WHERE exchange_security_id=?""",
                (
                    record.isin, record.name_raw, record.status, version_id,
                    effective_date, snapshot_id, now_utc, security_id,
                ),
            )
            if _link_isin(
                conn, exchange_security_id=security_id, record=record,
                effective_date=effective_date, snapshot_id=snapshot_id, now_utc=now_utc,
            ) is not None:
                counts["linked"] += 1

        # Securities that this full master no longer lists are marked absent and
        # kept, never deleted.
        for segment in sorted(segments):
            for row in conn.execute(
                """SELECT exchange_security_id, exchange_key, last_observed_date
                   FROM exchange_securities
                   WHERE exchange=? AND segment=? AND is_present=1""",
                (parsed.exchange, segment),
            ).fetchall():
                key = (parsed.exchange, segment, str(row["exchange_key"]))
                if key in seen or effective_date < str(row["last_observed_date"]):
                    continue
                security_id = int(row["exchange_security_id"])
                conn.execute(
                    """UPDATE exchange_securities SET is_present=0,
                       current_status='absent_from_master', updated_at_utc=?
                       WHERE exchange_security_id=?""",
                    (now_utc, security_id),
                )
                _record_event(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    event_date=effective_date, event_type="removed",
                    from_value=str(row["exchange_key"]), to_value=None,
                    evidence={"dataset": parsed.dataset}, now_utc=now_utc,
                )
                counts["removed"] += 1
    return counts


def apply_corporate_changes(
    conn: sqlite3.Connection,
    parsed: ParsedCorporateChanges,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, int]:
    counts = {"applied": 0, "unmatched": 0, "reused": 0}
    with conn:
        for change in parsed.changes:
            security = None
            for hint in change.key_hints:
                security = conn.execute(
                    """SELECT exchange_security_id FROM exchange_securities
                       WHERE exchange=? AND exchange_key=?
                       ORDER BY is_present DESC, exchange_security_id LIMIT 1""",
                    (change.exchange, hint),
                ).fetchone()
                if security is not None:
                    break
            security_id = int(security["exchange_security_id"]) if security else None
            status = "applied" if security_id is not None else "unmatched"
            cursor = conn.execute(
                """INSERT OR IGNORE INTO exchange_symbol_changes
                   (snapshot_id, exchange, change_kind, company_name_raw, old_value,
                    new_value, effective_date, applied_status, exchange_security_id,
                    recorded_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, change.exchange, change.change_kind,
                    change.company_name_raw, change.old_value, change.new_value,
                    change.effective_date, status, security_id, now_utc,
                ),
            )
            if cursor.rowcount != 1:
                counts["reused"] += 1
                continue
            if security_id is None:
                counts["unmatched"] += 1
                continue
            _record_event(
                conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                event_date=change.effective_date,
                event_type="symbol_changed" if change.change_kind == "symbol" else "name_changed",
                from_value=change.old_value, to_value=change.new_value,
                evidence={"dataset": parsed.dataset, "company_name_raw": change.company_name_raw},
                now_utc=now_utc,
            )
            counts["applied"] += 1
    return counts


# --------------------------------------------------------------------------
# Result filings
# --------------------------------------------------------------------------


def ingest_result_filings(
    conn: sqlite3.Connection,
    parsed: ParsedResultFilings,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, Any]:
    """Store filings idempotently and advance the broadcast watermark."""
    previous = conn.execute(
        """SELECT last_broadcast_at_utc FROM exchange_result_watermarks
           WHERE exchange=? AND dataset=?""",
        (parsed.exchange, parsed.dataset),
    ).fetchone()
    watermark = str(previous["last_broadcast_at_utc"]) if previous else None
    counts = {"stored": 0, "already_stored": 0, "after_watermark": 0, "at_or_before_watermark": 0}
    with conn:
        for filing in parsed.filings:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO exchange_result_filings
                   (snapshot_id, exchange, filing_key, exchange_key, isin,
                    company_name_raw, result_period, period_end_date, reporting_scope,
                    audited_raw, cumulative_raw, relating_to_raw, broadcast_raw,
                    broadcast_at_utc, raw_row_json, discovered_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, filing.exchange, filing.filing_key, filing.exchange_key,
                    filing.isin, filing.company_name_raw, filing.result_period,
                    filing.period_end_date, filing.reporting_scope, filing.audited_raw,
                    filing.cumulative_raw, filing.relating_to_raw, filing.broadcast_raw,
                    filing.broadcast_at_utc, filing.raw_row_json, now_utc,
                ),
            )
            counts["stored" if cursor.rowcount == 1 else "already_stored"] += 1
            if watermark is None or filing.broadcast_at_utc > watermark:
                counts["after_watermark"] += 1
            else:
                counts["at_or_before_watermark"] += 1
        newest = parsed.max_broadcast_at_utc
        if newest is not None and (watermark is None or newest > watermark):
            conn.execute(
                """INSERT INTO exchange_result_watermarks
                   (exchange, dataset, last_broadcast_at_utc, last_snapshot_id, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(exchange, dataset) DO UPDATE SET
                     last_broadcast_at_utc=excluded.last_broadcast_at_utc,
                     last_snapshot_id=excluded.last_snapshot_id,
                     updated_at_utc=excluded.updated_at_utc""",
                (parsed.exchange, parsed.dataset, newest, snapshot_id, now_utc),
            )
    return {
        "previous_watermark_utc": watermark,
        "watermark_utc": max(filter(None, (watermark, parsed.max_broadcast_at_utc)), default=None),
        **counts,
    }


def resolve_screener_path(
    conn: sqlite3.Connection, *, exchange: str, exchange_key: str, reporting_scope: str
) -> dict[str, Any]:
    """Derive the Screener company path an exchange filing must be collected at.

    Screener addresses NSE-listed companies by their NSE symbol and BSE-only
    companies by their scrip code. The preference is resolved through the
    ISIN-keyed registry so a BSE filing for a dual-listed company still collects
    the NSE company page.
    """
    security = conn.execute(
        """SELECT * FROM exchange_securities WHERE exchange=? AND exchange_key=?
           ORDER BY is_present DESC, exchange_security_id LIMIT 1""",
        (exchange, exchange_key),
    ).fetchone()
    if security is None:
        return {
            "decision": "manual_review",
            "reason": "security_not_in_registry",
            "registry_company_id": None,
        }
    link = conn.execute(
        """SELECT registry_company_id FROM exchange_company_links
           WHERE exchange_security_id=? AND is_current=1""",
        (int(security["exchange_security_id"]),),
    ).fetchone()
    registry_company_id = int(link["registry_company_id"]) if link else None
    candidates = [security]
    if registry_company_id is not None:
        candidates = conn.execute(
            """SELECT security.* FROM exchange_securities AS security
               JOIN exchange_company_links AS link USING (exchange_security_id)
               WHERE link.registry_company_id=? AND link.is_current=1
               ORDER BY
                 CASE security.exchange WHEN 'NSE' THEN 0 ELSE 1 END,
                 security.is_present DESC,
                 CASE security.segment WHEN 'equity' THEN 0 ELSE 1 END,
                 security.exchange_security_id""",
            (registry_company_id,),
        ).fetchall() or [security]
    chosen = candidates[0]
    if chosen["nse_symbol"]:
        identifier, basis = str(chosen["nse_symbol"]), "nse_symbol"
    else:
        identifier, basis = str(chosen["bse_scrip_code"]), "bse_scrip_code"
    suffix = "consolidated/" if reporting_scope == "consolidated" else ""
    try:
        path, path_scope = normalize_company_path(f"/company/{identifier}/{suffix}")
    except ValueError as exc:
        return {
            "decision": "manual_review",
            "reason": f"unusable_company_path: {exc}",
            "registry_company_id": registry_company_id,
        }
    if path_scope != reporting_scope:
        return {
            "decision": "manual_review",
            "reason": "reporting_scope_path_mismatch",
            "registry_company_id": registry_company_id,
        }
    return {
        "decision": "resolved",
        "reason": "resolved",
        "company_path_normalized": path,
        "path_basis": basis,
        "registry_company_id": registry_company_id,
        "exchange_security_id": int(security["exchange_security_id"]),
    }


def _decide(
    conn: sqlite3.Connection, filing: sqlite3.Row, *, now_utc: str
) -> dict[str, Any]:
    resolution = resolve_screener_path(
        conn,
        exchange=str(filing["exchange"]),
        exchange_key=str(filing["exchange_key"]),
        reporting_scope=str(filing["reporting_scope"]),
    )
    period = str(filing["result_period"])
    scope = str(filing["reporting_scope"])
    if resolution["decision"] != "resolved":
        return {**resolution, "queue_item_id": None, "result_period": period}
    path = str(resolution["company_path_normalized"])
    state = conn.execute(
        """SELECT latest_accepted_result_period FROM screener_company_collection_state
           WHERE company_path_normalized=? AND reporting_scope=?""",
        (path, scope),
    ).fetchone()
    accepted = (
        str(state["latest_accepted_result_period"])
        if state and state["latest_accepted_result_period"] else None
    )
    conn.execute(
        """INSERT INTO screener_company_collection_state
           (company_path_normalized, reporting_scope, latest_discovered_result_period,
            updated_at_utc)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_path_normalized, reporting_scope) DO UPDATE SET
             latest_discovered_result_period=CASE
               WHEN excluded.latest_discovered_result_period > latest_discovered_result_period
               THEN excluded.latest_discovered_result_period
               ELSE latest_discovered_result_period END,
             updated_at_utc=excluded.updated_at_utc""",
        (path, scope, period, now_utc),
    )
    if accepted is not None and period < accepted:
        return {**resolution, "decision": "stale", "reason": "older_than_accepted",
                "queue_item_id": None, "result_period": period}
    if accepted == period:
        return {**resolution, "decision": "already_accepted", "reason": "period_already_accepted",
                "queue_item_id": None, "result_period": period}
    reason = "new_company" if accepted is None else "result_period_advanced"
    cursor = conn.execute(
        """INSERT OR IGNORE INTO screener_company_queue
           (origin, candidate_id, exchange_filing_id, company_path_normalized,
            reporting_scope, result_period, queue_reason, status, attempt_count,
            next_attempt_at_utc, created_at_utc, updated_at_utc)
           VALUES ('exchange_filing', NULL, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
        (
            int(filing["filing_id"]), path, scope, period, reason,
            now_utc, now_utc, now_utc,
        ),
    )
    if cursor.rowcount == 1:
        queue_item_id = int(cursor.lastrowid)
        conn.execute(
            """INSERT INTO screener_company_queue_events
               (queue_item_id, event_at_utc, from_status, to_status, evidence_json)
               VALUES (?, ?, NULL, 'pending', ?)""",
            (
                queue_item_id, now_utc,
                _json(
                    {
                        "origin": "exchange_filing",
                        "queue_reason": reason,
                        "filing_id": int(filing["filing_id"]),
                        "exchange": str(filing["exchange"]),
                        "exchange_key": str(filing["exchange_key"]),
                        "path_basis": resolution["path_basis"],
                    }
                ),
            ),
        )
        decision = "queued"
    else:
        existing = conn.execute(
            """SELECT queue_item_id FROM screener_company_queue
               WHERE company_path_normalized=? AND reporting_scope=? AND result_period=?""",
            (path, scope, period),
        ).fetchone()
        queue_item_id = int(existing["queue_item_id"]) if existing else None
        decision = "reused"
    return {
        **resolution,
        "decision": decision,
        "reason": reason,
        "queue_item_id": queue_item_id,
        "result_period": period,
    }


def preview_filing_decisions(
    conn: sqlite3.Connection, parsed: ParsedResultFilings
) -> dict[str, int]:
    """Project queue decisions without writing anything (dry-run only)."""
    counts: dict[str, int] = {
        "queued": 0, "reused": 0, "stale": 0, "already_accepted": 0, "manual_review": 0,
    }
    planned: set[tuple[str, str, str]] = set()
    for filing in parsed.filings:
        resolution = resolve_screener_path(
            conn,
            exchange=filing.exchange,
            exchange_key=filing.exchange_key,
            reporting_scope=filing.reporting_scope,
        )
        if resolution["decision"] != "resolved":
            counts["manual_review"] += 1
            continue
        key = (
            str(resolution["company_path_normalized"]),
            filing.reporting_scope,
            filing.result_period,
        )
        state = conn.execute(
            """SELECT latest_accepted_result_period FROM screener_company_collection_state
               WHERE company_path_normalized=? AND reporting_scope=?""",
            key[:2],
        ).fetchone()
        accepted = (
            str(state["latest_accepted_result_period"])
            if state and state["latest_accepted_result_period"] else None
        )
        existing = conn.execute(
            """SELECT 1 FROM screener_company_queue
               WHERE company_path_normalized=? AND reporting_scope=? AND result_period=?""",
            key,
        ).fetchone()
        if accepted is not None and filing.result_period < accepted:
            counts["stale"] += 1
        elif accepted == filing.result_period:
            counts["already_accepted"] += 1
        elif existing is not None or key in planned:
            counts["reused"] += 1
        else:
            planned.add(key)
            counts["queued"] += 1
    return counts


def enqueue_result_filings(
    conn: sqlite3.Connection, *, snapshot_id: int | None = None, now_utc: str
) -> dict[str, int]:
    """Queue only undecided filings; a company enters work when its period advances."""
    query = """SELECT * FROM exchange_result_filings AS filing
               WHERE NOT EXISTS (
                 SELECT 1 FROM exchange_screener_enqueues AS decision
                 WHERE decision.filing_id = filing.filing_id
               )"""
    parameters: tuple[Any, ...] = ()
    if snapshot_id is not None:
        query += " AND filing.snapshot_id=?"
        parameters = (snapshot_id,)
    query += " ORDER BY filing.broadcast_at_utc, filing.filing_id"
    counts: dict[str, int] = {
        "queued": 0, "reused": 0, "unchanged": 0, "stale": 0,
        "already_accepted": 0, "manual_review": 0,
    }
    with conn:
        for filing in conn.execute(query, parameters).fetchall():
            outcome = _decide(conn, filing, now_utc=now_utc)
            decision = str(outcome["decision"])
            counts[decision] = counts.get(decision, 0) + 1
            conn.execute(
                """INSERT OR IGNORE INTO exchange_screener_enqueues
                   (filing_id, decision, decision_reason, company_path_normalized,
                    reporting_scope, result_period, path_basis, registry_company_id,
                    queue_item_id, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(filing["filing_id"]), decision, str(outcome["reason"]),
                    outcome.get("company_path_normalized"),
                    str(filing["reporting_scope"]), str(filing["result_period"]),
                    outcome.get("path_basis"), outcome.get("registry_company_id"),
                    outcome.get("queue_item_id"), now_utc,
                ),
            )
            conn.execute(
                """INSERT INTO exchange_company_result_state
                   (exchange, exchange_key, reporting_scope, latest_filed_result_period,
                    latest_broadcast_at_utc, latest_filing_id, latest_queued_result_period,
                    updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(exchange, exchange_key, reporting_scope) DO UPDATE SET
                     latest_filed_result_period=MAX(latest_filed_result_period, excluded.latest_filed_result_period),
                     latest_broadcast_at_utc=MAX(latest_broadcast_at_utc, excluded.latest_broadcast_at_utc),
                     latest_filing_id=CASE
                       WHEN excluded.latest_broadcast_at_utc >= latest_broadcast_at_utc
                       THEN excluded.latest_filing_id ELSE latest_filing_id END,
                     latest_queued_result_period=CASE
                       WHEN excluded.latest_queued_result_period IS NOT NULL
                        AND (latest_queued_result_period IS NULL
                             OR excluded.latest_queued_result_period > latest_queued_result_period)
                       THEN excluded.latest_queued_result_period
                       ELSE latest_queued_result_period END,
                     updated_at_utc=excluded.updated_at_utc""",
                (
                    str(filing["exchange"]), str(filing["exchange_key"]),
                    str(filing["reporting_scope"]), str(filing["result_period"]),
                    str(filing["broadcast_at_utc"]), int(filing["filing_id"]),
                    str(filing["result_period"]) if decision == "queued" else None,
                    now_utc,
                ),
            )
    return counts
