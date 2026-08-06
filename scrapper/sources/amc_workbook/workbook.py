"""Bounded, non-evaluating OOXML cell reader for official AMC workbooks."""

from __future__ import annotations

import posixpath
import re
import struct
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

import xlrd
from xlrd.biffh import XL_EOF, XL_FORMULA_OPCODES
from xlrd.formula import FMLA_TYPE_CELL, decompile_formula

from .detector import WORKBOOK_RELS, WORKBOOK_XML, _safe_xml, _validate_zip_limits


CELL_RE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
BUILTIN_FORMATS = {
    0: "General",
    1: "0",
    2: "0.00",
    3: "#,##0",
    4: "#,##0.00",
    9: "0%",
    10: "0.00%",
    14: "mm-dd-yy",
    49: "@",
}


@dataclass(frozen=True)
class MergeRange:
    reference: str
    start_row: int
    start_column: int
    end_row: int
    end_column: int


@dataclass(frozen=True)
class SourceCell:
    row_position: int
    column_position: int
    coordinate: str
    raw_value: str | None
    display_value: str | None
    formula: str | None
    data_type: str
    number_format: str
    style_index: int


@dataclass(frozen=True)
class SourceSheet:
    position: int
    name: str
    visibility_state: str
    package_part: str
    cells: tuple[SourceCell, ...]
    merged_ranges: tuple[MergeRange, ...]


@dataclass(frozen=True)
class SourceWorkbook:
    date_system: str
    sheets: tuple[SourceSheet, ...]
    workbook_format: str = "ooxml"


def column_number(letters: str) -> int:
    result = 0
    for character in letters:
        result = result * 26 + ord(character) - 64
    return result


def column_letters(number: int) -> str:
    if number < 1:
        raise ValueError("Spreadsheet column numbers start at one")
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def split_coordinate(coordinate: str) -> tuple[int, int]:
    match = CELL_RE.fullmatch(coordinate)
    if match is None:
        raise ValueError(f"Invalid OOXML cell coordinate: {coordinate}")
    return int(match.group(2)), column_number(match.group(1))


def _text(node: ElementTree.Element | None) -> str | None:
    return None if node is None else "".join(node.itertext())


def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = _safe_xml(archive.read("xl/sharedStrings.xml"), "xl/sharedStrings.xml")
    return tuple("".join(item.itertext()) for item in root if item.tag.endswith("}si"))


def _number_formats(archive: zipfile.ZipFile) -> tuple[str, ...]:
    if "xl/styles.xml" not in archive.namelist():
        return ("General",)
    root = _safe_xml(archive.read("xl/styles.xml"), "xl/styles.xml")
    custom = dict(BUILTIN_FORMATS)
    for node in root.iter():
        if node.tag.endswith("}numFmt"):
            custom[int(node.attrib["numFmtId"])] = node.attrib["formatCode"]
    xfs = next((node for node in root if node.tag.endswith("}cellXfs")), None)
    if xfs is None:
        return ("General",)
    return tuple(custom.get(int(node.attrib.get("numFmtId", "0")), "General") for node in xfs)


def _decimal_display(raw: str, number_format: str) -> str:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    sections = number_format.split(";")
    if value < 0 and len(sections) > 1:
        section = sections[1]
        rendered_value = -value
    elif value == 0 and len(sections) > 2:
        section = sections[2]
        rendered_value = value
    else:
        section = sections[0]
        rendered_value = value
    if value == 0 and re.search(r'"-"|(?<!\\)-', section) and not re.search(r"[0#]", section):
        return "-"
    date_code = re.sub(r"\[[^]]*\]|\\|;@", "", section).lower()
    if re.search(r"[dmy]", date_code) and not re.search(r"[0#].*%", date_code):
        serial = int(rendered_value)
        converted = date(1899, 12, 30) + timedelta(days=serial)
        if "mmmm" in date_code:
            month = converted.strftime("%B")
        elif "mmm" in date_code:
            month = converted.strftime("%b")
        else:
            month = f"{converted.month:02d}"
        day = str(converted.day) if re.search(r"(?<!d)d(?!d)", date_code) else f"{converted.day:02d}"
        year = str(converted.year) if "yyyy" in date_code else f"{converted.year % 100:02d}"
        separator = "/" if "/" in date_code else " "
        if date_code.startswith("mmmm") or date_code.startswith("mmm"):
            return f"{month}{separator}{year}"
        return f"{day}{separator}{month}{separator}{year}"
    if "%" in section:
        rendered_value *= 100
        decimals = len(section.split(".", 1)[1].split("%", 1)[0]) if "." in section else 0
        rendered = (
            f"{rendered_value:,.{decimals}f}%"
            if "," in section else f"{rendered_value:.{decimals}f}%"
        )
        return f"({rendered})" if value < 0 and "(" in section else rendered
    cleaned = re.sub(r'"[^"]*"|\\.|_.|\*.', "", section)
    placeholders = re.findall(r"[0#?,]+(?:\.[0#?]+)?", cleaned)
    if not placeholders or "General" in section:
        return raw
    pattern = max(placeholders, key=len)
    decimals = len(pattern.split(".", 1)[1]) if "." in pattern else 0
    grouped = "," in pattern.split(".", 1)[0]
    rendered = (
        f"{rendered_value:,.{decimals}f}"
        if grouped else f"{rendered_value:.{decimals}f}"
    )
    if decimals:
        optional = len(pattern.rsplit(".", 1)[1]) - len(pattern.rsplit(".", 1)[1].rstrip("#?"))
        for _ in range(optional):
            if rendered.endswith("0"):
                rendered = rendered[:-1]
        if rendered.endswith("."):
            rendered = rendered[:-1]
    literals = "".join(re.findall(r'"([^"]*)"', section))
    if literals and literals != "-":
        rendered = f"{literals}{rendered}"
    return f"({rendered})" if value < 0 and "(" in section else rendered


