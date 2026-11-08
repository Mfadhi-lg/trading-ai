"""Transactional holdings normalization, security identities and match evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ...sources.amc_workbook.adapters import get_workbook_adapter
from ...sources.amc_workbook.ppfas import NormalizedHolding, normalized_security_name
from .workbook_inventory import _json


@dataclass(frozen=True)
class HoldingsNormalizationSummary:
    holding_count: int
    primary_holding_count: int
    derivative_holding_count: int
    valid_isin_count: int
    missing_isin_count: int
    invalid_isin_count: int
    isin_match_count: int
    name_fallback_match_count: int
    unmatched_count: int
    manual_review_count: int
    security_count: int
    validation_rule_count: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()

M03_VALIDATION_RULE_COUNT = 8

M03_VALIDATION_RULES = (
    "m03_holding_rows_reconciled",
    "m03_holding_numeric_values",
    "m03_holding_units_declared",
    "m03_isin_structural_validity",
    "m03_security_match_decisions_complete",
    "m03_instrument_types_known",
    "m03_unmatched_excluded_from_features",
    "m03_name_fallback_unambiguous",
)

def _upsert_m03_validation(
    conn: sqlite3.Connection,
    import_file_id: int,
    rule_code: str,
    *,
    severity: str,
    passed: bool,
    details: dict,
) -> None:
    conn.execute(
        """INSERT INTO mf_workbook_validation_results
           (import_file_id, rule_code, severity, passed, details_json)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(import_file_id, rule_code) DO UPDATE SET
             severity=excluded.severity,
             passed=excluded.passed,
             details_json=excluded.details_json""",
        (import_file_id, rule_code, severity, int(passed), _json(details)),
    )

def _security_for_isin(
    conn: sqlite3.Connection,
    *,
    holding: NormalizedHolding,
    import_file_id: int,
    observed_at_utc: str,
) -> int:
    found = conn.execute(
        """SELECT security_id FROM mf_security_identifiers
           WHERE identifier_type='isin' AND identifier_value=?""",
        (holding.isin_normalized,),
    ).fetchone()
    if found is not None:
        return int(found["security_id"])
    security = conn.execute(
        """INSERT INTO mf_securities(canonical_name, created_at_utc)
           VALUES (?, ?)""",
        (holding.security_name_raw, observed_at_utc),
    )
    security_id = int(security.lastrowid)
    conn.execute(
        """INSERT INTO mf_security_identifiers
           (security_id, identifier_type, identifier_value, structurally_valid,
            first_seen_import_file_id, first_seen_disclosure_month, created_at_utc)
           VALUES (?, 'isin', ?, 1, ?, ?, ?)""",
        (
            security_id, holding.isin_normalized, import_file_id,
            holding.disclosure_month, observed_at_utc,
        ),
    )
    return security_id

def persist_ppfas_holdings(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    holdings: tuple[NormalizedHolding, ...],
) -> HoldingsNormalizationSummary:
    """Persist complete M03 normalization and initial match decisions atomically."""
    existing = conn.execute(
        "SELECT COUNT(*) FROM mf_holdings WHERE import_file_id=?",
        (import_file_id,),
    ).fetchone()[0]
    if existing:
        import_state = str(
            conn.execute(
                "SELECT import_state FROM mf_import_files WHERE import_file_id=?",
                (import_file_id,),
            ).fetchone()["import_state"]
        )
        if import_state == "accepted":
            summary = holdings_normalization_summary(conn, import_file_id)
            with conn:
                conn.execute(
                    """UPDATE mf_workbook_validation_results SET details_json=?
                       WHERE import_file_id=? AND rule_code LIKE 'm03_%'""",
                    (_json(summary.to_dict()), import_file_id),
                )
            return summary
        # Rejected/inbox imports are derived state and may be reprocessed after
        # an evidenced manifest revision. Immutable workbook cells stay intact.
        with conn:
            conn.execute(
                "DELETE FROM mf_security_name_evidence WHERE import_file_id=?",
                (import_file_id,),
            )
            conn.execute(
                "DELETE FROM mf_holdings WHERE import_file_id=?",
                (import_file_id,),
            )
            conn.execute(
                """DELETE FROM mf_workbook_validation_results
                   WHERE import_file_id=? AND (rule_code LIKE 'm03_%'
                                               OR rule_code LIKE 'm04_%')""",
                (import_file_id,),
            )
    imported = conn.execute(
        """SELECT publisher, detected_disclosure_month, first_observed_at_utc
           FROM mf_import_files WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    if imported is None:
        raise RuntimeError("Cannot normalize holdings for a missing import")
    sheet_rows = conn.execute(
        """SELECT sheet_id, sheet_position, sheet_classification
           FROM mf_workbook_sheets WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchall()
    sheet_ids = {
        int(row["sheet_position"]): int(row["sheet_id"]) for row in sheet_rows
    }
    portfolio_positions = {
        int(row["sheet_position"])
        for row in sheet_rows
        if row["sheet_classification"] == "portfolio"
    }
    if portfolio_positions != {item.source_sheet_position for item in holdings}:
        raise RuntimeError("M03 holdings do not reconcile to persisted workbook sheets")
    observed_at = str(imported["first_observed_at_utc"])
    adapter = get_workbook_adapter(str(imported["publisher"]))
    if adapter is None:
        raise RuntimeError(f"No adapter for publisher {imported['publisher']!r}")
    manifest = adapter.load_manifest()
    isin_rule_version = manifest["holdings_normalization"]["isin_rule_version"]
    name_rule_version = manifest["holdings_normalization"]["name_fallback_rule_version"]
    isin_security: dict[tuple[int, int, int], int] = {}

    with conn:
        # Create/reuse identities only from structurally valid ISINs. Names never
        # create a security identity in this pass.
        for item in holdings:
            if item.isin_validation_status != "valid":
                continue
            key = (
                item.source_sheet_position, item.source_table_position, item.source_row
            )
            security_id = _security_for_isin(
                conn, holding=item, import_file_id=import_file_id,
                observed_at_utc=observed_at,
            )
            isin_security[key] = security_id
            conn.execute(
                """INSERT OR IGNORE INTO mf_security_name_evidence
                   (security_id, normalized_name, security_name_raw, import_file_id,
                    sheet_id, source_row, evidence_basis, rule_version,
                    observed_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, 'valid_isin', ?, ?)""",
                (
                    security_id, normalized_security_name(item.security_name_raw),
                    item.security_name_raw, import_file_id,
                    sheet_ids[item.source_sheet_position], item.source_row,
                    name_rule_version, observed_at,
                ),
            )

        name_candidates: dict[str, tuple[int, ...]] = {}
        for row in conn.execute(
            """SELECT normalized_name, security_id
               FROM mf_security_name_evidence
               GROUP BY normalized_name, security_id
               ORDER BY normalized_name, security_id"""
        ):
            name = str(row["normalized_name"])
            name_candidates[name] = (
                *name_candidates.get(name, ()), int(row["security_id"])
            )

        for item in holdings:
            key = (
                item.source_sheet_position, item.source_table_position, item.source_row
            )
            match_key: str | None = None
            candidates: tuple[int, ...] = ()
            if item.isin_validation_status == "valid":
                security_id = isin_security[key]
                match_method = "isin"
                match_status = "matched"
                match_key = item.isin_normalized
                decision_rule_version = isin_rule_version
                evidence = {"basis": "structurally_valid_isin"}
            elif item.isin_validation_status.startswith("invalid"):
                security_id = None
                match_method = "none"
                match_status = "invalid_isin"
                match_key = item.isin_normalized
                decision_rule_version = isin_rule_version
                evidence = {"basis": item.isin_validation_status}
            elif item.source_table_kind != "primary_portfolio":
                security_id = None
                match_method = "none"
                match_status = "unmatched"
                decision_rule_version = name_rule_version
                evidence = {"basis": "derivative_has_no_isin"}
            else:
                match_key = normalized_security_name(item.security_name_raw)
                candidates = name_candidates.get(match_key, ())
                if len(candidates) == 1:
                    security_id = candidates[0]
                    match_method = "exact_normalized_name"
                    match_status = "matched"
                    decision_rule_version = name_rule_version
                    evidence = {"basis": "single_valid_isin_anchored_name"}
                elif len(candidates) > 1:
                    security_id = None
                    match_method = "exact_normalized_name"
                    match_status = "manual_review"
                    decision_rule_version = name_rule_version
                    evidence = {"basis": "ambiguous_valid_isin_anchored_name"}
                else:
                    security_id = None
                    match_method = "none"
                    match_status = "unmatched"
                    decision_rule_version = name_rule_version
                    evidence = {"basis": "no_valid_isin_anchored_name"}
            cursor = conn.execute(
                """INSERT INTO mf_holdings
                   (import_file_id, sheet_id, source_table_position,
                    source_table_kind, source_row, publisher, source,
                    source_quality, scheme_name_raw, scheme_code,
                    disclosure_month, source_security_code_raw,
                    security_name_raw, isin_raw, isin_normalized,
                    isin_validation_status, quantity_decimal,
                    quantity_missing_kind, market_value_decimal,
                    market_value_missing_kind, percentage_of_nav_decimal,
                    percentage_of_aum_decimal, percentage_missing_kind,
                    percentage_qualifier, percentage_basis,
                    instrument_type_raw, instrument_type_code,
                    industry_or_rating_raw, position_direction_raw, currency,
                    quantity_unit_code, market_value_unit_code,
                    market_value_scale_multiplier, percentage_unit_code,
                    normalization_rule_version, first_observed_at_utc,
                    accepted_at_utc, security_id, security_match_status)
                   VALUES (?, ?, ?, ?, ?, ?, 'amc_workbook', 'official', ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    import_file_id, sheet_ids[item.source_sheet_position],
                    item.source_table_position, item.source_table_kind,
                    item.source_row, item.publisher, item.scheme_name_raw,
                    item.scheme_code, item.disclosure_month,
                    item.source_security_code_raw, item.security_name_raw,
                    item.isin_raw, item.isin_normalized,
                    item.isin_validation_status, item.quantity_decimal,
                    item.quantity_missing_kind, item.market_value_decimal,
                    item.market_value_missing_kind,
                    item.percentage_of_nav_decimal,
                    item.percentage_of_aum_decimal,
                    item.percentage_missing_kind, item.percentage_qualifier,
                    item.percentage_basis, item.instrument_type_raw,
                    item.instrument_type_code, item.industry_or_rating_raw,
                    item.position_direction_raw, item.currency,
                    item.quantity_unit_code, item.market_value_unit_code,
                    item.market_value_scale_multiplier,
                    item.percentage_unit_code, item.normalization_rule_version,
                    observed_at, security_id, match_status,
                ),
            )
            conn.execute(
                """INSERT INTO mf_security_match_decisions
                   (holding_id, decision_version, is_current, security_id,
                    match_method, match_status, match_key,
                    rule_version, candidate_security_ids_json, evidence_json,
                    decided_at_utc, decided_by)
                   VALUES (?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'm03_normalizer')""",
                (
                    int(cursor.lastrowid), security_id, match_method, match_status,
                    match_key, decision_rule_version, _json(candidates),
                    _json(evidence), observed_at,
                ),
            )

        summary = holdings_normalization_summary(conn, import_file_id)
        details = summary.to_dict()
        details["validation_rule_count"] = M03_VALIDATION_RULE_COUNT
        rules = (
            (
                "m03_holding_rows_reconciled", "error",
                summary.holding_count == len(holdings),
            ),
            (
                "m03_holding_numeric_values", "error",
                all(
                    item.market_value_missing_kind is None
                    and item.percentage_missing_kind is None
                    and item.quantity_missing_kind != "parse_error"
                    for item in holdings
                ),
            ),
            (
                "m03_holding_units_declared", "error",
                all(
                    item.currency == "INR"
                    and item.market_value_unit_code == "INR_LAKH"
                    and item.market_value_scale_multiplier == "100000"
                    and item.percentage_unit_code == "PERCENT_POINT"
                    for item in holdings
                ),
            ),
            (
                "m03_isin_structural_validity", "error",
                summary.invalid_isin_count == 0,
            ),
            (
                "m03_security_match_decisions_complete", "error",
                conn.execute(
                    """SELECT COUNT(*) FROM mf_security_match_decisions AS d
                       JOIN mf_holdings AS h ON h.holding_id=d.holding_id
                       WHERE h.import_file_id=? AND d.is_current=1""",
                    (import_file_id,),
                ).fetchone()[0] == len(holdings),
            ),
            (
                "m03_instrument_types_known", "error",
                all(item.instrument_type_code != "unknown" for item in holdings),
            ),
            (
                "m03_unmatched_excluded_from_features", "error",
                conn.execute(
                    """SELECT COUNT(*) FROM mf_holdings
                       WHERE import_file_id=? AND security_match_status!='matched'
                         AND security_id IS NOT NULL""",
                    (import_file_id,),
                ).fetchone()[0] == 0,
            ),
            (
                "m03_name_fallback_unambiguous", "warning",
                summary.manual_review_count == 0,
            ),
        )
        for rule_code, severity, passed in rules:
            _upsert_m03_validation(
                conn, import_file_id, rule_code, severity=severity,
                passed=passed, details=details,
            )
    return holdings_normalization_summary(conn, import_file_id)

