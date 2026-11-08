"""Transactional workbook inventory, source-cell lineage and parse outcomes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ...sources.amc_workbook.detector import WorkbookInspection, safety_report_json
from ...sources.amc_workbook.adapters import adapter_for_parsed
from ...sources.amc_workbook.ppfas import PPFASStructureError, PPFASWorkbook


@dataclass(frozen=True)
class PersistedImport:
    import_file_id: int
    workbook_blob_id: int
    import_observation_id: int
    import_state: str
    duplicate: bool

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
