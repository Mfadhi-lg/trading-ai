"""Transactional persistence for official-workbook inventory through acceptance."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..sources.amc_workbook.detector import WorkbookInspection, safety_report_json
from ..sources.amc_workbook.adapters import adapter_for_parsed, get_workbook_adapter
from ..sources.amc_workbook.ppfas import (
    NormalizedHolding,
    PPFASStructureError,
    PPFASWorkbook,
    expected_scheme_codes,
    load_manifest,
    normalized_security_name,
)


@dataclass(frozen=True)
class PersistedImport:
    import_file_id: int
    workbook_blob_id: int
    import_observation_id: int
    import_state: str
    duplicate: bool


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


@dataclass(frozen=True)
class SchemeRegistrySummary:
    scheme_count: int
    active_equity_scheme_count: int
    classified_holding_count: int
    active_equity_holding_count: int
    classification_rule_version: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ImportFinalization:
    import_state: str
    validation_status: str
    failed_error_rules: tuple[str, ...]
    failed_warning_rules: tuple[str, ...]
    scheme_registry: SchemeRegistrySummary
    superseded_import_file_ids: tuple[int, ...]

    def to_dict(self) -> dict:
        return {
            **self.__dict__,
            "failed_error_rules": list(self.failed_error_rules),
            "failed_warning_rules": list(self.failed_warning_rules),
            "superseded_import_file_ids": list(self.superseded_import_file_ids),
            "scheme_registry": self.scheme_registry.to_dict(),
        }


M02_PASSING_RULES = (
    "ppfas_publisher_matches_cli",
    "ppfas_disclosure_month_matches_cli",
    "ppfas_sheet_inventory_matches_manifest",
    "ppfas_all_sheets_classified",
    "ppfas_required_columns_complete",
    "ppfas_table_boundaries_complete",
    "ppfas_source_cells_complete",
    "ppfas_merged_ranges_captured",
    "ppfas_formulas_preserved",
)
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
M04_VALIDATION_RULES = (
    "m04_prior_contract_complete",
    "m04_prior_blocking_validations_pass",
    "m04_scheme_registry_complete",
    "m04_scheme_classifications_complete",
    "m04_holdings_scheme_links_complete",
)


def persist_inventory(
    conn: sqlite3.Connection,
    inspection: WorkbookInspection,
    *,
    publisher: str,
    disclosure_month: str,
    immutable_path: str,
    observed_at_utc: str,
) -> PersistedImport:
    """Persist one complete inventory or quarantine decision atomically."""
    existing = conn.execute(
        """SELECT import_file_id, workbook_blob_id, import_state
           FROM mf_import_files WHERE publisher=? AND content_sha256=?""",
        (publisher, inspection.content_sha256),
    ).fetchone()
    decision = {
        "decision_code": inspection.decision_code,
        "reason": inspection.reason,
        "requested_disclosure_month": disclosure_month,
        "inventory_status": inspection.inventory_status,
    }
    if existing is not None:
        with conn:
            cursor = conn.execute(
                """INSERT INTO mf_import_observations
                   (import_file_id, observed_at_utc, submitted_path, original_filename,
                    requested_publisher, requested_disclosure_month,
                    observation_result, decision_json)
                   VALUES (?, ?, ?, ?, ?, ?, 'duplicate', ?)""",
                (
                    existing["import_file_id"], observed_at_utc, inspection.path,
                    inspection.original_filename, publisher, disclosure_month,
                    json.dumps(decision, sort_keys=True, separators=(",", ":")),
                ),
            )
        return PersistedImport(
            int(existing["import_file_id"]), int(existing["workbook_blob_id"]),
            int(cursor.lastrowid), str(existing["import_state"]), True,
        )

    state = inspection.proposed_state
    observation_result = "inventoried" if state == "inbox" else "quarantined"
    state_reason = json.dumps(decision, sort_keys=True, separators=(",", ":"))
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO mf_workbook_blobs
               (content_sha256, workbook_type, byte_size, local_path,
                first_observed_at_utc, created_at_utc, safety_status,
                safety_report_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inspection.content_sha256, inspection.workbook_type,
                inspection.byte_size, immutable_path, observed_at_utc,
                observed_at_utc, inspection.safety_status,
                safety_report_json(inspection),
            ),
        )
        blob = conn.execute(
            """SELECT workbook_blob_id FROM mf_workbook_blobs
               WHERE content_sha256=?""",
            (inspection.content_sha256,),
        ).fetchone()
        if blob is None:
            raise RuntimeError("Failed to persist immutable workbook blob")
        cursor = conn.execute(
            """INSERT INTO mf_import_files
               (publisher, requested_disclosure_month, detected_disclosure_month,
                original_filename, submitted_path, content_sha256, workbook_blob_id,
                workbook_type, byte_size, import_state, first_observed_at_utc,
                accepted_at_utc, state_reason_json)
               VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                publisher, disclosure_month, inspection.original_filename,
                inspection.path, inspection.content_sha256, blob["workbook_blob_id"],
                inspection.workbook_type, inspection.byte_size, state,
                observed_at_utc, state_reason,
            ),
        )
        import_file_id = int(cursor.lastrowid)
        for sheet in inspection.sheets:
            conn.execute(
                """INSERT INTO mf_workbook_sheets
                   (import_file_id, sheet_position, sheet_name_raw,
                    visibility_state, package_part, detection_evidence_json,
                    parse_status)
                   VALUES (?, ?, ?, ?, ?, '{}', 'inventoried')""",
                (
                    import_file_id, sheet.position, sheet.name,
                    sheet.visibility_state, sheet.package_part,
                ),
            )
        conn.execute(
            """INSERT INTO mf_import_state_events
               (import_file_id, from_state, to_state, changed_at_utc,
                changed_by, reason_json)
               VALUES (?, NULL, ?, ?, 'm01_importer', ?)""",
            (import_file_id, state, observed_at_utc, state_reason),
        )
        observation = conn.execute(
            """INSERT INTO mf_import_observations
               (import_file_id, observed_at_utc, submitted_path, original_filename,
                requested_publisher, requested_disclosure_month,
                observation_result, decision_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                import_file_id, observed_at_utc, inspection.path,
                inspection.original_filename, publisher, disclosure_month,
                observation_result, state_reason,
            ),
        )
    return PersistedImport(
        import_file_id, int(blob["workbook_blob_id"]),
        int(observation.lastrowid), state, False,
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _table_position(parsed_sheet, row_position: int) -> int | None:
    for table in parsed_sheet.tables:
        if table.start_row <= row_position <= table.end_row:
            return table.position
    return None


def persist_ppfas_structure(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    parsed: PPFASWorkbook,
) -> None:
    """Persist one complete structural parse atomically; formulas stay unevaluated."""
    adapter = adapter_for_parsed(parsed)
    existing_cells = conn.execute(
        """SELECT COUNT(*) FROM mf_workbook_cells AS c
           JOIN mf_workbook_sheets AS s ON s.sheet_id=c.sheet_id
           WHERE s.import_file_id=?""",
        (import_file_id,),
    ).fetchone()[0]
    if existing_cells:
        # Later manifest revisions may add normalized contracts without changing
        # immutable source cells. Advance only the manifest metadata; never
        # duplicate or rewrite the M02 source-faithful inventory.
        with conn:
            conn.execute(
                """UPDATE mf_import_files
                   SET detected_publisher=?, detected_disclosure_month=?,
                       workbook_manifest_version=?
                   WHERE import_file_id=?""",
                (
                    parsed.detected_publisher, parsed.detected_disclosure_month,
                    parsed.manifest_version, import_file_id,
                ),
            )
            conn.execute(
                """UPDATE mf_workbook_sheets SET manifest_version=?
                   WHERE import_file_id=?""",
                (parsed.manifest_version, import_file_id),
            )
        return
    with conn:
        conn.execute(
            """UPDATE mf_import_files
               SET detected_publisher=?, detected_disclosure_month=?,
                   workbook_manifest_version=?, state_reason_json=?
               WHERE import_file_id=?""",
            (
                parsed.detected_publisher, parsed.detected_disclosure_month,
                parsed.manifest_version,
                _json(
                    {
                        "decision_code": f"{parsed.detected_publisher}_structure_complete",
                        "detected_publisher": parsed.detected_publisher,
                        "detected_disclosure_month": parsed.detected_disclosure_month,
                        "manifest_version": parsed.manifest_version,
                    }
                ),
                import_file_id,
            ),
        )
        for parsed_sheet in parsed.sheets:
            row = conn.execute(
                """SELECT sheet_id FROM mf_workbook_sheets
                   WHERE import_file_id=? AND sheet_position=?""",
                (import_file_id, parsed_sheet.source.position),
            ).fetchone()
            if row is None:
                raise RuntimeError("Inventoried workbook sheet disappeared before M02 persistence")
            sheet_id = int(row["sheet_id"])
            primary = next(
                (
                    table for table in parsed_sheet.tables
                    if table.table_kind == "primary_portfolio"
                ),
                None,
            )
            conn.execute(
                """UPDATE mf_workbook_sheets
                   SET detected_scheme_code=?, detected_scheme_name_raw=?,
                       detection_evidence_json=?, parse_status='ok',
                       table_start_row=?, table_end_row=?,
                       sheet_classification=?, manifest_version=?
                   WHERE sheet_id=?""",
                (
                    parsed_sheet.scheme_code, parsed_sheet.scheme_name_raw,
                    _json(
                        {
                            "scheme_title_coordinate": "B1",
                            "disclosure_month_coordinate": "B3",
                            "disclosure_month_display": parsed_sheet.disclosure_month_evidence,
                            "table_count": len(parsed_sheet.tables),
                        }
                    ),
                    None if primary is None else primary.start_row,
                    None if primary is None else primary.end_row,
                    parsed_sheet.classification, parsed.manifest_version, sheet_id,
                ),
            )
            conn.executemany(
                """INSERT INTO mf_workbook_cells
                   (sheet_id, row_position, column_position, coordinate,
                    raw_value, display_value, formula, data_type, number_format,
                    cell_role, normalized_field_code, cell_position, table_position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        sheet_id,
                        item.source.row_position,
                        item.source.column_position,
                        item.source.coordinate,
                        item.source.raw_value,
                        item.source.display_value,
                        item.source.formula,
                        item.source.data_type,
                        item.source.number_format,
                        item.cell_role,
                        item.normalized_field_code,
                        position,
                        _table_position(parsed_sheet, item.source.row_position),
                    )
                    for position, item in enumerate(parsed_sheet.cells)
                ),
            )
            conn.executemany(
                """INSERT INTO mf_workbook_merged_ranges
                   (sheet_id, merge_position, range_reference, start_row,
                    start_column, end_row, end_column)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        sheet_id, position, item.reference, item.start_row,
                        item.start_column, item.end_row, item.end_column,
                    )
                    for position, item in enumerate(parsed_sheet.source.merged_ranges)
                ),
            )
            conn.executemany(
                """INSERT INTO mf_workbook_tables
                   (sheet_id, table_position, table_kind, heading_raw,
                    start_row, end_row, start_column, end_column,
                    header_start_row, header_end_row, source_total_rows_json,
                    required_columns_json, detection_evidence_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        sheet_id, table.position, table.table_kind, table.heading_raw,
                        table.start_row, table.end_row, table.start_column,
                        table.end_column, table.header_start_row, table.header_end_row,
                        _json(table.source_total_rows), _json(table.required_columns),
                        _json({"manifest_version": parsed.manifest_version}),
                    )
                    for table in parsed_sheet.tables
                ),
            )
        inventory = parsed.inventory_dict()
        details = {
            "manifest_version": parsed.manifest_version,
            "detected_publisher": parsed.detected_publisher,
            "detected_disclosure_month": parsed.detected_disclosure_month,
            "sheet_count": inventory["sheet_count"],
            "table_count": inventory["table_count"],
            "cell_count": inventory["cell_count"],
            "merged_range_count": inventory["merged_range_count"],
            "formula_count": inventory["formula_count"],
        }
        conn.executemany(
            """INSERT INTO mf_workbook_validation_results
               (import_file_id, rule_code, severity, passed, details_json)
               VALUES (?, ?, 'error', 1, ?)""",
            (
                (import_file_id, rule, _json(details))
                for rule in adapter.structure_validation_rules
            ),
        )