def _cell_value(
    node: ElementTree.Element,
    shared_strings: tuple[str, ...],
    number_format: str,
) -> tuple[str | None, str | None, str | None, str]:
    data_type = node.attrib.get("t", "n")
    formula = _text(next((child for child in node if child.tag.endswith("}f")), None))
    value = _text(next((child for child in node if child.tag.endswith("}v")), None))
    if data_type == "inlineStr":
        inline = next((child for child in node if child.tag.endswith("}is")), None)
        value = _text(inline)
    if value is None:
        return None, None, formula, data_type
    if data_type == "s":
        try:
            display = shared_strings[int(value)]
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Invalid shared-string index: {value}") from exc
        return display, display, formula, data_type
    if data_type == "b":
        return value, "TRUE" if value == "1" else "FALSE", formula, data_type
    if data_type in {"str", "inlineStr", "e"}:
        return value, value, formula, data_type
    return value, _decimal_display(value, number_format), formula, data_type


def _merge(reference: str) -> MergeRange:
    start, _, end = reference.partition(":")
    if not end:
        end = start
    start_row, start_column = split_coordinate(start)
    end_row, end_column = split_coordinate(end)
    return MergeRange(reference, start_row, start_column, end_row, end_column)


def read_ooxml_workbook(path: Path) -> SourceWorkbook:
    """Read stored values and cached formula results without evaluating formulas."""
    with zipfile.ZipFile(path) as archive:
        _validate_zip_limits(archive)
        workbook = _safe_xml(archive.read(WORKBOOK_XML), WORKBOOK_XML)
        relationships_root = _safe_xml(archive.read(WORKBOOK_RELS), WORKBOOK_RELS)
        relationships = {
            relation.attrib["Id"]: posixpath.normpath(
                posixpath.join("xl", relation.attrib["Target"])
            )
            for relation in relationships_root
            if relation.attrib.get("Type", "").endswith("/worksheet")
        }
        shared_strings = _shared_strings(archive)
        formats = _number_formats(archive)
        workbook_properties = next(
            (node for node in workbook if node.tag.endswith("}workbookPr")), None
        )
        date_system = (
            "1904"
            if workbook_properties is not None
            and workbook_properties.attrib.get("date1904") in {"1", "true"}
            else "1900"
        )
        sheets_parent = next(node for node in workbook.iter() if node.tag.endswith("}sheets"))
        sheets: list[SourceSheet] = []
        for position, sheet_node in enumerate(sheets_parent):
            rel_id = next(value for key, value in sheet_node.attrib.items() if key.endswith("}id"))
            part = relationships[rel_id]
            root = _safe_xml(archive.read(part), part)
            cells: list[SourceCell] = []
            for node in root.iter():
                if not node.tag.endswith("}c"):
                    continue
                coordinate = node.attrib["r"]
                row, column = split_coordinate(coordinate)
                style_index = int(node.attrib.get("s", "0"))
                if style_index >= len(formats):
                    raise ValueError(f"Invalid style index {style_index} at {coordinate}")
                number_format = formats[style_index]
                raw, display, formula, data_type = _cell_value(
                    node, shared_strings, number_format
                )
                cells.append(
                    SourceCell(
                        row, column, coordinate, raw, display, formula,
                        data_type, number_format, style_index,
                    )
                )
            merges = tuple(
                _merge(node.attrib["ref"])
                for node in root.iter()
                if node.tag.endswith("}mergeCell")
            )
            sheets.append(
                SourceSheet(
                    position,
                    sheet_node.attrib["name"],
                    sheet_node.attrib.get("state", "visible"),
                    part,
                    tuple(sorted(cells, key=lambda cell: (cell.row_position, cell.column_position))),
                    merges,
                )
            )
    return SourceWorkbook(date_system, tuple(sheets), "ooxml")


