"""PPFAS consolidated monthly-workbook structure parser (M02)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .workbook import SourceCell, SourceSheet, read_workbook


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifests" / "amc_workbook_ppfas.yml"


class PPFASStructureError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ClassifiedCell:
    source: SourceCell
    cell_role: str
    normalized_field_code: str | None


@dataclass(frozen=True)
class WorkbookTable:
    position: int
    table_kind: str
    heading_raw: str
    start_row: int
    end_row: int
    start_column: int
    end_column: int
    header_start_row: int | None
    header_end_row: int | None
    source_total_rows: tuple[int, ...]
    required_columns: tuple[dict, ...]


@dataclass(frozen=True)
class PPFASSheet:
    source: SourceSheet
    classification: str
    scheme_code: str
    scheme_name_raw: str
    disclosure_month_evidence: str
    tables: tuple[WorkbookTable, ...]
    cells: tuple[ClassifiedCell, ...]

    def inventory_dict(self) -> dict:
        return {
            "sheet_position": self.source.position,
            "sheet_name_raw": self.source.name,
            "classification": self.classification,
            "scheme_code": self.scheme_code,
            "scheme_name_raw": self.scheme_name_raw,
            "disclosure_month_evidence": self.disclosure_month_evidence,
            "cell_count": len(self.cells),
            "nonempty_cell_count": sum(
                cell.source.display_value not in (None, "") for cell in self.cells
            ),
            "formula_count": sum(cell.source.formula is not None for cell in self.cells),
            "merged_range_count": len(self.source.merged_ranges),
            "merged_ranges": [item.reference for item in self.source.merged_ranges],
            "tables": [asdict(table) for table in self.tables],
        }


@dataclass(frozen=True)
class PPFASWorkbook:
    manifest_version: str
    detected_publisher: str
    detected_disclosure_month: str
    workbook_format: str
    date_system: str
    sheets: tuple[PPFASSheet, ...]

    def inventory_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "detected_publisher": self.detected_publisher,
            "detected_disclosure_month": self.detected_disclosure_month,
            "workbook_format": self.workbook_format,
            "date_system": self.date_system,
            "sheet_count": len(self.sheets),
            "portfolio_sheet_count": sum(
                sheet.classification == "portfolio" for sheet in self.sheets
            ),
            "non_portfolio_sheet_count": sum(
                sheet.classification != "portfolio" for sheet in self.sheets
            ),
            "cell_count": sum(len(sheet.cells) for sheet in self.sheets),
            "nonempty_cell_count": sum(
                cell.source.display_value not in (None, "")
                for sheet in self.sheets for cell in sheet.cells
            ),
            "formula_count": sum(
                cell.source.formula is not None
                for sheet in self.sheets for cell in sheet.cells
            ),
            "merged_range_count": sum(
                len(sheet.source.merged_ranges) for sheet in self.sheets
            ),
            "table_count": sum(len(sheet.tables) for sheet in self.sheets),
            "sheets": [sheet.inventory_dict() for sheet in self.sheets],
        }


@dataclass(frozen=True)
class NormalizedHolding:
    source_sheet_position: int
    source_sheet_name: str
    source_table_position: int
    source_table_kind: str
    source_row: int
    publisher: str
    scheme_name_raw: str
    scheme_code: str
    disclosure_month: str
    source_security_code_raw: str | None
    security_name_raw: str
    isin_raw: str | None
    isin_normalized: str | None
    isin_validation_status: str
    quantity_decimal: str | None
    quantity_missing_kind: str | None
    market_value_decimal: str | None
    market_value_missing_kind: str | None
    percentage_of_nav_decimal: str | None
    percentage_of_aum_decimal: str | None
    percentage_missing_kind: str | None
    percentage_qualifier: str | None
    percentage_basis: str
    instrument_type_raw: str
    instrument_type_code: str
    industry_or_rating_raw: str | None
    position_direction_raw: str | None
    currency: str
    quantity_unit_code: str
    market_value_unit_code: str
    market_value_scale_multiplier: str
    percentage_unit_code: str
    normalization_rule_version: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != "0.4.0" or payload.get("publisher") != "ppfas":
        raise RuntimeError("Unsupported PPFAS workbook manifest")
    return payload


def _effective(item: dict, month: str) -> bool:
    return (
        str(item.get("valid_from_month", "0000-01")) <= month
        and (item.get("valid_to_month") is None or str(item["valid_to_month"]) >= month)
    )


def expected_scheme_codes(manifest: dict, month: str) -> tuple[str, ...]:
    return tuple(
        str(item["scheme_code"])
        for item in manifest["sheets"]
        if _effective(item, month)
    )


def _normalized(value: str | None) -> str:
    return " ".join((value or "").split())


def _cell(sheet: SourceSheet, coordinate: str) -> SourceCell:
    found = next((cell for cell in sheet.cells if cell.coordinate == coordinate), None)
    if found is None or found.display_value in (None, ""):
        raise PPFASStructureError(
            "required_evidence_missing",
            f"Required evidence cell {sheet.name}!{coordinate} is empty or absent",
            details={"sheet": sheet.name, "coordinate": coordinate},
        )
    return found


def _month_from_evidence(raw: str, pattern: str, sheet_name: str) -> str:
    match = re.fullmatch(pattern, raw)
    if match is None:
        raise PPFASStructureError(
            "disclosure_month_unrecognized",
            f"Unrecognized disclosure-month evidence on sheet {sheet_name}",
            details={"sheet": sheet_name, "display_value": raw},
        )
    try:
        parsed = datetime.strptime(match.group(1), "%B %d, %Y")
    except ValueError as exc:
        raise PPFASStructureError(
            "disclosure_month_unrecognized", str(exc), details={"sheet": sheet_name}
        ) from exc
    return parsed.strftime("%Y-%m")


def _unique_anchor(
    sheet: SourceSheet,
    value: str,
    *,
    after_row: int = 0,
    before_row: int | None = None,
) -> SourceCell:
    found = [
        cell for cell in sheet.cells
        if cell.row_position > after_row
        and (before_row is None or cell.row_position < before_row)
        and _normalized(cell.display_value) == value
    ]
    if len(found) != 1:
        raise PPFASStructureError(
            "table_anchor_ambiguous",
            f"Expected one {value!r} anchor on {sheet.name}, found {len(found)}",
            details={"sheet": sheet.name, "anchor": value, "count": len(found)},
        )
    return found[0]


def _first_anchor(sheet: SourceSheet, value: str, *, after_row: int) -> SourceCell:
    found = sorted(
        (
            cell for cell in sheet.cells
            if cell.row_position > after_row and _normalized(cell.display_value) == value
        ),
        key=lambda cell: (cell.row_position, cell.column_position),
    )
    if not found:
        raise PPFASStructureError(
            "partial_derivatives_table",
            f"Missing {value!r} after derivatives header on {sheet.name}",
            details={"sheet": sheet.name, "anchor": value},
        )
    return found[0]


def _header_fields(
    sheet: SourceSheet, row: int, columns: list[dict], *, table_kind: str
) -> dict[int, str]:
    by_position = {
        (cell.row_position, cell.column_position): cell for cell in sheet.cells
    }
    fields: dict[int, str] = {}
    missing: list[dict] = []
    for expected in columns:
        column = int(expected["column"])
        cell = by_position.get((row, column))
        allowed = expected.get("allowed_labels", [expected["label"]])
        if cell is None or _normalized(cell.display_value) not in allowed:
            missing.append(
                {
                    "column": column,
                    "expected": expected["label"],
                    "actual": None if cell is None else cell.display_value,
                }
            )
        fields[column] = expected["field_code"]
    if missing:
        raise PPFASStructureError(
            "required_column_missing",
            f"{table_kind} required columns changed on {sheet.name}",
            details={"sheet": sheet.name, "table_kind": table_kind, "columns": missing},
        )
    return fields


def _source_total_rows(sheet: SourceSheet, start: int, end: int) -> tuple[int, ...]:
    return tuple(
        cell.row_position for cell in sheet.cells
        if start <= cell.row_position <= end
        and cell.column_position == 2
        and _normalized(cell.display_value) in {"Sub Total", "Total", "GRAND TOTAL"}
    )


def _classify_cells(
    sheet: SourceSheet,
    tables: tuple[WorkbookTable, ...],
    field_maps: dict[int, dict[int, str]],
    title_coordinate: str,
    month_coordinate: str,
) -> tuple[ClassifiedCell, ...]:
    classified: list[ClassifiedCell] = []
    for cell in sheet.cells:
        role = "styled_blank" if cell.display_value in (None, "") else "supporting_disclosure"
        field_code = None
        for table in tables:
            if table.start_row <= cell.row_position <= table.end_row:
                role = f"{table.table_kind}_cell"
                field_code = field_maps.get(table.position, {}).get(cell.column_position)
                if cell.row_position in table.source_total_rows:
                    role = (
                        "source_total_label"
                        if cell.column_position == 2 else "source_total_value"
                    )
                if table.header_start_row == cell.row_position:
                    role = f"{table.table_kind}_header"
                break
        if cell.coordinate == title_coordinate:
            role = "scheme_identity"
        elif cell.coordinate == month_coordinate:
            role = "disclosure_month_evidence"
        if cell.formula is not None:
            role = "formula_cached_value"
        classified.append(ClassifiedCell(cell, role, field_code))
    return tuple(classified)


def parse_ppfas_workbook(
    path: Path,
    *,
    requested_publisher: str,
    requested_disclosure_month: str,
) -> PPFASWorkbook:
    manifest = load_manifest()
    try:
        source = read_workbook(path)
    except (OSError, ValueError) as exc:
        raise PPFASStructureError(
            "workbook_parse_failed", str(exc), details={"path": str(path)}
        ) from exc

    declared_by_code = {
        str(item["scheme_code"]): item for item in manifest["sheets"]
    }
    detected_months: set[str] = set()
    month_rule = manifest["month_evidence"]
    for source_sheet in source.sheets:
        if source_sheet.name not in declared_by_code:
            continue
        month_evidence = _cell(
            source_sheet, month_rule["coordinate"]
        ).display_value or ""
        detected_months.add(
            _month_from_evidence(month_evidence, month_rule["pattern"], source_sheet.name)
        )
    if len(detected_months) != 1:
        raise PPFASStructureError(
            "workbook_month_disagreement",
            "Scheme sheets disagree on disclosure month",
            details={"detected_months": sorted(detected_months)},
        )
    detected_month = next(iter(detected_months))

    expected_sheets = [
        *(
            {
                "scheme_code": str(item["sheet_name"]),
                "classification": str(item["classification"]),
                "is_non_portfolio": True,
            }
            for item in manifest.get("non_portfolio_sheets", [])
            if _effective(item, detected_month)
        ),
        *(
            item for item in manifest["sheets"] if _effective(item, detected_month)
        ),
    ]
    actual_names = [sheet.name for sheet in source.sheets]
    expected_names = [sheet["scheme_code"] for sheet in expected_sheets]
    if actual_names != expected_names:
        raise PPFASStructureError(
            "sheet_inventory_drift",
            "Workbook sheet order or classification changed",
            details={"expected": expected_names, "actual": actual_names},
        )

    parsed_sheets: list[PPFASSheet] = []
    for source_sheet, expected in zip(source.sheets, expected_sheets, strict=True):
        if expected.get("is_non_portfolio"):
            parsed_sheets.append(
                PPFASSheet(
                    source_sheet,
                    expected["classification"],
                    expected["scheme_code"],
                    (_cell(source_sheet, "B1").display_value or ""),
                    "",
                    (),
                    _classify_cells(source_sheet, (), {}, "B1", ""),
                )
            )
            continue
        title = _cell(source_sheet, "B1").display_value or ""
        if not title.startswith(expected["title_prefix"]):
            raise PPFASStructureError(
                "scheme_identity_mismatch",
                f"Scheme title does not match sheet {source_sheet.name}",
                details={"sheet": source_sheet.name, "title": title},
            )
        month_evidence = _cell(source_sheet, month_rule["coordinate"]).display_value or ""
        detected_month = _month_from_evidence(
            month_evidence, month_rule["pattern"], source_sheet.name
        )
        if detected_month != next(iter(detected_months)):
            raise PPFASStructureError(
                "workbook_month_disagreement",
                "Scheme sheets disagree on disclosure month",
            )

        primary_rule = manifest["primary_table"]
        grand_total = _unique_anchor(source_sheet, primary_rule["end_anchor"])
        header = _unique_anchor(
            source_sheet, primary_rule["header_anchor"], before_row=grand_total.row_position
        )
        if header.column_position != int(primary_rule["header_start_column"]):
            raise PPFASStructureError(
                "primary_table_shifted",
                f"Primary table header shifted on {source_sheet.name}",
            )
        if grand_total.row_position <= header.row_position:
            raise PPFASStructureError("partial_primary_table", "GRAND TOTAL precedes header")
        primary_fields = _header_fields(
            source_sheet, header.row_position, primary_rule["required_columns"],
            table_kind="primary_portfolio",
        )
        primary_fields.update(
            {
                int(item["column"]): item["field_code"]
                for item in primary_rule["optional_columns"]
            }
        )
        primary = WorkbookTable(
            0, "primary_portfolio", primary_rule["header_anchor"],
            header.row_position, grand_total.row_position, 1,
            max(primary_fields), header.row_position, header.row_position,
            _source_total_rows(source_sheet, header.row_position, grand_total.row_position),
            tuple(primary_rule["required_columns"]),
        )
        tables = [primary]
        field_maps: dict[int, dict[int, str]] = {0: primary_fields}

        derivative_rule = manifest["derivatives_table"]
        derivative_headings = [
            cell for cell in source_sheet.cells
            if _normalized(cell.display_value) == derivative_rule["heading_anchor"]
        ]
        if len(derivative_headings) > 1:
            raise PPFASStructureError(
                "table_anchor_ambiguous",
                f"Multiple Derivatives headings on {source_sheet.name}",
            )
        occupied_end = grand_total.row_position
        if derivative_headings:
            derivative_heading = derivative_headings[0]
            derivative_header_row = derivative_heading.row_position + 1
            derivative_fields = _header_fields(
                source_sheet, derivative_header_row, derivative_rule["required_columns"],
                table_kind="derivatives",
            )
            derivative_fields.update(
                {
                    int(item["column"]): item["field_code"]
                    for item in derivative_rule["optional_columns"]
                }
            )
            derivative_total = _first_anchor(
                source_sheet, derivative_rule["end_anchor"], after_row=derivative_header_row
            )
            derivative = WorkbookTable(
                1, "derivatives", derivative_rule["heading_anchor"],
                derivative_heading.row_position, derivative_total.row_position, 1,
                max(derivative_fields), derivative_header_row, derivative_header_row,
                _source_total_rows(
                    source_sheet, derivative_heading.row_position, derivative_total.row_position
                ),
                tuple(derivative_rule["required_columns"]),
            )
            tables.append(derivative)
            field_maps[1] = derivative_fields
            occupied_end = derivative_total.row_position

        max_row = max(cell.row_position for cell in source_sheet.cells)
        if max_row > occupied_end:
            tables.append(
                WorkbookTable(
                    len(tables), "supporting_disclosures", "Supporting disclosures",
                    occupied_end + 1, max_row, 1,
                    max(cell.column_position for cell in source_sheet.cells),
                    None, None, (), (),
                )
            )
        table_tuple = tuple(tables)
        parsed_sheets.append(
            PPFASSheet(
                source_sheet, expected["classification"], expected["scheme_code"],
                title, month_evidence, table_tuple,
                _classify_cells(
                    source_sheet, table_tuple, field_maps, "B1", month_rule["coordinate"]
                ),
            )
        )

    if requested_publisher != manifest["publisher"]:
        raise PPFASStructureError(
            "publisher_mismatch",
            f"CLI publisher {requested_publisher!r} does not match detected publisher 'ppfas'",
            details={"requested": requested_publisher, "detected": manifest["publisher"]},
        )
    if requested_disclosure_month != detected_month:
        raise PPFASStructureError(
            "disclosure_month_mismatch",
            f"CLI month {requested_disclosure_month} does not match workbook month {detected_month}",
            details={"requested": requested_disclosure_month, "detected": detected_month},
        )
    return PPFASWorkbook(
        manifest["manifest_version"], manifest["publisher"], detected_month,
        (
            "ooxml_in_xls_extension"
            if source.workbook_format == "ooxml"
            else "legacy_biff8_xls"
        ),
        source.date_system,
        tuple(parsed_sheets),
    )


def _holding_decimal(display_value: str | None) -> tuple[str | None, str | None, str | None]:
    """Parse one declared workbook display without passing through binary float."""
    if display_value is None or display_value.strip() == "":
        return None, "blank", None
    text = display_value.strip()
    qualifier = "less_than_0_01" if text.startswith("$") else None
    if qualifier:
        text = text[1:]
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = text.removesuffix("%").replace(",", "").strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?", text):
        return None, "parse_error", qualifier
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, "parse_error", qualifier
    if negative:
        value = -value
    return format(value, "f"), None, qualifier


def isin_status(value: str | None) -> tuple[str | None, str]:
    """Return canonical ISIN text and ISO-6166/Luhn structural status."""
    if value is None or value.strip() == "":
        return None, "missing"
    canonical = re.sub(r"\s+", "", value).upper()
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", canonical) is None:
        return canonical, "invalid_format"
    expanded = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in canonical
    )
    total = 0
    parity = len(expanded) % 2
    for position, character in enumerate(expanded):
        digit = int(character)
        if position % 2 == parity:
            digit *= 2
        total += digit // 10 + digit % 10
    return canonical, "valid" if total % 10 == 0 else "invalid_checksum"


def normalized_security_name(value: str) -> str:
    """Version-1 fallback key: case-folded exact words, with no fuzzy inference."""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def normalize_ppfas_holdings(parsed: PPFASWorkbook) -> tuple[NormalizedHolding, ...]:
    """Normalize declared primary and derivative instrument rows from saved cells."""
    manifest = load_manifest()
    contract = manifest["holdings_normalization"]
    heading_codes = contract["primary_instrument_headings"]
    ignored_headings = set(contract["ignored_primary_headings"])
    normalization_version = contract["normalization_rule_version"]
    holdings: list[NormalizedHolding] = []
    for sheet in parsed.sheets:
        by_position = {
            (cell.source.row_position, cell.source.column_position): cell.source
            for cell in sheet.cells
        }
        for table in sheet.tables:
            if table.table_kind not in {"primary_portfolio", "derivatives"}:
                continue
            current_heading: tuple[str, str] | None = None
            first_data_row = (table.header_end_row or table.start_row) + 1
            for row in range(first_data_row, table.end_row):
                displays = {
                    column: (
                        by_position[(row, column)].display_value
                        if (row, column) in by_position else None
                    )
                    for column in range(table.start_column, table.end_column + 1)
                }
                source_code = displays.get(1)
                name = displays.get(2)
                if table.table_kind == "primary_portfolio":
                    normalized_heading = _normalized(name)
                    if normalized_heading in heading_codes:
                        current_heading = (
                            name or normalized_heading,
                            heading_codes[normalized_heading],
                        )
                        continue
                    if normalized_heading in ignored_headings:
                        continue
                    has_declared_values = any(
                        displays.get(column) not in (None, "")
                        for column in (3, 5, 6, 7)
                    )
                    if not source_code and not (name and has_declared_values):
                        continue
                if table.table_kind == "derivatives":
                    normalized_heading = _normalized(name)
                    if normalized_heading == contract["derivative_heading"]:
                        current_heading = (name or "", "derivative_future")
                        continue
                    if normalized_heading in ignored_headings:
                        continue
                    has_declared_values = any(
                        displays.get(column) not in (None, "")
                        for column in (3, 4, 5, 6)
                    )
                    if not source_code and not (name and has_declared_values):
                        continue
                if name is None or name.strip() == "":
                    raise PPFASStructureError(
                        "holding_name_missing",
                        f"Instrument row {sheet.source.name}!{row} has no name",
                    )
                if current_heading is None:
                    current_heading = ("Unknown", "unknown")
                if table.table_kind == "primary_portfolio":
                    isin_raw = displays.get(3)
                    industry_or_rating = displays.get(4)
                    position_direction = None
                    quantity_display = displays.get(5)
                    market_display = displays.get(6)
                    percentage_display = displays.get(7)
                    percentage_basis = "net_assets"
                else:
                    isin_raw = None
                    industry_or_rating = None
                    position_direction = displays.get(3)
                    quantity_display = displays.get(4)
                    market_display = displays.get(5)
                    percentage_display = displays.get(6)
                    percentage_basis = "aum"
                quantity, quantity_missing, _ = _holding_decimal(quantity_display)
                market_value, market_missing, _ = _holding_decimal(market_display)
                percentage, percentage_missing, qualifier = _holding_decimal(
                    percentage_display
                )
                isin, validation_status = isin_status(isin_raw)
                holdings.append(
                    NormalizedHolding(
                        sheet.source.position, sheet.source.name, table.position,
                        table.table_kind, row, parsed.detected_publisher,
                        sheet.scheme_name_raw, sheet.scheme_code,
                        parsed.detected_disclosure_month, source_code, name,
                        isin_raw, isin, validation_status, quantity,
                        quantity_missing, market_value, market_missing,
                        percentage if percentage_basis == "net_assets" else None,
                        percentage if percentage_basis == "aum" else None,
                        percentage_missing, qualifier, percentage_basis,
                        current_heading[0], current_heading[1], industry_or_rating,
                        position_direction, contract["currency"], "COUNT",
                        contract["market_value_unit_code"],
                        contract["market_value_scale_multiplier"],
                        "PERCENT_POINT", normalization_version,
                    )
                )
    return tuple(holdings)