def persist_ppfas_structure_failure(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    error: PPFASStructureError,
) -> None:
    imported = conn.execute(
        "SELECT publisher FROM mf_import_files WHERE import_file_id=?",
        (import_file_id,),
    ).fetchone()
    publisher = "workbook" if imported is None else str(imported["publisher"])
    rule_code = f"{publisher}_structure_complete"
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO mf_workbook_validation_results
               (validation_id, import_file_id, rule_code, severity, passed, details_json)
               VALUES (
                 (SELECT validation_id FROM mf_workbook_validation_results
                  WHERE import_file_id=? AND rule_code=?),
                 ?, ?, 'error', 0, ?
               )""",
            (
                import_file_id, rule_code, import_file_id, rule_code,
                _json({"error_code": error.code, "message": str(error), **error.details}),
            ),
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


def _resolved_classification(
    conn: sqlite3.Connection, scheme_id: int, disclosure_month: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM mf_scheme_classifications
           WHERE scheme_id=? AND valid_from_month<=?
             AND (valid_to_month IS NULL OR valid_to_month>=?)
           ORDER BY scheme_classification_id DESC LIMIT 1""",
        (scheme_id, disclosure_month, disclosure_month),
    ).fetchone()


def persist_scheme_registry(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    parsed: PPFASWorkbook,
) -> SchemeRegistrySummary:
    """Persist append-only names/classifications and link every normalized row."""
    adapter = adapter_for_parsed(parsed)
    manifest = adapter.load_manifest()
    registry = manifest["scheme_registry"]
    declared = registry["schemes"]
    observed_at = conn.execute(
        "SELECT first_observed_at_utc FROM mf_import_files WHERE import_file_id=?",
        (import_file_id,),
    ).fetchone()
    if observed_at is None:
        raise RuntimeError("Cannot classify schemes for a missing import")
    created_at = str(observed_at["first_observed_at_utc"])
    resolved: dict[str, tuple[int, sqlite3.Row]] = {}
    with conn:
        for sheet in parsed.sheets:
            policy = declared.get(sheet.scheme_code)
            if policy is None:
                continue
            scheme = conn.execute(
                "SELECT scheme_id, current_name FROM mf_schemes WHERE publisher=? AND scheme_code=?",
                (parsed.detected_publisher, sheet.scheme_code),
            ).fetchone()
            if scheme is None:
                cursor = conn.execute(
                    """INSERT INTO mf_schemes(publisher, scheme_code, current_name)
                       VALUES (?, ?, ?)""",
                    (
                        parsed.detected_publisher, sheet.scheme_code,
                        sheet.scheme_name_raw,
                    ),
                )
                scheme_id = int(cursor.lastrowid)
            else:
                scheme_id = int(scheme["scheme_id"])
                if str(scheme["current_name"]) != sheet.scheme_name_raw:
                    conn.execute(
                        "UPDATE mf_schemes SET current_name=? WHERE scheme_id=?",
                        (sheet.scheme_name_raw, scheme_id),
                    )
            conn.execute(
                """INSERT OR IGNORE INTO mf_scheme_name_versions
                   (scheme_id, scheme_name_raw, valid_from_month, valid_to_month,
                    naming_source, naming_rule_version, first_seen_import_file_id,
                    created_at_utc)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    scheme_id, sheet.scheme_name_raw,
                    parsed.detected_disclosure_month,
                    registry["classification_source"],
                    registry["naming_rule_version"], import_file_id, created_at,
                ),
            )
            conn.execute(
                """INSERT OR IGNORE INTO mf_scheme_classifications
                   (scheme_id, scheme_management_type, is_equity_scheme,
                    scheme_category, valid_from_month, valid_to_month,
                    classification_source, classification_rule_version,
                    first_seen_import_file_id, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    scheme_id, policy["scheme_management_type"],
                    int(policy["is_equity_scheme"]), policy["scheme_category"],
                    policy.get("valid_from_month", registry["valid_from_month"]),
                    registry["classification_source"],
                    registry["classification_rule_version"], import_file_id,
                    created_at,
                ),
            )
            classification = _resolved_classification(
                conn, scheme_id, parsed.detected_disclosure_month
            )
            if classification is not None:
                resolved[sheet.scheme_code] = (scheme_id, classification)
                conn.execute(
                    """UPDATE mf_holdings
                       SET scheme_id=?, scheme_category=?
                       WHERE import_file_id=? AND scheme_code=?""",
                    (
                        scheme_id, str(classification["scheme_category"]),
                        import_file_id, sheet.scheme_code,
                    ),
                )

    active_codes = {
        code
        for code, (_, classification) in resolved.items()
        if classification["scheme_management_type"] == "active"
        and int(classification["is_equity_scheme"]) == 1
    }
    holding_counts = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(scheme_id IS NOT NULL AND scheme_category IS NOT NULL) AS classified
           FROM mf_holdings WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    active_holding_count = 0
    if active_codes:
        placeholders = ",".join("?" for _ in active_codes)
        active_holding_count = int(
            conn.execute(
                f"""SELECT COUNT(*) FROM mf_holdings
                     WHERE import_file_id=? AND scheme_code IN ({placeholders})""",
                (import_file_id, *sorted(active_codes)),
            ).fetchone()[0]
        )
    return SchemeRegistrySummary(
        scheme_count=len(resolved),
        active_equity_scheme_count=len(active_codes),
        classified_holding_count=int(holding_counts["classified"] or 0),
        active_equity_holding_count=active_holding_count,
        classification_rule_version=str(registry["classification_rule_version"]),
    )


