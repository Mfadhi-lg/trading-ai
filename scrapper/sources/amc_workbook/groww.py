"""Groww consolidated monthly portfolio workbook adapter (M08)."""

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


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifests" / "amc_workbook_groww.yml"
TITLE_RE = re.compile(r"^(IB\d{2})-(Groww .+)$", re.IGNORECASE)
MONTH_RE = re.compile(r"^Portfolio as on (\d{2}-[A-Z]{3}-\d{4})$")
TOTAL_LABELS = {
    "sub total", "total", "grand total", "net receivables / (payables)",
    "net current assets", "other current assets / (liabilities)", "nil",
}
FIELD_COLUMNS = {
    2: "isin",
    3: "security_name",
    4: "instrument_type_or_rating",
    5: "quantity",
    6: "market_value_lakh",
    7: "percentage_of_nav",
    8: "maturity_date",
    9: "put_call_option",
    10: "yield",
}


def load_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != "1.0.0" or payload.get("publisher") != "groww":
        raise RuntimeError("Unsupported Groww workbook manifest")
    active_equity = set(payload["scheme_registry"]["active_equity_scheme_codes"])
    for code, policy in payload["scheme_registry"]["schemes"].items():
        name = policy["scheme_name"].casefold()
        if "fof" in name:
            management_type = "fund_of_funds"
        elif re.search(r"\betf\b", name):
            management_type = "ETF"
        elif "index" in name:
            management_type = "index"
        elif "arbitrage" in name:
            management_type = "arbitrage"
        else:
            management_type = "active"
        policy.update(
            scheme_management_type=management_type,
            is_equity_scheme=code in active_equity,
            scheme_category=f"groww_{code.casefold()}",
        )
    return payload


def expected_scheme_codes(manifest: dict, month: str) -> tuple[str, ...]:
    return tuple(
        code for code, policy in manifest["scheme_registry"]["schemes"].items()
        if policy.get("valid_from_month", "0000-01") <= month
        and (policy.get("valid_to_month") is None or policy["valid_to_month"] >= month)
    )


def _cells(sheet: SourceSheet) -> dict[tuple[int, int], SourceCell]:
    return {(cell.row_position, cell.column_position): cell for cell in sheet.cells}


def _display(by_position: dict[tuple[int, int], SourceCell], row: int, column: int) -> str | None:
    cell = by_position.get((row, column))
    return None if cell is None else cell.display_value


def _month(value: str | None, sheet_name: str) -> str:
    match = MONTH_RE.fullmatch(value or "")
    if match is None:
        raise PPFASStructureError(
            "disclosure_month_unrecognized",
            f"Unrecognized disclosure month on Groww sheet {sheet_name}",
            details={"sheet": sheet_name, "display_value": value},
        )
    try:
        return datetime.strptime(match.group(1), "%d-%b-%Y").strftime("%Y-%m")
    except ValueError as exc:
        raise PPFASStructureError("disclosure_month_unrecognized", str(exc)) from exc


def _classify_cells(sheet: SourceSheet, table: WorkbookTable | None) -> tuple[ClassifiedCell, ...]:
    result: list[ClassifiedCell] = []
    for cell in sheet.cells:
        role = "styled_blank" if cell.display_value in (None, "") else "supporting_disclosure"
        field = None
        if table is not None and table.start_row <= cell.row_position <= table.end_row:
            role = "primary_portfolio_cell"
            field = FIELD_COLUMNS.get(cell.column_position)
            if cell.row_position == table.header_start_row:
                role = "primary_portfolio_header"
            if cell.row_position in table.source_total_rows:
                role = "source_total_label" if cell.column_position == 3 else "source_total_value"
        if cell.coordinate == "B1":
            role = "scheme_identity"
        elif cell.coordinate == "B2":
            role = "disclosure_month_evidence"
        if cell.formula is not None:
            role = "formula_cached_value"
        result.append(ClassifiedCell(cell, role, field))
    return tuple(result)


