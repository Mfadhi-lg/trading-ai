"""What the first automated refresh did to a hand-built registry.

The registry starts as operator-imported rows. The moment an official file is
fetched unattended, somebody has to be able to answer: what matched, what is
genuinely new, what did the exchange decline to confirm, and where do the two
disagree. This report answers exactly that, and refuses to be quiet about the
disagreements — it exits non-zero while any identity conflict is still open, so
it works as the gate C7's scheduler is only turned on behind.

Deterministic and read-only: it derives everything from stored evidence and
writes one JSON document.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from ..config import EXPORTS
from ..sources.exchange.datasets import SECURITY_MASTER, datasets_of_kind


SCHEMA_VERSION = "1.0.0"
SECURITY_MASTER_DATASETS = tuple(
    config.key for config in datasets_of_kind(SECURITY_MASTER)
)


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


def _rows(conn: sqlite3.Connection, sql: str, *parameters: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, parameters)]


def _counts(conn: sqlite3.Connection, sql: str, *parameters: Any) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in conn.execute(sql, parameters)}


def scope_snapshots(
    conn: sqlite3.Connection, snapshot_id: int | None = None
) -> list[int]:
    """The snapshots this report is about.

    One explicit snapshot when asked for; otherwise the latest accepted snapshot
    of every security master, which is the state the registry currently reflects.
    """
    if snapshot_id is not None:
        return [snapshot_id]
    if not SECURITY_MASTER_DATASETS:
        return []
    rows = conn.execute(
        f"""SELECT snapshot_id FROM exchange_source_snapshots AS snapshot
            WHERE snapshot.validation_status IN ('passed', 'overridden')
              AND snapshot.dataset IN ({_placeholders(SECURITY_MASTER_DATASETS)})
              AND snapshot.snapshot_id = (
                SELECT other.snapshot_id FROM exchange_source_snapshots AS other
                WHERE other.dataset = snapshot.dataset
                  AND other.validation_status IN ('passed', 'overridden')
                ORDER BY other.effective_date DESC, other.snapshot_id DESC LIMIT 1)
            ORDER BY snapshot.snapshot_id""",
        SECURITY_MASTER_DATASETS,
    ).fetchall()
    return [int(row["snapshot_id"]) for row in rows]


def _securities(
    conn: sqlite3.Connection, snapshots: Sequence[int], *, first_seen_here: bool,
    exchange: str | None = None,
) -> list[dict[str, Any]]:
    """Securities these snapshots brought in, or confirmed without introducing.

    "New" is a fact about the snapshot that first observed the security, so it
    stays answerable for any snapshot, including an old one. "Matched" is a
    statement about the registry as it stands now — these snapshots are the ones
    currently holding the row — so it is only meaningful for current state.
    """
    if not snapshots:
        return []
    marks = _placeholders(snapshots)
    if first_seen_here:
        condition = f"first_observed_snapshot_id IN ({marks})"
        parameters: tuple[Any, ...] = tuple(snapshots)
    else:
        condition = (
            f"last_observed_snapshot_id IN ({marks})"
            f" AND first_observed_snapshot_id NOT IN ({marks})"
        )
        parameters = tuple(snapshots) + tuple(snapshots)
    query = f"""SELECT exchange, segment, exchange_key, nse_symbol, bse_scrip_code,
                       current_isin, current_name_raw, current_status, source_type,
                       verification_status, first_observed_date
                FROM exchange_securities
                WHERE {condition}"""
    if exchange is not None:
        query += " AND exchange=?"
        parameters += (exchange,)
    return _rows(conn, query + " ORDER BY exchange, segment, exchange_key", *parameters)


def build_exchange_reconciliation_report(
    conn: sqlite3.Connection, snapshot_id: int | None = None
) -> dict[str, Any]:
    snapshots = scope_snapshots(conn, snapshot_id)
    marks = _placeholders(snapshots) if snapshots else "NULL"
    scoped: tuple[Any, ...] = tuple(snapshots)

    snapshot_rows = (
        _rows(
            conn,
            f"""SELECT snapshot.snapshot_id, snapshot.dataset, snapshot.exchange,
                       snapshot.effective_date, snapshot.acquisition_mode,
                       snapshot.compression, snapshot.accepted_row_count,
                       snapshot.rejected_row_count, snapshot.excluded_row_count,
                       snapshot.validation_status, validation.findings_json
                FROM exchange_source_snapshots AS snapshot
                LEFT JOIN exchange_snapshot_validations AS validation
                  USING (snapshot_id)
                WHERE snapshot.snapshot_id IN ({marks})
                ORDER BY snapshot.dataset, snapshot.snapshot_id""",
            *scoped,
        )
        if snapshots
        else []
    )
    guard_findings = [
        {
            "snapshot_id": item["snapshot_id"],
            "dataset": item["dataset"],
            "validation_status": item["validation_status"],
            "findings": json.loads(str(item.pop("findings_json") or "[]")),
        }
        for item in snapshot_rows
    ]

    matched = _securities(conn, snapshots, first_seen_here=False)
    new_nse = _securities(conn, snapshots, first_seen_here=True, exchange="NSE")
    new_bse = _securities(conn, snapshots, first_seen_here=True, exchange="BSE")

    # A hand-imported row no official file has confirmed. This is the list that
    # matters most on a first run: either the operator's row is real and the
    # scope of the fetched file is wrong, or the row should never have existed.
    unconfirmed_manual = _rows(
        conn,
        """SELECT exchange, segment, exchange_key, current_isin, current_name_raw,
                  source_type, verification_status, absence_streak,
                  first_absent_date, first_observed_date, last_observed_date
           FROM exchange_securities
           WHERE source_type IN ('manual_import', 'manual_override')
             AND official_presence='not_confirmed'
           ORDER BY absence_streak DESC, exchange, exchange_key""",
    )

    conflicts = {
        conflict_type: _rows(
            conn,
            """SELECT conflict_id, snapshot_id, exchange_security_id, exchange,
                      exchange_key, existing_value, incoming_value,
                      existing_source_type, detected_at_utc
               FROM exchange_identity_conflicts
               WHERE resolution='open' AND conflict_type=?
               ORDER BY conflict_id""",
            conflict_type,
        )
        for conflict_type in (
            "isin_mismatch", "scrip_code_mismatch", "duplicate_isin", "segment_mismatch",
        )
    }
    open_conflict_count = sum(len(items) for items in conflicts.values())

    rejected = (
        _counts(
            conn,
            f"""SELECT reason_code, COUNT(*) FROM exchange_source_rejected_rows
                WHERE snapshot_id IN ({marks}) GROUP BY 1 ORDER BY 1""",
            *scoped,
        )
        if snapshots
        else {}
    )
    excluded = (
        _counts(
            conn,
            f"""SELECT instrument_class, COUNT(*) FROM exchange_source_excluded_rows
                WHERE snapshot_id IN ({marks}) GROUP BY 1 ORDER BY 1""",
            *scoped,
        )
        if snapshots
        else {}
    )
    provenance = _counts(
        conn,
        """SELECT source_type || '_' || verification_status, COUNT(*)
           FROM exchange_securities GROUP BY 1 ORDER BY 1""",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope_snapshot_ids": snapshots,
        "reconciliation_status": "blocked" if open_conflict_count else "clear",
        "summary": {
            "snapshot_count": len(snapshots),
            "matched_existing_count": len(matched),
            "new_nse_security_count": len(new_nse),
            "new_bse_exclusive_security_count": len(new_bse),
            "unconfirmed_manual_count": len(unconfirmed_manual),
            "open_conflict_count": open_conflict_count,
            "open_conflict_counts_by_type": {
                key: len(items) for key, items in conflicts.items()
            },
            "rejected_rows_by_reason": rejected,
            "excluded_rows_by_instrument_class": excluded,
            "securities_by_provenance": provenance,
        },
        "snapshots": snapshot_rows,
        "guard_findings": guard_findings,
        "matched_existing": matched,
        "new_nse_securities": new_nse,
        "new_bse_exclusive_securities": new_bse,
        "unconfirmed_manual_records": unconfirmed_manual,
        "open_conflicts": conflicts,
    }


def write_exchange_reconciliation_report(
    conn: sqlite3.Connection,
    output: Path | None = None,
    snapshot_id: int | None = None,
) -> Path:
    output = output or EXPORTS / "exchange-reconciliation-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_exchange_reconciliation_report(conn, snapshot_id),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output
