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
from typing import Any, Iterable, Mapping, Sequence

from ..config import EXCHANGE_ABSENCE_CONFIRMATIONS, SNAPSHOTS
from ..core.http import FetchResponse
from ..sources.exchange.corporate_changes import ParsedCorporateChanges
from ..sources.exchange.result_filings import ParsedResultFilings, ResultFiling
from ..sources.exchange.security_master import ParsedSecurityMaster, SecurityRecord
from ..sources.exchange.snapshot_guard import (
    GuardVerdict,
    ScopeMetrics,
    SnapshotBaseline,
)
from ..sources.exchange.tabular import ExcludedRow, RejectedRow
from ..sources.screener.result_screen import canonical_company_path
from .repo import utc_now


# The suffix describes the bytes as served, because the stored blob is exactly
# what the exchange published; a gzipped report is kept gzipped.
DATASET_SUFFIX = {
    "nse_financial_results": ".csv",
    "bse_financial_results": ".csv",
    "nse_equity_master": ".csv",
    "nse_sme_master": ".csv",
    "nse_symbol_change": ".csv",
    "nse_name_change": ".csv",
    "bse_security_master": ".csv",
    "nse_mii_security_file": ".csv",
}
COMPRESSION_SUFFIX = {"gzip": ".gz", "zip": ".zip"}


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
    compression: str = "none",
) -> int:
    sha256 = hashlib.sha256(response.content).hexdigest()
    suffix = DATASET_SUFFIX.get(dataset, ".txt") + COMPRESSION_SUFFIX.get(compression, "")
    destination = (
        snapshot_root / "exchange" / exchange.upper() / dataset / effective_date
        / f"{sha256}{suffix}"
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
    compression: str = "none",
    excluded: Iterable[ExcludedRow] = (),
) -> tuple[int, bool]:
    rejected = tuple(rejected)
    excluded = tuple(excluded)
    with conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO exchange_source_snapshots
               (exchange, dataset, effective_date, source_label, requested_url,
                acquisition_mode, fetch_id, blob_id, parser_name, parser_version,
                source_encoding, observed_at_utc, header_json, accepted_row_count,
                rejected_row_count, validation_status, compression,
                excluded_row_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                exchange.upper(), dataset, effective_date, source_label, requested_url,
                acquisition_mode, fetch_id, blob_id, parser_name, parser_version,
                source_encoding, observed_at_utc, _json(list(headers)),
                accepted_row_count, len(rejected), validation_status, compression,
                len(excluded),
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
        if created and excluded:
            conn.executemany(
                """INSERT INTO exchange_source_excluded_rows
                   (snapshot_id, row_position, instrument_class, reason_detail,
                    raw_cells_json)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (snapshot_id, item.row_position, item.instrument_class,
                     item.reason_detail, _json(list(item.cells)))
                    for item in excluded
                ],
            )
    return snapshot_id, created


# --------------------------------------------------------------------------
# Snapshot-level validation
# --------------------------------------------------------------------------


def latest_passed_snapshot(
    conn: sqlite3.Connection, dataset: str, *, before_snapshot_id: int | None = None
) -> SnapshotBaseline | None:
    """The most recent accepted snapshot of a dataset, as a guard baseline.

    A snapshot that only passed because it was overridden is still the state the
    registry currently reflects, so it is a legitimate baseline for the next one.
    """
    query = """SELECT snapshot.snapshot_id, snapshot.accepted_row_count,
                      snapshot.header_json, snapshot.effective_date,
                      validation.metrics_json
               FROM exchange_source_snapshots AS snapshot
               LEFT JOIN exchange_snapshot_validations AS validation
                 USING (snapshot_id)
               WHERE snapshot.dataset=?
                 AND snapshot.validation_status IN ('passed', 'overridden')"""
    parameters: tuple[Any, ...] = (dataset,)
    if before_snapshot_id is not None:
        query += " AND snapshot.snapshot_id<>?"
        parameters += (before_snapshot_id,)
    query += " ORDER BY snapshot.effective_date DESC, snapshot.snapshot_id DESC LIMIT 1"
    row = conn.execute(query, parameters).fetchone()
    if row is None:
        return None
    metrics = json.loads(str(row["metrics_json"] or "{}"))
    return SnapshotBaseline(
        snapshot_id=int(row["snapshot_id"]),
        accepted_row_count=int(row["accepted_row_count"]),
        headers=tuple(json.loads(str(row["header_json"] or "[]"))),
        distinct_isin_count=int(metrics.get("distinct_isin_count") or 0),
        effective_date=str(row["effective_date"]),
        # Recorded with the verdict, so a snapshot from before the guard measured
        # scopes simply carries none and the per-scope size rule sits out a turn.
        scope_row_counts=tuple(
            (str(item["exchange"]), str(item["segment"]),
             int(item.get("accepted_row_count") or 0))
            for item in metrics.get("scope_metrics") or ()
        ),
    )


def record_snapshot_validation(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    verdict: GuardVerdict,
    evaluated_at_utc: str,
) -> None:
    """Store the verdict and move the snapshot's own status to match it.

    The snapshot and its blob are kept either way: a rejected file is evidence
    of what the exchange published, and discarding it would hide the incident.
    """
    with conn:
        conn.execute(
            """INSERT INTO exchange_snapshot_validations
               (snapshot_id, baseline_snapshot_id, status, findings_json,
                metrics_json, thresholds_json, evaluated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapshot_id) DO UPDATE SET
                 baseline_snapshot_id=excluded.baseline_snapshot_id,
                 status=excluded.status, findings_json=excluded.findings_json,
                 metrics_json=excluded.metrics_json,
                 thresholds_json=excluded.thresholds_json,
                 evaluated_at_utc=excluded.evaluated_at_utc""",
            (
                snapshot_id,
                verdict.baseline.snapshot_id if verdict.baseline else None,
                verdict.status,
                _json([item.as_dict() for item in verdict.findings]),
                _json(verdict.metrics.as_dict() if verdict.metrics else {}),
                _json(verdict.thresholds.as_dict()),
                evaluated_at_utc,
            ),
        )
        conn.execute(
            "UPDATE exchange_source_snapshots SET validation_status=? WHERE snapshot_id=?",
            (verdict.status, snapshot_id),
        )


def sweep_scope(
    parsed: ParsedSecurityMaster,
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
) -> tuple[tuple[str, str], ...]:
    """The (exchange, segment) pairs a master may mark absent.

    A file is a complete statement of its own scope and of nothing else. The NSE
    equity master lists no SME security and the combined MII file lists neither
    exchange's SME segment, so neither may conclude that those securities have
    gone. Absent a declared scope the file speaks only for what it carries.

    Within the declared scope the sweep is unconditional, including over a
    segment this particular file happens to carry no rows for. Skipping it there
    would be the wrong protection in the wrong place: a file that dropped a whole
    exchange is caught by the row-count and absence-volume guards before it can
    be applied at all, and narrowing the sweep here would only hide real
    delistings on the days the file is sound.
    """
    return tuple(authoritative_segments or ()) or parsed.segments


def snapshot_content_is_new(
    conn: sqlite3.Connection, *, dataset: str, snapshot_id: int
) -> bool:
    """True when this snapshot's bytes differ from the previous one's.

    This answers whether the content moved, and nothing more. Identical bytes do
    not mean the exchange failed to publish: a security master is republished
    every trading day and is legitimately unchanged on a day with no listing,
    delisting, symbol change or series change. Non-publication has its own
    evidence — the dated artifact is absent — and is established there, not here.
    """
    row = conn.execute(
        """SELECT blob_id FROM exchange_source_snapshots WHERE snapshot_id=?""",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return True
    previous = conn.execute(
        """SELECT blob_id FROM exchange_source_snapshots
           WHERE dataset=? AND snapshot_id<>?
           ORDER BY effective_date DESC, snapshot_id DESC LIMIT 1""",
        (dataset, snapshot_id),
    ).fetchone()
    return previous is None or int(previous["blob_id"]) != int(row["blob_id"])


# What one attempt found. The two `published_` outcomes are both successes: an
# exchange that publishes an unchanged file has published, and a day on which no
# security changed is an ordinary day, not a failure to look at again.
SCHEDULE_OUTCOMES = (
    "published_changed", "published_unchanged", "already_refreshed",
    "not_published", "guard_failed", "fetch_failed", "error", "skipped_no_url",
    "unrefreshed", "source_access_blocked",
)

# The outcomes that satisfy a scheduled day and stop the ladder for a dataset.
SATISFYING_OUTCOMES = ("published_changed", "published_unchanged")

SCHEDULE_ROLES = ("primary", "revision")


def active_source_access_holds(
    conn: sqlite3.Connection, *, before_utc: str | None = None,
) -> dict[str, dict[str, str]]:
    """Return the latest access event per dataset when it is a BLOCKED event."""
    return {
        str(row["dataset"]): dict(row)
        for row in conn.execute(
            """SELECT event.dataset, event.action, event.event_at_utc, event.evidence
               FROM exchange_source_access_events AS event
               WHERE event.event_id = (
                   SELECT MAX(later.event_id) FROM exchange_source_access_events AS later
                   WHERE later.dataset=event.dataset
                     AND (? IS NULL OR later.event_at_utc < ?)
               ) AND event.action='BLOCKED'""",
            (before_utc, before_utc),
        )
    }


def record_source_access_event(
    conn: sqlite3.Connection, *, dataset: str, action: str,
    event_at_utc: str, evidence: str,
) -> None:
    if not dataset.startswith("bse_") or dataset not in DATASET_SUFFIX:
        raise ValueError("Only a registered BSE dataset may have an access hold")
    if action not in {"BLOCKED", "RESUMED"} or not evidence.strip():
        raise ValueError("Access event needs a valid action and evidence")
    with conn:
        conn.execute(
            """INSERT INTO exchange_source_access_events
               (dataset, action, event_at_utc, evidence) VALUES (?, ?, ?, ?)""",
            (dataset, action, event_at_utc, evidence.strip()),
        )


def record_schedule_run(
    conn: sqlite3.Connection,
    *,
    dataset: str,
    scheduled_for: str,
    attempt: int,
    outcome: str,
    snapshot_id: int | None,
    error: str,
    started_at_utc: str,
    finished_at_utc: str,
    result_period: str = "",
    role: str = "primary",
) -> None:
    """Write one attempt's outcome to the schedule ledger.

    A run is identified by its role as well as its rung, because a revision
    sweep re-reads an already-collected period alongside the day's primary run
    and is a different fact about a different period.
    """
    if outcome not in SCHEDULE_OUTCOMES:
        raise ValueError(f"Unknown schedule outcome: {outcome!r}")
    if role not in SCHEDULE_ROLES:
        raise ValueError(f"Unknown schedule role: {role!r}")
    with conn:
        conn.execute(
            """INSERT INTO exchange_schedule_runs
               (dataset, scheduled_for, attempt, role, result_period, outcome,
                snapshot_id, error, started_at_utc, finished_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(dataset, scheduled_for, attempt, role) DO UPDATE SET
                 result_period=excluded.result_period, outcome=excluded.outcome,
                 snapshot_id=excluded.snapshot_id,
                 error=excluded.error, started_at_utc=excluded.started_at_utc,
                 finished_at_utc=excluded.finished_at_utc""",
            (
                dataset, scheduled_for, attempt, role, result_period, outcome,
                snapshot_id, error, started_at_utc, finished_at_utc,
            ),
        )


def datasets_published_on(conn: sqlite3.Connection, scheduled_for: str) -> set[str]:
    """Datasets an earlier primary attempt already got a publication of.

    A revision sweep re-reads an old period and says nothing about whether today
    was published, so it never satisfies the day.
    """
    placeholders = ", ".join("?" for _ in SATISFYING_OUTCOMES)
    return {
        str(row["dataset"])
        for row in conn.execute(
            f"""SELECT DISTINCT dataset FROM exchange_schedule_runs
                WHERE scheduled_for=? AND role='primary'
                  AND outcome IN ({placeholders})""",
            (scheduled_for, *SATISFYING_OUTCOMES),
        )
    }


def schedule_day(conn: sqlite3.Connection, scheduled_for: str) -> list[dict[str, Any]]:
    """Every recorded attempt for one scheduled day, oldest first."""
    return [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM exchange_schedule_runs WHERE scheduled_for=?
               ORDER BY attempt, dataset""",
            (scheduled_for,),
        )
    ]


def absence_preview(
    conn: sqlite3.Connection,
    parsed: ParsedSecurityMaster,
    *,
    effective_date: str,
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
) -> tuple[ScopeMetrics, ...]:
    """Measure the sweep this master would perform, one authoritative scope at a time.

    Every scope the file may mark absent is reported separately — its rows in
    the file, the securities currently present, and how many of those the file
    would not confirm — so the guard can hold each to its own bound instead of
    only their sum. A scope the file carries no rows for is still reported: it
    is in the declared scope, so it is swept, so it is judged.
    """
    seen = {
        (record.exchange, record.segment, record.exchange_key)
        for record in parsed.records
    }
    rows_in_file: dict[tuple[str, str], int] = {}
    for record in parsed.records:
        key = (record.exchange, record.segment)
        rows_in_file[key] = rows_in_file.get(key, 0) + 1

    measured: list[ScopeMetrics] = []
    for exchange, segment in sweep_scope(parsed, authoritative_segments):
        would_absent = 0
        present = 0
        for row in conn.execute(
            """SELECT exchange_key, last_observed_date FROM exchange_securities
               WHERE exchange=? AND segment=? AND is_present=1""",
            (exchange, segment),
        ).fetchall():
            present += 1
            if (exchange, segment, str(row["exchange_key"])) in seen:
                continue
            if effective_date < str(row["last_observed_date"]):
                continue
            would_absent += 1
        measured.append(
            ScopeMetrics(
                exchange=exchange,
                segment=segment,
                accepted_row_count=rows_in_file.get((exchange, segment), 0),
                would_absent_count=would_absent,
                present_count=present,
            )
        )
    return tuple(measured)


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


def _open_conflict(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    exchange_security_id: int | None,
    conflict_type: str,
    exchange: str,
    exchange_key: str,
    existing_value: str | None,
    incoming_value: str | None,
    existing_source_type: str | None,
    now_utc: str,
) -> bool:
    """Record an identity disagreement. Returns True when it is a new one.

    The same disagreement seen on a later snapshot is the same open question,
    not a new one, so re-ingesting a file cannot inflate the conflict queue.
    """
    cursor = conn.execute(
        """INSERT OR IGNORE INTO exchange_identity_conflicts
           (snapshot_id, exchange_security_id, conflict_type, exchange, exchange_key,
            existing_value, incoming_value, existing_source_type, resolution,
            detected_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (
            snapshot_id, exchange_security_id, conflict_type, exchange, exchange_key,
            existing_value, incoming_value, existing_source_type, now_utc,
        ),
    )
    if cursor.rowcount != 1:
        return False
    if exchange_security_id is not None:
        _record_event(
            conn, exchange_security_id=exchange_security_id, snapshot_id=snapshot_id,
            event_date=now_utc[:10], event_type="conflict_detected",
            from_value=existing_value, to_value=incoming_value,
            evidence={
                "conflict_type": conflict_type,
                "existing_source_type": existing_source_type,
            },
            now_utc=now_utc,
        )
    return True


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
    return _link_isin_value(
        conn,
        exchange_security_id=exchange_security_id,
        exchange=record.exchange,
        isin=record.isin,
        name_raw=record.name_raw,
        effective_date=effective_date,
        now_utc=now_utc,
    )


def _link_isin_value(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    exchange: str,
    isin: str,
    name_raw: str,
    effective_date: str,
    now_utc: str,
) -> int:
    conn.execute(
        """INSERT INTO exchange_company_registry
           (isin, canonical_name_raw, first_observed_date, last_observed_date,
            created_at_utc, updated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(isin) DO UPDATE SET
             last_observed_date=MAX(last_observed_date, excluded.last_observed_date),
             updated_at_utc=excluded.updated_at_utc""",
        (isin, name_raw, effective_date, effective_date, now_utc, now_utc),
    )
    registry_company_id = int(
        conn.execute(
            "SELECT registry_company_id FROM exchange_company_registry WHERE isin=?",
            (isin,),
        ).fetchone()["registry_company_id"]
    )
    if exchange == "NSE":
        # The NSE master carries the security's registered company name; BSE
        # rows carry an abbreviated security name, so they never overwrite it.
        conn.execute(
            """UPDATE exchange_company_registry SET canonical_name_raw=?, updated_at_utc=?
               WHERE registry_company_id=?""",
            (name_raw, now_utc, registry_company_id),
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


def _already_absent_on(
    conn: sqlite3.Connection, last_absent_snapshot_id: Any, *, effective_date: str
) -> bool:
    """True when the streak was last advanced by a file of this same date."""
    if last_absent_snapshot_id is None:
        return False
    row = conn.execute(
        "SELECT effective_date FROM exchange_source_snapshots WHERE snapshot_id=?",
        (int(last_absent_snapshot_id),),
    ).fetchone()
    return row is not None and str(row["effective_date"]) == effective_date


def _link_or_conflict(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    record: SecurityRecord,
    effective_date: str,
    snapshot_id: int,
    now_utc: str,
    keys_in_file: set[tuple[str, str]],
) -> tuple[int | None, bool]:
    """Link by ISIN unless the same file gives that ISIN to another security.

    A security that this file has dropped but that still holds the ISIN is being
    superseded - an ordinary symbol rename - and is not a conflict. Two rows in
    the same file claiming one ISIN is a conflict, and neither is linked.
    """
    if record.isin:
        for row in conn.execute(
            """SELECT exchange_security_id, exchange_key, source_type
               FROM exchange_securities
               WHERE exchange=? AND segment=? AND current_isin=?
                 AND exchange_security_id<>? AND is_present=1""",
            (record.exchange, record.segment, record.isin, exchange_security_id),
        ).fetchall():
            if (record.segment, str(row["exchange_key"])) not in keys_in_file:
                continue
            opened = _open_conflict(
                conn, snapshot_id=snapshot_id,
                exchange_security_id=exchange_security_id,
                conflict_type="duplicate_isin", exchange=record.exchange,
                exchange_key=record.exchange_key, existing_value=str(row["exchange_key"]),
                incoming_value=record.isin,
                existing_source_type=str(row["source_type"]), now_utc=now_utc,
            )
            conn.execute(
                """UPDATE exchange_securities SET verification_status='conflict',
                   updated_at_utc=? WHERE exchange_security_id=?""",
                (now_utc, exchange_security_id),
            )
            return None, opened
    registry_company_id = _link_isin(
        conn, exchange_security_id=exchange_security_id, record=record,
        effective_date=effective_date, snapshot_id=snapshot_id, now_utc=now_utc,
    )
    return registry_company_id, False


def apply_security_master(
    conn: sqlite3.Connection,
    parsed: ParsedSecurityMaster,
    *,
    snapshot_id: int,
    effective_date: str,
    now_utc: str,
    acquisition_mode: str = "fetched",
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Fold one dated master into the durable registry without losing history.

    Three rules keep an automated refresh from damaging the registry. A security
    missing from this file is only *observed* absent; it stops being present
    after EXCHANGE_ABSENCE_CONFIRMATIONS distinct effective dates. An
    operator-imported row that disagrees with the file about identity opens a
    conflict instead of being overwritten. And absence is only ever concluded
    within `authoritative_segments`, the scope this file is a complete statement
    of, so a partial master cannot empty a segment it never listed.

    Only a fetched file settles provenance. A hand-carried one is still an
    official master and still confirms presence, but it cannot verify a row or
    contradict one, because nothing about how it reached us is verifiable.
    """
    official = acquisition_mode == "fetched"
    counts = {
        "added": 0, "unchanged": 0, "changed": 0, "reinstated": 0,
        "removed": 0, "stale_rows": 0, "linked": 0,
        "absence_observed": 0, "absence_repeated": 0, "verified": 0, "conflicts": 0,
    }
    seen: set[tuple[str, str, str]] = set()
    keys_in_file = {(record.segment, record.exchange_key) for record in parsed.records}
    with conn:
        for record in parsed.records:
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
                        source_type, verification_status, official_presence,
                        created_at_utc, updated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?,
                               ?, ?, 'confirmed', ?, ?)""",
                    (
                        record.exchange, record.segment, record.exchange_key,
                        record.nse_symbol, record.bse_scrip_code, record.isin,
                        record.name_raw, record.status, effective_date, snapshot_id,
                        effective_date, snapshot_id,
                        "official_automated" if official else "manual_import",
                        "verified" if official else "unverified",
                        now_utc, now_utc,
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
                registry_company_id, opened = _link_or_conflict(
                    conn, exchange_security_id=security_id, record=record,
                    effective_date=effective_date, snapshot_id=snapshot_id,
                    now_utc=now_utc, keys_in_file=keys_in_file,
                )
                counts["conflicts"] += int(opened)
                counts["linked"] += int(registry_company_id is not None)
                continue

            security_id = int(existing["exchange_security_id"])
            if effective_date < str(existing["last_observed_date"]):
                # An older snapshot is retained as evidence but never moves the
                # current pointer backward.
                counts["stale_rows"] += 1
                continue
            existing_source = str(existing["source_type"])
            existing_isin = (
                str(existing["current_isin"]) if existing["current_isin"] else None
            )
            if (
                official
                and existing_source != "official_automated"
                and existing_isin
                and record.isin
                and existing_isin != record.isin
            ):
                # An operator imported this row by hand and the official file
                # disagrees about which instrument it is. Neither value is
                # discarded and nothing is relinked; a human decides.
                if _open_conflict(
                    conn, snapshot_id=snapshot_id, exchange_security_id=security_id,
                    conflict_type="isin_mismatch", exchange=record.exchange,
                    exchange_key=record.exchange_key, existing_value=existing_isin,
                    incoming_value=record.isin, existing_source_type=existing_source,
                    now_utc=now_utc,
                ):
                    counts["conflicts"] += 1
                conn.execute(
                    """UPDATE exchange_securities
                       SET verification_status='conflict', official_presence='confirmed',
                           is_present=1, absence_streak=0, first_absent_date=NULL,
                           last_absent_snapshot_id=NULL, last_observed_date=?,
                           last_observed_snapshot_id=?, updated_at_utc=?
                       WHERE exchange_security_id=?""",
                    (effective_date, snapshot_id, now_utc, security_id),
                )
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
            # A fetched file confirming a row is what verifies it. A row an
            # operator deliberately overrode keeps that marker; only the
            # unverified manual import is promoted.
            promoted = (
                "official_automated"
                if official and existing_source == "manual_import"
                else existing_source
            )
            verification = (
                "verified" if official else str(existing["verification_status"])
            )
            if official and (
                existing_source != promoted
                or str(existing["verification_status"]) != "verified"
                or str(existing["official_presence"]) != "confirmed"
            ):
                _record_event(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    event_date=effective_date, event_type="provenance_verified",
                    from_value=existing_source, to_value=promoted,
                    evidence={"dataset": parsed.dataset}, now_utc=now_utc,
                )
                counts["verified"] += 1
            conn.execute(
                """UPDATE exchange_securities SET current_isin=?, current_name_raw=?,
                   current_status=?, current_version_id=?, is_present=1,
                   last_observed_date=?, last_observed_snapshot_id=?,
                   source_type=?, verification_status=?,
                   official_presence='confirmed', absence_streak=0,
                   first_absent_date=NULL, last_absent_snapshot_id=NULL,
                   updated_at_utc=?
                   WHERE exchange_security_id=?""",
                (
                    record.isin, record.name_raw, record.status, version_id,
                    effective_date, snapshot_id, promoted, verification,
                    now_utc, security_id,
                ),
            )
            registry_company_id, opened = _link_or_conflict(
                conn, exchange_security_id=security_id, record=record,
                effective_date=effective_date, snapshot_id=snapshot_id,
                now_utc=now_utc, keys_in_file=keys_in_file,
            )
            counts["conflicts"] += int(opened)
            counts["linked"] += int(registry_company_id is not None)

        # Securities this master no longer lists are kept, never deleted. One
        # absence is an observation; only a streak of distinct effective dates is
        # a confirmation that the security has left the master. The sweep runs
        # only where this file is authoritative.
        for exchange, segment in sweep_scope(parsed, authoritative_segments):
            for row in conn.execute(
                """SELECT exchange_security_id, exchange_key, last_observed_date,
                          absence_streak, first_absent_date, last_absent_snapshot_id
                   FROM exchange_securities
                   WHERE exchange=? AND segment=? AND is_present=1""",
                (exchange, segment),
            ).fetchall():
                key = (exchange, segment, str(row["exchange_key"]))
                if key in seen or effective_date < str(row["last_observed_date"]):
                    continue
                security_id = int(row["exchange_security_id"])
                if _already_absent_on(
                    conn, row["last_absent_snapshot_id"], effective_date=effective_date
                ):
                    # Re-ingesting the same dated file cannot advance a streak.
                    counts["absence_repeated"] += 1
                    continue
                streak = int(row["absence_streak"] or 0) + 1
                first_absent = str(row["first_absent_date"] or effective_date)
                conn.execute(
                    """UPDATE exchange_securities SET absence_streak=?,
                       first_absent_date=?, last_absent_snapshot_id=?,
                       official_presence='not_confirmed', updated_at_utc=?
                       WHERE exchange_security_id=?""",
                    (streak, first_absent, snapshot_id, now_utc, security_id),
                )
                _record_event(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    event_date=effective_date, event_type="absence_observed",
                    from_value=str(row["exchange_key"]), to_value=str(streak),
                    evidence={
                        "dataset": parsed.dataset,
                        "absence_streak": streak,
                        "first_absent_date": first_absent,
                        "required": EXCHANGE_ABSENCE_CONFIRMATIONS,
                    },
                    now_utc=now_utc,
                )
                counts["absence_observed"] += 1
                if streak < EXCHANGE_ABSENCE_CONFIRMATIONS:
                    continue
                conn.execute(
                    """UPDATE exchange_securities SET is_present=0,
                       current_status='absent_from_master', updated_at_utc=?
                       WHERE exchange_security_id=?""",
                    (now_utc, security_id),
                )
                for event_type in ("absence_confirmed", "removed"):
                    _record_event(
                        conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                        event_date=effective_date, event_type=event_type,
                        from_value=str(row["exchange_key"]), to_value=None,
                        evidence={
                            "dataset": parsed.dataset,
                            "absence_streak": streak,
                            "first_absent_date": first_absent,
                        },
                        now_utc=now_utc,
                    )
                counts["removed"] += 1
    return counts


CONFLICT_RESOLUTIONS = ("accepted_official", "kept_existing", "dismissed")


def open_identity_conflicts(
    conn: sqlite3.Connection, *, exchange_security_id: int | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM exchange_identity_conflicts WHERE resolution='open'"
    parameters: tuple[Any, ...] = ()
    if exchange_security_id is not None:
        query += " AND exchange_security_id=?"
        parameters = (exchange_security_id,)
    return [dict(row) for row in conn.execute(query + " ORDER BY conflict_id", parameters)]


def resolve_identity_conflict(
    conn: sqlite3.Connection,
    *,
    conflict_id: int,
    resolution: str,
    now_utc: str,
) -> dict[str, Any]:
    """Close one identity conflict by an explicit operator decision.

    `accepted_official` is the only resolution that changes registry data, and
    only for the value that was actually disputed. The security stops being
    blocked once no open conflict remains against it.
    """
    if resolution not in CONFLICT_RESOLUTIONS:
        raise ValueError(
            f"resolution must be one of {list(CONFLICT_RESOLUTIONS)}: {resolution!r}"
        )
    row = conn.execute(
        "SELECT * FROM exchange_identity_conflicts WHERE conflict_id=?", (conflict_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown identity conflict: {conflict_id}")
    if str(row["resolution"]) != "open":
        raise ValueError(
            f"Conflict {conflict_id} is already resolved as {row['resolution']!r}"
        )
    security_id = (
        int(row["exchange_security_id"]) if row["exchange_security_id"] is not None else None
    )
    applied = False
    with conn:
        conn.execute(
            """UPDATE exchange_identity_conflicts
               SET resolution=?, resolved_at_utc=? WHERE conflict_id=?""",
            (resolution, now_utc, conflict_id),
        )
        if security_id is not None:
            security = conn.execute(
                "SELECT * FROM exchange_securities WHERE exchange_security_id=?",
                (security_id,),
            ).fetchone()
            if (
                security is not None
                and resolution == "accepted_official"
                and str(row["conflict_type"]) == "isin_mismatch"
                and row["incoming_value"]
            ):
                incoming = str(row["incoming_value"])
                conn.execute(
                    """UPDATE exchange_securities SET current_isin=?, updated_at_utc=?
                       WHERE exchange_security_id=?""",
                    (incoming, now_utc, security_id),
                )
                _record_event(
                    conn, exchange_security_id=security_id, snapshot_id=int(row["snapshot_id"]),
                    event_date=now_utc[:10], event_type="isin_changed",
                    from_value=row["existing_value"], to_value=incoming,
                    evidence={"conflict_id": conflict_id, "resolution": resolution},
                    now_utc=now_utc,
                )
                _link_isin_value(
                    conn, exchange_security_id=security_id,
                    exchange=str(security["exchange"]), isin=incoming,
                    name_raw=str(security["current_name_raw"]),
                    effective_date=str(security["last_observed_date"]), now_utc=now_utc,
                )
                applied = True
            _record_event(
                conn, exchange_security_id=security_id, snapshot_id=int(row["snapshot_id"]),
                event_date=now_utc[:10], event_type="conflict_resolved",
                from_value=row["existing_value"], to_value=row["incoming_value"],
                evidence={
                    "conflict_id": conflict_id,
                    "conflict_type": str(row["conflict_type"]),
                    "resolution": resolution,
                    "applied": applied,
                },
                now_utc=now_utc,
            )
            remaining = conn.execute(
                """SELECT COUNT(*) AS open_count FROM exchange_identity_conflicts
                   WHERE exchange_security_id=? AND resolution='open'""",
                (security_id,),
            ).fetchone()["open_count"]
            if not int(remaining):
                # `dismissed` records no judgement about the identity, so the
                # security is unblocked but not asserted to be verified.
                conn.execute(
                    """UPDATE exchange_securities SET verification_status=?,
                       updated_at_utc=? WHERE exchange_security_id=?""",
                    (
                        "unverified" if resolution == "dismissed" else "verified",
                        now_utc, security_id,
                    ),
                )
    return {
        "conflict_id": conflict_id,
        "conflict_type": str(row["conflict_type"]),
        "resolution": resolution,
        "exchange": str(row["exchange"]),
        "exchange_key": str(row["exchange_key"]),
        "exchange_security_id": security_id,
        "applied_incoming_value": applied,
    }


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
    counts = {"stored": 0, "already_stored": 0, "identity_collisions": 0,
              "after_watermark": 0, "at_or_before_watermark": 0}
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
            if cursor.rowcount == 0:
                existing = conn.execute(
                    """SELECT filing_id, raw_row_json FROM exchange_result_filings
                       WHERE exchange=? AND filing_key=?""",
                    (filing.exchange, filing.filing_key),
                ).fetchone()
                if existing is not None and str(existing["raw_row_json"]) != filing.raw_row_json:
                    collision = conn.execute(
                        """INSERT OR IGNORE INTO exchange_result_filing_collisions
                           (exchange, filing_key, existing_filing_id, snapshot_id,
                            raw_sha256, raw_row_json, first_seen_at_utc)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (filing.exchange, filing.filing_key, int(existing["filing_id"]),
                         snapshot_id, hashlib.sha256(filing.raw_row_json.encode()).hexdigest(),
                         filing.raw_row_json, now_utc),
                    )
                    counts["identity_collisions"] += collision.rowcount
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
    # A security whose identity is disputed never becomes queued work: the
    # company page we would collect might not be the company that filed.
    for security_row in (security, chosen):
        if str(security_row["verification_status"]) == "conflict":
            return {
                "decision": "manual_review",
                "reason": "identity_conflict",
                "registry_company_id": registry_company_id,
                "exchange_security_id": int(security_row["exchange_security_id"]),
            }
    if chosen["nse_symbol"]:
        identifier, basis = str(chosen["nse_symbol"]), "nse_symbol"
    else:
        identifier, basis = str(chosen["bse_scrip_code"]), "bse_scrip_code"
    try:
        # The filing scope is raw exchange provenance. It is deliberately not
        # part of collection identity: one company and one quarter produce one
        # queue item and one Screener scrape.
        path = canonical_company_path(f"/company/{identifier}/")
    except ValueError as exc:
        return {
            "decision": "manual_review",
            "reason": f"unusable_company_path: {exc}",
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
    # The exchange filing scope is immutable provenance, not collection scope.
    # Every filing leads (when resolvable) to the actual standalone Screener
    # statement; there is no consolidated fallback path.
    scope = "standalone"
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
    prior = conn.execute(
        """SELECT earlier.filing_id FROM exchange_result_filings earlier
           JOIN exchange_screener_enqueues decision ON decision.filing_id=earlier.filing_id
           WHERE earlier.exchange=? AND earlier.exchange_key=?
             AND earlier.reporting_scope=? AND earlier.result_period=?
             AND earlier.filing_id<>?
           ORDER BY earlier.broadcast_at_utc DESC, earlier.filing_id DESC LIMIT 1""",
        (filing["exchange"], filing["exchange_key"], filing["reporting_scope"],
         period, filing["filing_id"]),
    ).fetchone()
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
    from .screener_discovery_repo import has_accepted_quarter
    captured = has_accepted_quarter(conn, path, scope, period)
    if accepted is not None and period <= accepted and prior is not None:
        # Distinct official filing event for a period already accepted. A new
        # timestamp alone cannot prove corrected financial content, so hold
        # it for source-grounded revision review instead of silently skipping.
        return {**resolution, "decision": "manual_review",
                "reason": f"possible_same_period_revision:prior_filing={prior['filing_id']}",
                "queue_item_id": None, "result_period": period}
    if captured and accepted is not None and period < accepted:
        return {**resolution, "decision": "stale", "reason": "older_than_accepted",
                "queue_item_id": None, "result_period": period}
    if captured and accepted == period:
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
                        "reporting_scope_policy": "standalone_only",
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
            "standalone",
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
        prior = conn.execute(
            """SELECT 1 FROM exchange_result_filings earlier
               JOIN exchange_screener_enqueues decision ON decision.filing_id=earlier.filing_id
               WHERE earlier.exchange=? AND earlier.exchange_key=?
                 AND earlier.reporting_scope=? AND earlier.result_period=? LIMIT 1""",
            (filing.exchange, filing.exchange_key, filing.reporting_scope,
             filing.result_period),
        ).fetchone()
        if accepted is not None and filing.result_period <= accepted and prior is not None:
            counts["manual_review"] += 1
        elif accepted is not None and filing.result_period < accepted:
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
                    "standalone", str(filing["result_period"]),
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