def holdings_normalization_summary(
    conn: sqlite3.Connection, import_file_id: int
) -> HoldingsNormalizationSummary:
    counts = conn.execute(
        """SELECT COUNT(*) AS holding_count,
                  SUM(source_table_kind='primary_portfolio') AS primary_count,
                  SUM(source_table_kind='derivatives') AS derivative_count,
                  SUM(isin_validation_status='valid') AS valid_isin_count,
                  SUM(isin_validation_status='missing') AS missing_isin_count,
                  SUM(isin_validation_status IN ('invalid_format','invalid_checksum'))
                    AS invalid_isin_count,
                  SUM(security_match_status='unmatched') AS unmatched_count,
                  SUM(security_match_status='manual_review') AS manual_review_count
           FROM mf_holdings WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    decisions = conn.execute(
        """SELECT SUM(d.match_method='isin' AND d.match_status='matched') AS isin_matches,
                  SUM(d.match_method='exact_normalized_name' AND d.match_status='matched')
                    AS name_matches
           FROM mf_security_match_decisions AS d
           JOIN mf_holdings AS h ON h.holding_id=d.holding_id
           WHERE h.import_file_id=? AND d.is_current=1""",
        (import_file_id,),
    ).fetchone()
    return HoldingsNormalizationSummary(
        int(counts["holding_count"] or 0), int(counts["primary_count"] or 0),
        int(counts["derivative_count"] or 0),
        int(counts["valid_isin_count"] or 0),
        int(counts["missing_isin_count"] or 0),
        int(counts["invalid_isin_count"] or 0),
        int(decisions["isin_matches"] or 0), int(decisions["name_matches"] or 0),
        int(counts["unmatched_count"] or 0),
        int(counts["manual_review_count"] or 0),
        int(conn.execute("SELECT COUNT(*) FROM mf_securities").fetchone()[0]),
        int(conn.execute(
            """SELECT COUNT(*) FROM mf_workbook_validation_results
               WHERE import_file_id=? AND rule_code LIKE 'm03_%'""",
            (import_file_id,),
        ).fetchone()[0]),
    )
