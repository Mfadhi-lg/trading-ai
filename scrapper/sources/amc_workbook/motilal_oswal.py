"""Motilal Oswal fund-level monthly portfolio workbook adapter (M08)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .ppfas import (
    ClassifiedCell,
    NormalizedHolding,
    PPFASSheet,
    PPFASStructureError,
    PPFASWorkbook,
    WorkbookTable,
    _holding_decimal,
    isin_status,
)
from .workbook import SourceCell, SourceSheet, read_workbook


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifests" / "amc_workbook_motilal_oswal.yml"
MONTH_RE = re.compile(r"^Portfolio as on ([A-Z][a-z]+ \d{1,2}, \d{4})$")
FIELD_COLUMNS = {
    1: "source_security_code", 2: "security_name", 4: "isin",
    5: "instrument_type_or_rating", 6: "quantity", 7: "market_value_lakh",
    8: "percentage_of_nav",
}


def load_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != "1.0.0" or payload.get("publisher") != "motilal_oswal":
        raise RuntimeError("Unsupported Motilal Oswal workbook manifest")
    return payload


def expected_scheme_codes(manifest: dict, month: str) -> tuple[str, ...]:
    return tuple(manifest["scheme_registry"]["schemes"])


def _cells(sheet: SourceSheet) -> dict[tuple[int, int], SourceCell]:
    return {(cell.row_position, cell.column_position): cell for cell in sheet.cells}


def _display(by_position: dict[tuple[int, int], SourceCell], row: int, column: int) -> str | None:
    cell = by_position.get((row, column))
    return None if cell is None else cell.display_value


def _classified(sheet: SourceSheet, table: WorkbookTable) -> tuple[ClassifiedCell, ...]:
    totals = set(table.source_total_rows)
    result = []
    for cell in sheet.cells:
        role = "styled_blank" if cell.display_value in (None, "") else "supporting_disclosure"
        field = None
        if table.start_row <= cell.row_position <= table.end_row:
            role = "primary_portfolio_cell"
            field = FIELD_COLUMNS.get(cell.column_position)
            if cell.row_position == table.header_start_row:
                role = "primary_portfolio_header"
            if cell.row_position in totals:
                role = "source_total_label" if cell.column_position == 2 else "source_total_value"
        if cell.coordinate == "B1":
            role = "scheme_identity"
        elif cell.coordinate == "B2":
            role = "disclosure_month_evidence"
        if cell.formula is not None:
            role = "formula_cached_value"
        result.append(ClassifiedCell(cell, role, field))
    return tuple(result)


def parse_motilal_oswal_workbook(
    path: Path, *, requested_publisher: str, requested_disclosure_month: str
) -> PPFASWorkbook:
    manifest = load_manifest()
    try:
        source = read_workbook(path)
    except (OSError, ValueError) as exc:
        raise PPFASStructureError("workbook_parse_failed", str(exc)) from exc
    if len(source.sheets) != 1 or source.sheets[0].name != "YO20":
        raise PPFASStructureError(
            "sheet_inventory_mismatch", "Motilal Oswal YO20 workbook must have one YO20 sheet",
            details={"sheets": [sheet.name for sheet in source.sheets]},
        )
    sheet = source.sheets[0]
    by_position = _cells(sheet)
    title = _display(by_position, 1, 2)
    if title != "Motilal Oswal Large and Midcap Fund":
        raise PPFASStructureError(
            "publisher_evidence_missing", "Unexpected Motilal Oswal scheme identity",
            details={"title": title},
        )
    evidence = _display(by_position, 2, 2)
    match = MONTH_RE.fullmatch(evidence or "")
    if match is None:
        raise PPFASStructureError("disclosure_month_unrecognized", f"Unrecognized month: {evidence}")
    try:
        detected_month = datetime.strptime(match.group(1), "%B %d, %Y").strftime("%Y-%m")
    except ValueError as exc:
        raise PPFASStructureError("disclosure_month_unrecognized", str(exc)) from exc
    expected_headers = {
        1: "Sr. No.", 2: "Name of Instrument", 4: "ISIN", 5: "Rating/Industry",
        6: "Quantity", 7: "Market Value (In Lakhs)", 8: "% to Net Asset",
    }
    changed = {
        column: _display(by_position, 4, column)
        for column, expected in expected_headers.items()
        if _display(by_position, 4, column) != expected
    }
    if changed:
        raise PPFASStructureError(
            "required_column_missing", "Motilal Oswal required columns changed",
            details={"changed": changed},
        )
    grand_totals = [
        row for (row, column), cell in by_position.items()
        if column == 2 and cell.display_value == "GRAND TOTAL"
    ]
    if len(grand_totals) != 1:
        raise PPFASStructureError(
            "partial_primary_table", "Expected one Motilal Oswal GRAND TOTAL",
            details={"rows": grand_totals},
        )
    end_row = grand_totals[0]
    total_rows = tuple(
        row for (row, column), cell in by_position.items()
        if column == 2 and 4 < row <= end_row and cell.display_value in {"Total", "GRAND TOTAL"}
    )
    table = WorkbookTable(
        0, "primary_portfolio", "Monthly portfolio", 4, end_row, 1, 8, 4, 4,
        total_rows,
        tuple(
            {"column": column, "label": label, "field_code": FIELD_COLUMNS[column]}
            for column, label in expected_headers.items()
        ),
    )
    parsed_sheet = PPFASSheet(
        sheet, "portfolio", "YO20", title, evidence or "", (table,),
        _classified(sheet, table),
    )
    if requested_publisher != "motilal_oswal":
        raise PPFASStructureError(
            "publisher_mismatch",
            f"CLI publisher {requested_publisher!r} does not match detected publisher 'motilal_oswal'",
        )
    if requested_disclosure_month != detected_month:
        raise PPFASStructureError(
            "disclosure_month_mismatch",
            f"CLI month {requested_disclosure_month!r} does not match {detected_month!r}",
        )
    return PPFASWorkbook(
        manifest["manifest_version"], "motilal_oswal", detected_month,
        source.workbook_format, source.date_system, (parsed_sheet,),
    )


def _instrument_code(heading: str | None) -> str:
    value = (heading or "other").casefold()
    if "equity" in value:
        return "equity"
    if any(token in value for token in ("cblo", "repo", "treps", "money market")):
        return "reverse_repo_treps"
    return "other"


def normalize_motilal_oswal_holdings(parsed: PPFASWorkbook) -> tuple[NormalizedHolding, ...]:
    contract = load_manifest()["holdings_normalization"]
    sheet = parsed.sheets[0]
    table = sheet.tables[0]
    by_position = {
        (cell.source.row_position, cell.source.column_position): cell.source
        for cell in sheet.cells
    }
    holdings: list[NormalizedHolding] = []
    heading: str | None = None
    instrument_code = "other"
    for row in range(5, table.end_row):
        displays = {
            column: by_position.get((row, column)).display_value
            if by_position.get((row, column)) is not None else None
            for column in range(1, 9)
        }
        name = (displays[2] or "").strip()
        serial, _, _ = _holding_decimal(displays[1])
        if serial is None:
            if name and name not in {"Total", "GRAND TOTAL", "Net Receivables / (Payables)"}:
                heading = name
                candidate_code = _instrument_code(name)
                if candidate_code != "other":
                    instrument_code = candidate_code
            continue
        isin_raw = displays[4]
        isin, status = isin_status(isin_raw)
        if isin_raw in {"CBLO", "TREPS"}:
            isin, status = None, "missing"
        quantity, quantity_missing, _ = _holding_decimal(displays[6])
        market, market_missing, _ = _holding_decimal(displays[7])
        percentage, percentage_missing, qualifier = _holding_decimal(displays[8])
        instrument_raw = heading or "Other"
        holdings.append(
            NormalizedHolding(
                sheet.source.position, sheet.source.name, table.position,
                table.table_kind, row, parsed.detected_publisher,
                sheet.scheme_name_raw, sheet.scheme_code, parsed.detected_disclosure_month,
                displays[1], name, isin_raw, isin, status, quantity, quantity_missing,
                market, market_missing, percentage, None, percentage_missing, qualifier,
                "net_assets", instrument_raw, instrument_code, displays[5],
                None, "INR", "COUNT", "INR_LAKH", "100000", "PERCENT_POINT",
                contract["normalization_rule_version"],
            )
        )
    return tuple(holdings)