def _xls_formula_cells(book: xlrd.book.Book) -> dict[tuple[int, int, int], str]:
    """Decompile BIFF formula tokens without evaluating them."""
    if book.mem is None:
        raise ValueError("Legacy workbook stream is unavailable")
    formulas: dict[tuple[int, int, int], str] = {}
    for sheet_position, start in enumerate(book._sh_abs_posn):
        position = start
        while position + 4 <= len(book.mem):
            record_code, record_size = struct.unpack(
                "<HH", book.mem[position : position + 4]
            )
            data_start = position + 4
            data_end = data_start + record_size
            if data_end > len(book.mem):
                raise ValueError("Truncated BIFF record in legacy workbook")
            data = book.mem[data_start:data_end]
            position = data_end
            if record_code in XL_FORMULA_OPCODES:
                if len(data) < 22:
                    raise ValueError("Truncated BIFF formula record")
                row, column = struct.unpack("<HH", data[:4])
                formula_length = struct.unpack("<H", data[20:22])[0]
                formula_end = 22 + formula_length
                if formula_end > len(data):
                    raise ValueError("Truncated BIFF formula token stream")
                formula = decompile_formula(
                    book,
                    data[22:formula_end],
                    formula_length,
                    FMLA_TYPE_CELL,
                    browx=row,
                    bcolx=column,
                )
                if formula is not None:
                    formulas[(sheet_position, row, column)] = formula
            if record_code == XL_EOF:
                break
    return formulas


def _xls_number_format(book: xlrd.book.Book, xf_index: int) -> str:
    try:
        format_key = book.xf_list[xf_index].format_key
    except IndexError as exc:
        raise ValueError(f"Invalid legacy XLS style index: {xf_index}") from exc
    number_format = book.format_map.get(format_key)
    return "General" if number_format is None else str(number_format.format_str)


def _xls_cell_values(
    cell: xlrd.sheet.Cell,
    *,
    number_format: str,
) -> tuple[str | None, str | None, str]:
    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
        return None, None, "blank"
    if cell.ctype == xlrd.XL_CELL_TEXT:
        value = str(cell.value)
        return value, value, "str"
    if cell.ctype in {xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE}:
        raw = repr(float(cell.value))
        return raw, _decimal_display(raw, number_format), (
            "date" if cell.ctype == xlrd.XL_CELL_DATE else "n"
        )
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        raw = "1" if bool(cell.value) else "0"
        return raw, "TRUE" if raw == "1" else "FALSE", "b"
    if cell.ctype == xlrd.XL_CELL_ERROR:
        raw = str(cell.value)
        return raw, xlrd.biffh.error_text_from_code.get(int(cell.value), raw), "e"
    value = str(cell.value)
    return value, value, "unknown"


def read_xls_workbook(path: Path) -> SourceWorkbook:
    """Read BIFF cell records and formula tokens without executing workbook content."""
    try:
        book = xlrd.open_workbook(
            path,
            formatting_info=True,
            on_demand=True,
            use_mmap=False,
            ignore_workbook_corruption=False,
        )
    except (OSError, xlrd.XLRDError) as exc:
        raise ValueError(f"Invalid legacy XLS workbook: {exc}") from exc
    try:
        formulas = _xls_formula_cells(book)
        sheets: list[SourceSheet] = []
        visibility_names = {0: "visible", 1: "hidden", 2: "veryHidden"}
        for position in range(book.nsheets):
            sheet = book.sheet_by_index(position)
            cells: list[SourceCell] = []
            for row in range(sheet.nrows):
                for column in range(sheet.row_len(row)):
                    cell = sheet.cell(row, column)
                    # With formatting_info=True, stored BLANK/MULBLANK records have
                    # XL_CELL_BLANK. Ignore only rectangular padding invented by xlrd.
                    if cell.ctype == xlrd.XL_CELL_EMPTY:
                        continue
                    number_format = _xls_number_format(book, cell.xf_index)
                    raw, display, data_type = _xls_cell_values(
                        cell, number_format=number_format
                    )
                    formula = formulas.get((position, row, column))
                    cells.append(
                        SourceCell(
                            row + 1,
                            column + 1,
                            f"{column_letters(column + 1)}{row + 1}",
                            raw,
                            display,
                            formula,
                            "formula" if formula is not None else data_type,
                            number_format,
                            cell.xf_index,
                        )
                    )
            merges = tuple(
                MergeRange(
                    (
                        f"{column_letters(first_column + 1)}{first_row + 1}:"
                        f"{column_letters(last_column)}{last_row}"
                    ),
                    first_row + 1,
                    first_column + 1,
                    last_row,
                    last_column,
                )
                for first_row, last_row, first_column, last_column
                in sheet.merged_cells
            )
            sheets.append(
                SourceSheet(
                    position,
                    sheet.name,
                    visibility_names.get(sheet.visibility, "unknown"),
                    f"Workbook/Sheet{position + 1}",
                    tuple(cells),
                    merges,
                )
            )
        return SourceWorkbook(
            "1904" if book.datemode else "1900", tuple(sheets), "legacy_biff8"
        )
    except (IndexError, KeyError, struct.error, xlrd.XLRDError) as exc:
        raise ValueError(f"Invalid legacy XLS workbook structure: {exc}") from exc
    finally:
        book.release_resources()


def read_workbook(path: Path) -> SourceWorkbook:
    with Path(path).open("rb") as source:
        signature = source.read(8)
    if signature == bytes.fromhex("D0CF11E0A1B11AE1"):
        return read_xls_workbook(path)
    return read_ooxml_workbook(path)