def parse_groww_workbook(
    path: Path, *, requested_publisher: str, requested_disclosure_month: str
) -> PPFASWorkbook:
    manifest = load_manifest()
    try:
        source = read_workbook(path)
    except (OSError, ValueError) as exc:
        raise PPFASStructureError("workbook_parse_failed", str(exc)) from exc

    parsed_sheets: list[PPFASSheet] = []
    detected_months: set[str] = set()
    detected_codes: set[str] = set()
    for sheet in source.sheets:
        by_position = _cells(sheet)
        title = _display(by_position, 1, 2)
        title_match = TITLE_RE.fullmatch(title or "")
        if title_match is None:
            if sheet.name == "XDO_METADATA" and sheet.visibility_state == "hidden":
                parsed_sheets.append(
                    PPFASSheet(
                        sheet, "non_portfolio", "__XDO_METADATA__", "XDO metadata",
                        "not_applicable", (), _classify_cells(sheet, None),
                    )
                )
                continue
            raise PPFASStructureError(
                "publisher_evidence_missing",
                f"Groww scheme identity is missing on sheet {sheet.name}",
                details={"sheet": sheet.name, "title": title},
            )
        scheme_code, scheme_name = title_match.group(1).upper(), title_match.group(2)
        month_evidence = _display(by_position, 2, 2)
        detected_month = _month(month_evidence, sheet.name)
        detected_months.add(detected_month)
        detected_codes.add(scheme_code)

        expected_headers = {
            2: "ISIN", 3: "Name of Instrument", 4: "Rating/Industry",
            5: "Quantity", 6: "Market Value (In Rs. lakh)", 7: "% To Net Assets",
        }
        changed = {
            column: _display(by_position, 3, column)
            for column, expected in expected_headers.items()
            if _display(by_position, 3, column) != expected
        }
        if changed:
            raise PPFASStructureError(
                "required_column_missing",
                f"Groww required columns changed on {sheet.name}",
                details={"sheet": sheet.name, "changed": changed},
            )
        grand_totals = [
            row for (row, column), cell in by_position.items()
            if column == 3 and (cell.display_value or "").strip().casefold() == "grand total"
        ]
        if len(grand_totals) != 1 or grand_totals[0] <= 3:
            raise PPFASStructureError(
                "partial_primary_table",
                f"Expected one complete Groww GRAND TOTAL on {sheet.name}",
                details={"sheet": sheet.name, "rows": grand_totals},
            )
        end_row = grand_totals[0]
        total_rows = tuple(
            row for (row, column), cell in by_position.items()
            if column == 3 and 3 < row <= end_row
            and (cell.display_value or "").strip().casefold() in TOTAL_LABELS
        )
        table = WorkbookTable(
            0, "primary_portfolio", "Monthly portfolio", 3, end_row, 2, 10,
            3, 3, total_rows,
            tuple(
                {"column": column, "label": label, "field_code": FIELD_COLUMNS[column]}
                for column, label in expected_headers.items()
            ),
        )
        parsed_sheets.append(
            PPFASSheet(
                sheet, "portfolio", scheme_code, scheme_name, month_evidence or "",
                (table,), _classify_cells(sheet, table),
            )
        )

    if len(detected_months) != 1:
        raise PPFASStructureError(
            "workbook_month_disagreement", "Groww scheme sheets disagree on disclosure month",
            details={"detected_months": sorted(detected_months)},
        )
    detected_month = next(iter(detected_months))
    expected = set(expected_scheme_codes(manifest, detected_month))
    if detected_codes != expected:
        raise PPFASStructureError(
            "sheet_inventory_mismatch", "Groww workbook scheme inventory changed",
            details={"missing": sorted(expected - detected_codes), "unexpected": sorted(detected_codes - expected)},
        )
    if requested_publisher != "groww":
        raise PPFASStructureError(
            "publisher_mismatch",
            f"CLI publisher {requested_publisher!r} does not match detected publisher 'groww'",
        )
    if requested_disclosure_month != detected_month:
        raise PPFASStructureError(
            "disclosure_month_mismatch",
            f"CLI month {requested_disclosure_month!r} does not match {detected_month!r}",
        )
    return PPFASWorkbook(
        manifest["manifest_version"], "groww", detected_month,
        source.workbook_format, source.date_system, tuple(parsed_sheets),
    )