def _transition_import_state(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    to_state: str,
    changed_at_utc: str,
    changed_by: str,
    reason: dict,
) -> bool:
    current = conn.execute(
        "SELECT import_state FROM mf_import_files WHERE import_file_id=?",
        (import_file_id,),
    ).fetchone()
    if current is None:
        raise RuntimeError("Cannot transition a missing workbook import")
    from_state = str(current["import_state"])
    if from_state == to_state:
        return False
    allowed = {
        "inbox": {"accepted", "rejected", "quarantine"},
        "rejected": {"accepted", "quarantine"},
        "accepted": set(),
        "quarantine": set(),
    }
    if to_state not in allowed[from_state]:
        raise RuntimeError(f"Invalid mutual-fund import transition: {from_state}->{to_state}")
    reason_json = _json(reason)
    conn.execute(
        """UPDATE mf_import_files
           SET import_state=?, accepted_at_utc=CASE WHEN ?='accepted' THEN ? ELSE NULL END,
               state_reason_json=?
           WHERE import_file_id=?""",
        (to_state, to_state, changed_at_utc, reason_json, import_file_id),
    )
    conn.execute(
        """INSERT INTO mf_import_state_events
           (import_file_id, from_state, to_state, changed_at_utc, changed_by,
            reason_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            import_file_id, from_state, to_state, changed_at_utc, changed_by,
            reason_json,
        ),
    )
    return True


def finalize_ppfas_import(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    parsed: PPFASWorkbook,
    decided_at_utc: str,
) -> ImportFinalization:
    """Validate, classify, and atomically accept or reject one complete workbook."""
    adapter = adapter_for_parsed(parsed)
    registry_summary = persist_scheme_registry(
        conn, import_file_id=import_file_id, parsed=parsed
    )
    expected_prior = set(adapter.structure_validation_rules) | set(M03_VALIDATION_RULES)
    prior_rows = conn.execute(
        """SELECT rule_code, severity, passed
           FROM mf_workbook_validation_results
           WHERE import_file_id=? AND rule_code NOT LIKE 'm04_%'""",
        (import_file_id,),
    ).fetchall()
    prior_codes = {str(row["rule_code"]) for row in prior_rows}
    prior_errors_pass = all(
        int(row["passed"]) == 1
        for row in prior_rows
        if str(row["severity"]) == "error"
    )
    sheet_codes = {
        str(row["detected_scheme_code"])
        for row in conn.execute(
            """SELECT detected_scheme_code FROM mf_workbook_sheets
               WHERE import_file_id=? AND sheet_classification='portfolio'""",
            (import_file_id,),
        )
        if row["detected_scheme_code"] is not None
    }
    manifest = adapter.load_manifest()
    manifest_codes = set(
        adapter.expected_scheme_codes(manifest, parsed.detected_disclosure_month)
    )
    holding_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM mf_holdings WHERE import_file_id=?",
            (import_file_id,),
        ).fetchone()[0]
    )
    classifications_complete = (
        registry_summary.scheme_count == len(sheet_codes) == len(manifest_codes)
    )
    links_complete = (
        holding_count > 0
        and registry_summary.classified_holding_count == holding_count
        and conn.execute(
            """SELECT COUNT(*) FROM mf_holdings
               WHERE import_file_id=? AND scheme_id IS NULL""",
            (import_file_id,),
        ).fetchone()[0] == 0
    )
    validation_details = {
        "expected_prior_rule_count": len(expected_prior),
        "present_prior_rule_count": len(prior_codes & expected_prior),
        "sheet_scheme_codes": sorted(sheet_codes),
        "manifest_scheme_codes": sorted(manifest_codes),
        "holding_count": holding_count,
        **registry_summary.to_dict(),
    }
    rules = (
        ("m04_prior_contract_complete", expected_prior.issubset(prior_codes)),
        ("m04_prior_blocking_validations_pass", prior_errors_pass),
        ("m04_scheme_registry_complete", sheet_codes == manifest_codes),
        ("m04_scheme_classifications_complete", classifications_complete),
        ("m04_holdings_scheme_links_complete", links_complete),
    )
    with conn:
        for rule_code, passed in rules:
            _upsert_m03_validation(
                conn, import_file_id, rule_code, severity="error",
                passed=passed, details=validation_details,
            )

    failed_errors = tuple(
        str(row["rule_code"])
        for row in conn.execute(
            """SELECT rule_code FROM mf_workbook_validation_results
               WHERE import_file_id=? AND severity='error' AND passed=0
               ORDER BY rule_code""",
            (import_file_id,),
        )
    )
    failed_warnings = tuple(
        str(row["rule_code"])
        for row in conn.execute(
            """SELECT rule_code FROM mf_workbook_validation_results
               WHERE import_file_id=? AND severity='warning' AND passed=0
               ORDER BY rule_code""",
            (import_file_id,),
        )
    )
    if failed_errors:
        reason = {
            "decision_code": "deterministic_validation_failed",
            "failed_error_rules": list(failed_errors),
            "failed_warning_rules": list(failed_warnings),
        }
        with conn:
            _transition_import_state(
                conn, import_file_id=import_file_id, to_state="rejected",
                changed_at_utc=decided_at_utc, changed_by="m04_acceptance_workflow",
                reason=reason,
            )
        return ImportFinalization(
            "rejected", "failed", failed_errors, failed_warnings,
            registry_summary, (),
        )

    validation_status = "passed_with_warnings" if failed_warnings else "passed"
    imported = conn.execute(
        """SELECT publisher, detected_disclosure_month, import_state
           FROM mf_import_files WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    if imported is None or imported["detected_disclosure_month"] is None:
        raise RuntimeError("Accepted workbook is missing detected source identity")
    publisher = str(imported["publisher"])
    disclosure_month = str(imported["detected_disclosure_month"])
    schemes = conn.execute(
        """SELECT DISTINCT scheme_id FROM mf_holdings
           WHERE import_file_id=? ORDER BY scheme_id""",
        (import_file_id,),
    ).fetchall()
    superseded: set[int] = set()
    with conn:
        if str(imported["import_state"]) != "accepted":
            for scheme_row in schemes:
                scheme_id = int(scheme_row["scheme_id"])
                classification = _resolved_classification(
                    conn, scheme_id, disclosure_month
                )
                if classification is None:
                    raise RuntimeError("Scheme classification disappeared during acceptance")
                previous = conn.execute(
                    """SELECT import_file_id FROM mf_accepted_versions
                       WHERE publisher=? AND scheme_id=? AND disclosure_month=?""",
                    (publisher, scheme_id, disclosure_month),
                ).fetchone()
                previous_id = int(previous["import_file_id"]) if previous else None
                if previous_id is not None and previous_id != import_file_id:
                    superseded.add(previous_id)
                conn.execute(
                    """INSERT OR IGNORE INTO mf_accepted_version_events
                       (publisher, scheme_id, disclosure_month, import_file_id,
                        previous_import_file_id, scheme_classification_id,
                        accepted_at_utc, accepted_by, acceptance_mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'm04_acceptance_workflow',
                               'automatic_complete_contract')""",
                    (
                        publisher, scheme_id, disclosure_month, import_file_id,
                        previous_id, int(classification["scheme_classification_id"]),
                        decided_at_utc,
                    ),
                )
                conn.execute(
                    """INSERT INTO mf_accepted_versions
                       (publisher, scheme_id, disclosure_month, import_file_id,
                        accepted_at_utc, accepted_by, acceptance_mode)
                       VALUES (?, ?, ?, ?, ?, 'm04_acceptance_workflow',
                               'automatic_complete_contract')
                       ON CONFLICT(publisher, scheme_id, disclosure_month) DO UPDATE SET
                         import_file_id=excluded.import_file_id,
                         accepted_at_utc=excluded.accepted_at_utc,
                         accepted_by=excluded.accepted_by,
                         acceptance_mode=excluded.acceptance_mode""",
                    (
                        publisher, scheme_id, disclosure_month, import_file_id,
                        decided_at_utc,
                    ),
                )
            reason = {
                "decision_code": "complete_contract_accepted",
                "validation_status": validation_status,
                "failed_warning_rules": list(failed_warnings),
                "scheme_count": len(schemes),
                "superseded_import_file_ids": sorted(superseded),
            }
            _transition_import_state(
                conn, import_file_id=import_file_id, to_state="accepted",
                changed_at_utc=decided_at_utc, changed_by="m04_acceptance_workflow",
                reason=reason,
            )
            conn.execute(
                """UPDATE mf_holdings SET accepted_at_utc=?
                   WHERE import_file_id=?""",
                (decided_at_utc, import_file_id),
            )
            conn.execute(
                """INSERT INTO mf_workbook_acceptance_events
                   (import_file_id, decided_at_utc, decided_by, acceptance_mode,
                    validation_status, scheme_count,
                    previous_import_file_ids_json, decision_json)
                   VALUES (?, ?, 'm04_acceptance_workflow',
                           'automatic_complete_contract', ?, ?, ?, ?)""",
                (
                    import_file_id, decided_at_utc, validation_status, len(schemes),
                    _json(sorted(superseded)), _json(reason),
                ),
            )
    return ImportFinalization(
        "accepted", validation_status, (), failed_warnings, registry_summary,
        tuple(sorted(superseded)),
    )
