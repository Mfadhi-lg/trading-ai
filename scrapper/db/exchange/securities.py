"""Effective-dated exchange securities, corporate changes and identity decisions."""
from __future__ import annotations
from ..repository_backend import (
    atomic_repository, begin_immediate, commit, execute, executemany, generated_id,
    is_postgresql, rollback, statement, transaction,
)

import json
import sqlite3
from typing import Any, Mapping, Sequence

from ...config import EXCHANGE_ABSENCE_CONFIRMATIONS
from ...sources.exchange.corporate_changes import ParsedCorporateChanges
from ...sources.exchange.security_master import ParsedSecurityMaster, SecurityRecord
from ...sources.exchange.snapshot_guard import ScopeMetrics

CONFLICT_RESOLUTIONS = ("accepted_official", "kept_existing", "dismissed")

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

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
        for row in execute(conn,
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


@atomic_repository('financial-repositories')
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
    _json=_json,
) -> None:
    execute(conn,
        """INSERT INTO exchange_security_events
           (exchange_security_id, snapshot_id, event_date, event_type,
            from_value, to_value, evidence_json, recorded_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            exchange_security_id, snapshot_id, event_date, event_type,
            from_value, to_value, _json(dict(evidence or {})), now_utc,
        ),
    )


@atomic_repository('financial-repositories')
def _insert_version(
    conn: sqlite3.Connection,
    *,
    exchange_security_id: int,
    snapshot_id: int,
    record: SecurityRecord,
    effective_date: str,
    now_utc: str,
    _json=_json,
) -> int:
    execute(conn,
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
        execute(conn,
            """SELECT version_id FROM exchange_security_versions
               WHERE exchange_security_id=? AND valid_from_date=?""",
            (exchange_security_id, effective_date),
        ).fetchone()["version_id"]
    )
    execute(conn,
        """UPDATE exchange_security_versions SET valid_to_date=?
           WHERE exchange_security_id=? AND valid_to_date IS NULL AND version_id<>?""",
        (effective_date, exchange_security_id, version_id),
    )
    return version_id


@atomic_repository('financial-repositories')
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
    cursor = execute(conn,
        statement(conn, "INSERT OR IGNORE INTO exchange_identity_conflicts\n           (snapshot_id, exchange_security_id, conflict_type, exchange, exchange_key,\n            existing_value, incoming_value, existing_source_type, resolution,\n            detected_at_utc)\n           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)", "INSERT INTO exchange_identity_conflicts\n           (snapshot_id, exchange_security_id, conflict_type, exchange, exchange_key,\n            existing_value, incoming_value, existing_source_type, resolution,\n            detected_at_utc)\n           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?) ON CONFLICT DO NOTHING"),
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


@atomic_repository('financial-repositories')
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
    execute(conn,
        statement(conn, 'INSERT INTO exchange_company_registry\n           (isin, canonical_name_raw, first_observed_date, last_observed_date,\n            created_at_utc, updated_at_utc)\n           VALUES (?, ?, ?, ?, ?, ?)\n           ON CONFLICT(isin) DO UPDATE SET\n             last_observed_date=MAX(last_observed_date, excluded.last_observed_date),\n             updated_at_utc=excluded.updated_at_utc', 'INSERT INTO exchange_company_registry\n           (isin, canonical_name_raw, first_observed_date, last_observed_date,\n            created_at_utc, updated_at_utc)\n           VALUES (?, ?, ?, ?, ?, ?)\n           ON CONFLICT(isin) DO UPDATE SET\n             last_observed_date=GREATEST(exchange_company_registry.last_observed_date, excluded.last_observed_date),\n             updated_at_utc=excluded.updated_at_utc'),
        (isin, name_raw, effective_date, effective_date, now_utc, now_utc),
    )
    registry_company_id = int(
        execute(conn,
            "SELECT registry_company_id FROM exchange_company_registry WHERE isin=?",
            (isin,),
        ).fetchone()["registry_company_id"]
    )
    if exchange == "NSE":
        # The NSE master carries the security's registered company name; BSE
        # rows carry an abbreviated security name, so they never overwrite it.
        execute(conn,
            """UPDATE exchange_company_registry SET canonical_name_raw=?, updated_at_utc=?
               WHERE registry_company_id=?""",
            (name_raw, now_utc, registry_company_id),
        )
    current = execute(conn,
        """SELECT link_id, registry_company_id FROM exchange_company_links
           WHERE exchange_security_id=? AND is_current=1""",
        (exchange_security_id,),
    ).fetchone()
    if current is not None and int(current["registry_company_id"]) == registry_company_id:
        return registry_company_id
    if current is not None:
        execute(conn,
            """UPDATE exchange_company_links
               SET is_current=0, unlinked_from_date=? WHERE link_id=?""",
            (effective_date, int(current["link_id"])),
        )
    execute(conn,
        statement(conn, "INSERT OR IGNORE INTO exchange_company_links\n           (registry_company_id, exchange_security_id, link_basis, linked_from_date,\n            is_current, created_at_utc)\n           VALUES (?, ?, 'isin', ?, 1, ?)", "INSERT INTO exchange_company_links\n           (registry_company_id, exchange_security_id, link_basis, linked_from_date,\n            is_current, created_at_utc)\n           VALUES (?, ?, 'isin', ?, 1, ?) ON CONFLICT DO NOTHING"),
        (registry_company_id, exchange_security_id, effective_date, now_utc),
    )
    return registry_company_id


def _already_absent_on(
    conn: sqlite3.Connection, last_absent_snapshot_id: Any, *, effective_date: str
) -> bool:
    """True when the streak was last advanced by a file of this same date."""
    if last_absent_snapshot_id is None:
        return False
    row = execute(conn,
        "SELECT effective_date FROM exchange_source_snapshots WHERE snapshot_id=?",
        (int(last_absent_snapshot_id),),
    ).fetchone()
    return row is not None and str(row["effective_date"]) == effective_date


@atomic_repository('financial-repositories')
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
        for row in execute(conn,
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
            execute(conn,
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


@atomic_repository('financial-repositories')
def apply_security_master(
    conn: sqlite3.Connection,
    parsed: ParsedSecurityMaster,
    *,
    snapshot_id: int,
    effective_date: str,
    now_utc: str,
    acquisition_mode: str = "fetched",
    authoritative_segments: Sequence[tuple[str, str]] | None = None,
    EXCHANGE_ABSENCE_CONFIRMATIONS=EXCHANGE_ABSENCE_CONFIRMATIONS,
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
    with transaction(conn):
        for record in parsed.records:
            key = (record.exchange, record.segment, record.exchange_key)
            seen.add(key)
            existing = execute(conn,
                """SELECT * FROM exchange_securities
                   WHERE exchange=? AND segment=? AND exchange_key=?""",
                key,
            ).fetchone()
            if existing is None:
                cursor = execute(conn,
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
                 returning='exchange_security_id')
                security_id = int(generated_id(cursor, 'exchange_security_id'))
                version_id = _insert_version(
                    conn, exchange_security_id=security_id, snapshot_id=snapshot_id,
                    record=record, effective_date=effective_date, now_utc=now_utc,
                )
                execute(conn,
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
                execute(conn,
                    """UPDATE exchange_securities
                       SET verification_status='conflict', official_presence='confirmed',
                           is_present=1, absence_streak=0, first_absent_date=NULL,
                           last_absent_snapshot_id=NULL, last_observed_date=?,
                           last_observed_snapshot_id=?, updated_at_utc=?
                       WHERE exchange_security_id=?""",
                    (effective_date, snapshot_id, now_utc, security_id),
                )
                continue
            current_version = execute(conn,
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
            execute(conn,
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
            for row in execute(conn,
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
                execute(conn,
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
                execute(conn,
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


def open_identity_conflicts(
    conn: sqlite3.Connection, *, exchange_security_id: int | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM exchange_identity_conflicts WHERE resolution='open'"
    parameters: tuple[Any, ...] = ()
    if exchange_security_id is not None:
        query += " AND exchange_security_id=?"
        parameters = (exchange_security_id,)
    return [dict(row) for row in execute(conn, query + " ORDER BY conflict_id", parameters)]


@atomic_repository('financial-repositories')
def resolve_identity_conflict(
    conn: sqlite3.Connection,
    *,
    conflict_id: int,
    resolution: str,
    now_utc: str,
    CONFLICT_RESOLUTIONS=CONFLICT_RESOLUTIONS,
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
    row = execute(conn,
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
    with transaction(conn):
        execute(conn,
            """UPDATE exchange_identity_conflicts
               SET resolution=?, resolved_at_utc=? WHERE conflict_id=?""",
            (resolution, now_utc, conflict_id),
        )
        if security_id is not None:
            security = execute(conn,
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
                execute(conn,
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
            remaining = execute(conn,
                """SELECT COUNT(*) AS open_count FROM exchange_identity_conflicts
                   WHERE exchange_security_id=? AND resolution='open'""",
                (security_id,),
            ).fetchone()["open_count"]
            if not int(remaining):
                # `dismissed` records no judgement about the identity, so the
                # security is unblocked but not asserted to be verified.
                execute(conn,
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


@atomic_repository('financial-repositories')
def apply_corporate_changes(
    conn: sqlite3.Connection,
    parsed: ParsedCorporateChanges,
    *,
    snapshot_id: int,
    now_utc: str,
) -> dict[str, int]:
    counts = {"applied": 0, "unmatched": 0, "reused": 0}
    with transaction(conn):
        for change in parsed.changes:
            security = None
            for hint in change.key_hints:
                security = execute(conn,
                    """SELECT exchange_security_id FROM exchange_securities
                       WHERE exchange=? AND exchange_key=?
                       ORDER BY is_present DESC, exchange_security_id LIMIT 1""",
                    (change.exchange, hint),
                ).fetchone()
                if security is not None:
                    break
            security_id = int(security["exchange_security_id"]) if security else None
            status = "applied" if security_id is not None else "unmatched"
            cursor = execute(conn,
                statement(conn, 'INSERT OR IGNORE INTO exchange_symbol_changes\n                   (snapshot_id, exchange, change_kind, company_name_raw, old_value,\n                    new_value, effective_date, applied_status, exchange_security_id,\n                    recorded_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', 'INSERT INTO exchange_symbol_changes\n                   (snapshot_id, exchange, change_kind, company_name_raw, old_value,\n                    new_value, effective_date, applied_status, exchange_security_id,\n                    recorded_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING'),
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