def _instrument_code(heading: str | None) -> str:
    value = (heading or "other").casefold()
    for needle, code in (
        ("equity", "equity"), ("reit", "reit"), ("invit", "invit"),
        ("certificate of deposit", "certificate_of_deposit"),
        ("commercial paper", "commercial_paper"), ("treasury", "treasury_bill"),
        ("money market", "money_market"), ("debt", "debt"),
        ("government", "government_security"), ("mutual fund", "mutual_fund_unit"),
        ("repo", "reverse_repo_treps"), ("treps", "reverse_repo_treps"),
    ):
        if needle in value:
            return code
    return "other"


def normalize_groww_holdings(parsed: PPFASWorkbook) -> tuple[NormalizedHolding, ...]:
    contract = load_manifest()["holdings_normalization"]
    holdings: list[NormalizedHolding] = []
    for sheet in parsed.sheets:
        if sheet.classification != "portfolio":
            continue
        by_position = {
            (cell.source.row_position, cell.source.column_position): cell.source
            for cell in sheet.cells
        }
        table = sheet.tables[0]
        heading: str | None = None
        scheme_name = sheet.scheme_name_raw.casefold()
        if "gold" in scheme_name or "silver" in scheme_name:
            instrument_code = "commodity"
        elif any(token in scheme_name for token in ("nifty", "bse", "index", "etf")):
            instrument_code = "equity"
        else:
            instrument_code = "other"
        in_derivatives = False
        for row in range(4, table.end_row):
            displays = {
                column: by_position.get((row, column)).display_value
                if by_position.get((row, column)) is not None else None
                for column in range(2, 11)
            }
            name = (displays[3] or "").strip()
            label = name.casefold()
            market, market_missing, _ = _holding_decimal(displays[6])
            percentage, percentage_missing, qualifier = _holding_decimal(displays[7])
            isin, status = isin_status(displays[2])
            if displays[2] and re.fullmatch(
                r"(?:INTREP\d{6}|IN011234455[89])", displays[2].strip().upper()
            ):
                isin, status = None, "missing"
            is_holding = bool(
                name and label not in TOTAL_LABELS
                and (status == "valid" or (market_missing is None and percentage_missing is None))
            )
            if not is_holding:
                if label == "derivatives":
                    heading = name
                    instrument_code = "derivative_future"
                    in_derivatives = True
                    continue
                if in_derivatives and label == "total":
                    heading = None
                    instrument_code = "other"
                    in_derivatives = False
                    continue
                if name and label not in TOTAL_LABELS:
                    heading = name
                    candidate_code = _instrument_code(name)
                    if candidate_code != "other":
                        instrument_code = candidate_code
                continue
            quantity, quantity_missing, _ = _holding_decimal(displays[5])
            instrument_raw = heading or "Other"
            if in_derivatives:
                isin, status = None, "missing"
            holdings.append(
                NormalizedHolding(
                    sheet.source.position, sheet.source.name, table.position,
                    "derivatives" if in_derivatives else table.table_kind,
                    row, parsed.detected_publisher,
                    sheet.scheme_name_raw, sheet.scheme_code, parsed.detected_disclosure_month,
                    None, name, displays[2], isin, status, quantity, quantity_missing,
                    market, market_missing, percentage, None, percentage_missing,
                    qualifier, "net_assets", instrument_raw, instrument_code,
                    displays[4], None, "INR", "COUNT", "INR_LAKH", "100000",
                    "PERCENT_POINT", contract["normalization_rule_version"],
                )
            )
    return tuple(holdings)
