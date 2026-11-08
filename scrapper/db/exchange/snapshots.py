"""Exchange source snapshots, immutable response evidence and guard verdicts."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ...config import SNAPSHOTS
from ...core.http import FetchResponse
from ...sources.exchange.snapshot_guard import GuardVerdict, SnapshotBaseline
from ...sources.exchange.tabular import ExcludedRow, RejectedRow
from ..repo import utc_now

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
    utc_now=utc_now,
    DATASET_SUFFIX=DATASET_SUFFIX,
    COMPRESSION_SUFFIX=COMPRESSION_SUFFIX,
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
    _json=_json,
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
    _json=_json,
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
