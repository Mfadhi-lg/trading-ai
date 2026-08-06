"""Persistence and official-first reconciliation for M09 mfdata evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from ..sources.mfdata.adapter import MFDataSnapshot


@dataclass(frozen=True)
class MFDataCrosscheckSummary:
    mfdata_snapshot_id: int
    mfdata_observation_id: int
    mfdata_crosscheck_run_id: int
    duplicate: bool
    comparison_status: str
    official_observation_count: int
    third_party_observation_count: int
    matched_count: int
    conflict_count: int
    official_only_count: int
    third_party_only_count: int
    unmatched_third_party_count: int
    official_precedence_enforced: bool = True

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sum_exact(rows: list[sqlite3.Row], field: str) -> str | None:
    values = [row[field] for row in rows]
    if not values or any(value is None for value in values):
        return None
    return format(sum((Decimal(str(value)) for value in values), Decimal("0")), "f")


def _different(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left != right
    return Decimal(left) != Decimal(right)


def _scheme(
    conn: sqlite3.Connection, *, publisher: str, scheme_code: str
) -> sqlite3.Row:
    row = conn.execute(
        """SELECT scheme_id, publisher, scheme_code FROM mf_schemes
           WHERE publisher=? AND scheme_code=?""",
        (publisher, scheme_code),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"mfdata mapping requires an existing official scheme: "
            f"{publisher}/{scheme_code}"
        )
    return row


def _persist_snapshot(
    conn: sqlite3.Connection,
    *,
    parsed: MFDataSnapshot,
    raw_bytes: bytes,
    publisher: str,
    scheme_code: str,
    submitted_path: str,
    immutable_path: str,
    observed_at_utc: str,
) -> tuple[int, int, bool]:
    scheme = _scheme(conn, publisher=publisher, scheme_code=scheme_code)
    scheme_id = int(scheme["scheme_id"])
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    existing = conn.execute(
        """SELECT * FROM mfdata_snapshots
           WHERE provider='mfdata' AND content_sha256=?""",
        (content_hash,),
    ).fetchone()
    if existing is not None:
        identity = (
            str(existing["publisher"]), int(existing["scheme_id"]),
            str(existing["scheme_code"]), int(existing["provider_family_id"]),
            str(existing["detected_disclosure_month"]),
        )
        requested = (
            publisher, scheme_id, scheme_code, parsed.provider_family_id,
            parsed.disclosure_month,
        )
        if identity != requested:
            raise ValueError(
                "Identical mfdata bytes cannot be remapped to another official identity"
            )
        observation = conn.execute(
            """INSERT INTO mfdata_snapshot_observations
               (mfdata_snapshot_id, observed_at_utc, submitted_path,
                observation_result)
               VALUES (?, ?, ?, 'duplicate')""",
            (int(existing["mfdata_snapshot_id"]), observed_at_utc, submitted_path),
        )
        return int(existing["mfdata_snapshot_id"]), int(observation.lastrowid), True

    snapshot = conn.execute(
        """INSERT INTO mfdata_snapshots
           (provider, source, source_quality, publisher, scheme_id, scheme_code,
            provider_family_id, requested_disclosure_month,
            detected_disclosure_month, response_url, content_sha256, byte_size,
            submitted_path, immutable_path, parser_version, parse_status,
            first_observed_at_utc, accepted_at_utc)
           VALUES ('mfdata', 'mfdata', 'third_party_unverified', ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)""",
        (
            publisher, scheme_id, scheme_code, parsed.provider_family_id,
            parsed.disclosure_month, parsed.disclosure_month, parsed.response_url,
            content_hash, len(raw_bytes), submitted_path, immutable_path,
            parsed.parser_version, observed_at_utc, observed_at_utc,
        ),
    )
    snapshot_id = int(snapshot.lastrowid)
    observation = conn.execute(
        """INSERT INTO mfdata_snapshot_observations
           (mfdata_snapshot_id, observed_at_utc, submitted_path,
            observation_result)
           VALUES (?, ?, ?, 'accepted')""",
        (snapshot_id, observed_at_utc, submitted_path),
    )
    for holding in parsed.holdings:
        security_id = None
        match_status = "invalid_isin"
        if holding.isin_validation_status == "valid":
            security = conn.execute(
                """SELECT security_id FROM mf_security_identifiers
                   WHERE identifier_type='isin' AND identifier_value=?""",
                (holding.isin_normalized,),
            ).fetchone()
            if security is not None:
                security_id = int(security["security_id"])
                match_status = "matched"
            else:
                match_status = "unmatched"
        elif holding.isin_validation_status == "missing":
            match_status = "unmatched"
        conn.execute(
            """INSERT INTO mfdata_holdings
               (mfdata_snapshot_id, source_position, holding_type,
                holding_type_position, publisher, scheme_id, scheme_code,
                provider_family_id, disclosure_month, source, source_quality,
                security_name_raw, isin_raw, isin_normalized,
                isin_validation_status, quantity_display, quantity_decimal,
                market_value_cr_display, market_value_cr_decimal,
                market_value_lakh_decimal, percentage_of_nav_display,
                percentage_of_nav_decimal, change_mom_display,
                change_mom_decimal, currency, quantity_unit_code,
                market_value_source_unit_code,
                market_value_normalized_unit_code, percentage_unit_code,
                source_row_json, security_id, security_match_status,
                normalization_rule_version, first_observed_at_utc,
                accepted_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'mfdata',
                       'third_party_unverified', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, 'INR', 'COUNT', 'INR_CRORE', 'INR_LAKH',
                       'PERCENT_POINT', ?, ?, ?, 'mfdata_holdings_v1', ?, ?)""",
            (
                snapshot_id, holding.source_position, holding.holding_type,
                holding.holding_type_position, publisher, scheme_id, scheme_code,
                parsed.provider_family_id, parsed.disclosure_month,
                holding.security_name_raw, holding.isin_raw,
                holding.isin_normalized, holding.isin_validation_status,
                holding.quantity_display, holding.quantity_decimal,
                holding.market_value_cr_display,
                holding.market_value_cr_decimal,
                holding.market_value_lakh_decimal,
                holding.percentage_of_nav_display,
                holding.percentage_of_nav_decimal, holding.change_mom_display,
                holding.change_mom_decimal, holding.source_row_json, security_id,
                match_status, observed_at_utc, observed_at_utc,
            ),
        )
    previous = conn.execute(
        """SELECT mfdata_snapshot_id FROM mfdata_accepted_versions
           WHERE publisher=? AND scheme_id=? AND disclosure_month=?""",
        (publisher, scheme_id, parsed.disclosure_month),
    ).fetchone()
    previous_id = None if previous is None else int(previous["mfdata_snapshot_id"])
    conn.execute(
        """INSERT INTO mfdata_accepted_versions
           (publisher, scheme_id, disclosure_month, mfdata_snapshot_id,
            accepted_at_utc, accepted_by)
           VALUES (?, ?, ?, ?, ?, 'm09_mfdata_adapter')
           ON CONFLICT(publisher, scheme_id, disclosure_month) DO UPDATE SET
             mfdata_snapshot_id=excluded.mfdata_snapshot_id,
             accepted_at_utc=excluded.accepted_at_utc,
             accepted_by=excluded.accepted_by""",
        (publisher, scheme_id, parsed.disclosure_month, snapshot_id, observed_at_utc),
    )
    conn.execute(
        """INSERT INTO mfdata_accepted_version_events
           (publisher, scheme_id, disclosure_month, mfdata_snapshot_id,
            previous_mfdata_snapshot_id, accepted_at_utc, accepted_by)
           VALUES (?, ?, ?, ?, ?, ?, 'm09_mfdata_adapter')""",
        (
            publisher, scheme_id, parsed.disclosure_month, snapshot_id,
            previous_id, observed_at_utc,
        ),
    )
    return snapshot_id, int(observation.lastrowid), False


def _crosscheck(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    publisher: str,
    scheme_code: str,
    generated_at_utc: str,
) -> tuple[int, dict[str, int | str]]:
    snapshot = conn.execute(
        "SELECT * FROM mfdata_snapshots WHERE mfdata_snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if snapshot is None:
        raise ValueError(f"Unknown mfdata snapshot: {snapshot_id}")
    scheme_id = int(snapshot["scheme_id"])
    month = str(snapshot["detected_disclosure_month"])
    official_rows = conn.execute(
        """SELECT holding_id, security_id, security_name_raw, isin_normalized,
                  quantity_decimal, market_value_decimal,
                  percentage_of_nav_decimal
           FROM mf_accepted_holdings
           WHERE publisher=? AND scheme_id=? AND disclosure_month=?
             AND source_table_kind='primary_portfolio'
             AND security_id IS NOT NULL AND security_match_status='matched'
             AND isin_validation_status='valid'
           ORDER BY isin_normalized, holding_id""",
        (publisher, scheme_id, month),
    ).fetchall()
    third_rows = conn.execute(
        """SELECT * FROM mfdata_holdings
           WHERE mfdata_snapshot_id=? AND holding_type='equity'
           ORDER BY source_position""",
        (snapshot_id,),
    ).fetchall()
    fingerprint_payload = {
        "snapshot_id": snapshot_id,
        "content_sha256": snapshot["content_sha256"],
        "official": [dict(row) for row in official_rows],
        "third_party": [
            {
                key: row[key]
                for key in (
                    "mfdata_holding_id", "security_id", "isin_normalized",
                    "quantity_decimal", "market_value_lakh_decimal",
                    "percentage_of_nav_decimal", "security_match_status",
                )
            }
            for row in third_rows
        ],
    }
    fingerprint = hashlib.sha256(_json(fingerprint_payload).encode()).hexdigest()
    existing = conn.execute(
        """SELECT * FROM mfdata_crosscheck_runs WHERE input_fingerprint=?""",
        (fingerprint,),
    ).fetchone()
    if existing is not None:
        return int(existing["mfdata_crosscheck_run_id"]), dict(existing)

    official_by_isin: dict[str, list[sqlite3.Row]] = {}
    for row in official_rows:
        official_by_isin.setdefault(str(row["isin_normalized"]), []).append(row)
    third_by_isin = {
        str(row["isin_normalized"]): row
        for row in third_rows
        if row["security_match_status"] == "matched"
        and row["isin_validation_status"] == "valid"
    }
    unmatched = [
        row for row in third_rows
        if row["security_match_status"] != "matched"
        or row["isin_validation_status"] != "valid"
    ]

    results: list[dict] = []
    counts = {
        "matched_count": 0,
        "conflict_count": 0,
        "official_only_count": 0,
        "third_party_only_count": 0,
        "unmatched_third_party_count": len(unmatched),
    }
    for isin in sorted(set(official_by_isin) | set(third_by_isin)):
        official = official_by_isin.get(isin, [])
        third = third_by_isin.get(isin)
        official_quantity = _sum_exact(official, "quantity_decimal")
        official_market = _sum_exact(official, "market_value_decimal")
        official_weight = _sum_exact(official, "percentage_of_nav_decimal")
        if official and third is not None:
            comparisons = (
                ("quantity", official_quantity, third["quantity_decimal"]),
                ("market_value_lakh", official_market, third["market_value_lakh_decimal"]),
                ("percentage_of_nav", official_weight, third["percentage_of_nav_decimal"]),
            )
            conflicts = {
                field: {"official": left, "mfdata": right}
                for field, left, right in comparisons
                if _different(left, right)
            }
            status = "conflict" if conflicts else "match"
            counts[f"{status}ed_count" if status == "match" else "conflict_count"] += 1
            preferred_source = "amc_workbook"
            preferred_quality = "official"
            name = str(official[0]["security_name_raw"])
            security_id = int(official[0]["security_id"])
        elif official:
            status = "official_only"
            counts["official_only_count"] += 1
            conflicts = {}
            preferred_source = "amc_workbook"
            preferred_quality = "official"
            name = str(official[0]["security_name_raw"])
            security_id = int(official[0]["security_id"])
        else:
            status = "third_party_only"
            counts["third_party_only_count"] += 1
            conflicts = {}
            preferred_source = "mfdata"
            preferred_quality = "third_party_unverified"
            name = str(third["security_name_raw"])
            security_id = int(third["security_id"])
        results.append({
            "result_status": status,
            "security_id": security_id,
            "isin_normalized": isin,
            "security_name_raw": name,
            "official_holding_ids_json": _json(
                [int(row["holding_id"]) for row in official]
            ),
            "mfdata_holding_id": None if third is None else int(third["mfdata_holding_id"]),
            "official_quantity_decimal": official_quantity,
            "mfdata_quantity_decimal": None if third is None else third["quantity_decimal"],
            "official_market_value_lakh_decimal": official_market,
            "mfdata_market_value_lakh_decimal": (
                None if third is None else third["market_value_lakh_decimal"]
            ),
            "official_percentage_of_nav_decimal": official_weight,
            "mfdata_percentage_of_nav_decimal": (
                None if third is None else third["percentage_of_nav_decimal"]
            ),
            "conflict_fields_json": _json(conflicts),
            "preferred_source": preferred_source,
            "preferred_source_quality": preferred_quality,
        })
    for third in unmatched:
        results.append({
            "result_status": "unmatched_third_party",
            "security_id": None,
            "isin_normalized": third["isin_normalized"],
            "security_name_raw": third["security_name_raw"],
            "official_holding_ids_json": "[]",
            "mfdata_holding_id": int(third["mfdata_holding_id"]),
            "official_quantity_decimal": None,
            "mfdata_quantity_decimal": third["quantity_decimal"],
            "official_market_value_lakh_decimal": None,
            "mfdata_market_value_lakh_decimal": third["market_value_lakh_decimal"],
            "official_percentage_of_nav_decimal": None,
            "mfdata_percentage_of_nav_decimal": third["percentage_of_nav_decimal"],
            "conflict_fields_json": "{}",
            "preferred_source": None,
            "preferred_source_quality": None,
        })
    warning_count = (
        counts["conflict_count"] + counts["official_only_count"]
        + counts["third_party_only_count"] + counts["unmatched_third_party_count"]
    )
    comparison_status = "passed_with_warnings" if warning_count else "passed"
    run = conn.execute(
        """INSERT INTO mfdata_crosscheck_runs
           (mfdata_snapshot_id, publisher, scheme_id, disclosure_month,
            comparison_status, official_observation_count,
            third_party_observation_count, matched_count, conflict_count,
            official_only_count, third_party_only_count,
            unmatched_third_party_count, official_precedence_enforced,
            input_fingerprint, generated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            snapshot_id, publisher, scheme_id, month, comparison_status,
            len(official_by_isin), len(third_rows), counts["matched_count"],
            counts["conflict_count"], counts["official_only_count"],
            counts["third_party_only_count"], counts["unmatched_third_party_count"],
            fingerprint, generated_at_utc,
        ),
    )
    run_id = int(run.lastrowid)
    for position, result in enumerate(results):
        conn.execute(
            """INSERT INTO mfdata_crosscheck_results
               (mfdata_crosscheck_run_id, result_position, result_status,
                security_id, isin_normalized, security_name_raw,
                official_holding_ids_json, mfdata_holding_id,
                official_quantity_decimal, mfdata_quantity_decimal,
                official_market_value_lakh_decimal,
                mfdata_market_value_lakh_decimal,
                official_percentage_of_nav_decimal,
                mfdata_percentage_of_nav_decimal, conflict_fields_json,
                preferred_source, preferred_source_quality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, position, result["result_status"], result["security_id"],
                result["isin_normalized"], result["security_name_raw"],
                result["official_holding_ids_json"], result["mfdata_holding_id"],
                result["official_quantity_decimal"], result["mfdata_quantity_decimal"],
                result["official_market_value_lakh_decimal"],
                result["mfdata_market_value_lakh_decimal"],
                result["official_percentage_of_nav_decimal"],
                result["mfdata_percentage_of_nav_decimal"],
                result["conflict_fields_json"], result["preferred_source"],
                result["preferred_source_quality"],
            ),
        )
    persisted = conn.execute(
        "SELECT * FROM mfdata_crosscheck_runs WHERE mfdata_crosscheck_run_id=?",
        (run_id,),
    ).fetchone()
    return run_id, dict(persisted)


def persist_and_crosscheck_mfdata(
    conn: sqlite3.Connection,
    *,
    parsed: MFDataSnapshot,
    raw_bytes: bytes,
    publisher: str,
    scheme_code: str,
    submitted_path: str,
    immutable_path: str,
    observed_at_utc: str,
) -> MFDataCrosscheckSummary:
    """Persist complete evidence, then compare it without mutating official rows."""
    with conn:
        snapshot_id, observation_id, duplicate = _persist_snapshot(
            conn,
            parsed=parsed,
            raw_bytes=raw_bytes,
            publisher=publisher,
            scheme_code=scheme_code,
            submitted_path=submitted_path,
            immutable_path=immutable_path,
            observed_at_utc=observed_at_utc,
        )
        run_id, run = _crosscheck(
            conn,
            snapshot_id=snapshot_id,
            publisher=publisher,
            scheme_code=scheme_code,
            generated_at_utc=observed_at_utc,
        )
    return MFDataCrosscheckSummary(
        mfdata_snapshot_id=snapshot_id,
        mfdata_observation_id=observation_id,
        mfdata_crosscheck_run_id=run_id,
        duplicate=duplicate,
        comparison_status=str(run["comparison_status"]),
        official_observation_count=int(run["official_observation_count"]),
        third_party_observation_count=int(run["third_party_observation_count"]),
        matched_count=int(run["matched_count"]),
        conflict_count=int(run["conflict_count"]),
        official_only_count=int(run["official_only_count"]),
        third_party_only_count=int(run["third_party_only_count"]),
        unmatched_third_party_count=int(run["unmatched_third_party_count"]),
    )
