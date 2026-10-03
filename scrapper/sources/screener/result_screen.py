"""Deterministic, source-faithful parser for Screener result screens."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from ...core.nums import clean_text


RESULT_SCREEN_PARSER_NAME = "screener-result-screen"
RESULT_SCREEN_PARSER_VERSION = "1.0.1"


@dataclass(frozen=True)
class ResultScreenCell:
    row_position: int
    column_position: int
    row_label_raw: str | None
    column_label_raw: str | None
    raw_text: str
    normalized_display_text: str
    link_url: str | None
    cell_role: str


@dataclass(frozen=True)
class ResultScreenRow:
    source_position: int
    serial_display: str
    screen_row_company_id: str
    company_name_raw: str
    company_href_raw: str
    company_path_normalized: str
    reporting_scope_hint: str
    last_result_date_display: str
    result_period: str
    source_cells: tuple[ResultScreenCell, ...]


@dataclass(frozen=True)
class ParsedResultScreen:
    query_raw: str
    requested_url: str
    result_count: int
    page_number: int
    page_count: int
    table_headers: tuple[str, ...]
    rows: tuple[ResultScreenRow, ...]
    source_cells: tuple[ResultScreenCell, ...]
    is_complete_capture: bool

    @property
    def captured_row_count(self) -> int:
        return len(self.rows)

    @property
    def coverage_status(self) -> str:
        return "complete" if self.is_complete_capture else "partial"


_BLOCKED_MARKERS = (
    "captcha",
    "access denied",
    "log in to screener",
    "login to screener",
    "register on screener",
    "create an account",
)
_RESULT_DATE_HEADERS = {"last result date", "qtr date"}
_PERIOD_RE = re.compile(r"^(\d{4})(\d{2})(?:\.0+)?$")
_PAGE_INFO_RE = re.compile(
    r"([\d,]+)\s+results?\s+found(?:\s*:\s*showing\s+page\s+(\d+)\s+of\s+(\d+))?",
    re.IGNORECASE,
)


def canonical_query(query: str) -> str:
    value = clean_text(query)
    if not value:
        raise ValueError("Screener result query is required")
    return value


def normalize_company_path(href: str) -> tuple[str, str]:
    """Return an exact safe source path and its requested reporting scope."""

    value = href.strip()
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise ValueError("Company link must be a relative /company/.../ path")
    if not re.fullmatch(r"/company/[A-Za-z0-9._&%-]+/(?:consolidated/)?", parts.path):
        raise ValueError(f"Invalid Screener company path: {value!r}")
    scope = "consolidated" if parts.path.endswith("/consolidated/") else "standalone"
    return parts.path, scope


def canonical_company_path(href: str) -> str:
    """Return the one company-level path used by the results queue.

    The exchange and Screener can expose two page variants for one company.  They
    are source variants, not separate result candidates: all quarterly history
    and every current-cycle job belong to one company-period record.
    """
    path, _ = normalize_company_path(href)
    identifier = path.strip("/").split("/")[1]
    return f"/company/{identifier}/"


def normalize_result_period(display: str) -> str:
    value = clean_text(display)
    match = _PERIOD_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid Last result date: {display!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1900 <= year <= 2200 or not 1 <= month <= 12:
        raise ValueError(f"Invalid Last result date: {display!r}")
    return f"{year:04d}-{month:02d}"


def _raw_text(node: Tag) -> str:
    return node.get_text("", strip=False)


def _first_href(node: Tag) -> str | None:
    anchor = node.find("a", href=True)
    return str(anchor["href"]) if isinstance(anchor, Tag) else None


def _page_metadata(info: Tag) -> tuple[int, int, int]:
    # Prefer explicit attributes when Screener provides them, but validate all
    # values as positive decimal integers and retain text as the fallback.
    attributes = {
        key: info.get(key)
        for key in ("data-result-count", "data-page", "data-page-count")
    }
    text = clean_text(info.get_text(" ", strip=True))
    match = _PAGE_INFO_RE.search(text)
    if attributes["data-result-count"] is not None:
        result_count = int(str(attributes["data-result-count"]).replace(",", ""))
    elif match:
        result_count = int(match.group(1).replace(",", ""))
    else:
        raise ValueError("Screener result count metadata is missing")
    if attributes["data-page"] is not None:
        page_number = int(str(attributes["data-page"]))
    else:
        page_number = int(match.group(2)) if match and match.group(2) else 1
    if attributes["data-page-count"] is not None:
        page_count = int(str(attributes["data-page-count"]))
    else:
        page_count = int(match.group(3)) if match and match.group(3) else 1
    if result_count < 0 or page_number < 1 or page_count < 1 or page_number > page_count:
        raise ValueError("Invalid Screener page metadata")
    return result_count, page_number, page_count


def parse_result_screen(
    content: bytes,
    query_raw: str,
    requested_url: str,
) -> ParsedResultScreen:
    """Parse one saved authenticated screen without network or database access."""

    query = canonical_query(query_raw)
    soup = BeautifulSoup(content, "html.parser")
    sample = clean_text(soup.get_text(" ", strip=True)).lower()
    if any(marker in sample for marker in _BLOCKED_MARKERS):
        raise ValueError("Screener result screen is an authentication or access-denied page")

    info = soup.select_one("[data-page-info]")
    results = soup.select_one("[data-page-results]")
    if not isinstance(info, Tag) or not isinstance(results, Tag):
        raise ValueError("Screener result screen containers are missing")
    tables = results.find_all("table")
    if len(tables) != 1:
        raise ValueError(f"Expected exactly one Screener result table; found {len(tables)}")
    table = tables[0]
    table_rows = table.find_all("tr")
    header_rows = [
        row
        for row in table_rows
        if row.get("data-row-company-id") is None
        and row.find_all("th", recursive=False)
    ]
    if not header_rows:
        raise ValueError("Screener result table headers are missing")
    header_nodes = header_rows[0].find_all("th", recursive=False)
    headers = tuple(clean_text(node.get_text(" ", strip=True)) for node in header_nodes)
    def result_date_columns(nodes: list[Tag]) -> list[int]:
        return [
            index
            for index, node in enumerate(nodes)
            if clean_text(node.get_text(" ", strip=True)).lower() in _RESULT_DATE_HEADERS
            or clean_text(str(node.get("title", ""))).lower() == "last result date"
            or clean_text(str(node.get("data-tooltip", ""))).lower() == "last result date"
        ]

    date_columns = result_date_columns(header_nodes)
    if len(date_columns) != 1:
        raise ValueError(
            "Expected exactly one Last result date column; "
            f"found {len(date_columns)}"
        )
    date_column = date_columns[0]
    for repeated in header_rows[1:]:
        repeated_nodes = repeated.find_all("th", recursive=False)
        repeated_headers = tuple(
            clean_text(node.get_text(" ", strip=True)) for node in repeated_nodes
        )
        if repeated_headers != headers or result_date_columns(repeated_nodes) != date_columns:
            raise ValueError("Repeated Screener result table headers conflict")
    result_count, page_number, page_count = _page_metadata(info)

    all_cells: list[ResultScreenCell] = []
    header_ids = {id(row) for row in header_rows}
    for dom_position, table_row in enumerate(table_rows):
        if id(table_row) not in header_ids:
            continue
        for column, node in enumerate(table_row.find_all("th", recursive=False)):
            all_cells.append(
                ResultScreenCell(
                    dom_position,
                    column,
                    None,
                    headers[column],
                    _raw_text(node),
                    headers[column],
                    _first_href(node),
                    "column_header",
                )
            )

    parsed_rows: list[ResultScreenRow] = []
    seen_positions: set[int] = set()
    seen_paths: dict[str, str] = {}
    body_rows = [
        (dom_position, row)
        for dom_position, row in enumerate(table_rows)
        if row.get("data-row-company-id") is not None
    ]
    unexpected_rows = [
        row
        for row in table_rows
        if id(row) not in header_ids and row.get("data-row-company-id") is None
        and row.find_all(["td", "th"], recursive=False)
    ]
    if unexpected_rows:
        raise ValueError("Screener result table contains an unidentified row")
    for fallback_position, (dom_position, row) in enumerate(body_rows, start=1):
        if not isinstance(row, Tag):
            continue
        company_id = clean_text(str(row.get("data-row-company-id", "")))
        if not company_id:
            raise ValueError(f"Result row {fallback_position} has no data-row-company-id")
        nodes = row.find_all(["td", "th"], recursive=False)
        if len(nodes) != len(headers):
            raise ValueError(
                f"Result row {fallback_position} has {len(nodes)} cells; expected {len(headers)}"
            )
        serial_display = clean_text(nodes[0].get_text(" ", strip=True))
        source_position = int(serial_display.rstrip(".")) if re.fullmatch(r"\d+\.?", serial_display) else fallback_position
        if source_position in seen_positions:
            raise ValueError(f"Duplicate result source position: {source_position}")
        seen_positions.add(source_position)

        company_links: list[Tag] = []
        for node in nodes:
            company_links.extend(
                anchor
                for anchor in node.find_all("a", href=True)
                if str(anchor.get("href", "")).startswith("/company/")
            )
        if len(company_links) != 1:
            raise ValueError(
                f"Result row {source_position} must have exactly one company link"
            )
        company_link = company_links[0]
        company_name = clean_text(company_link.get_text(" ", strip=True))
        href = str(company_link["href"])
        path, scope = normalize_company_path(href)
        if not company_name:
            raise ValueError(f"Result row {source_position} company name is blank")
        previous_id = seen_paths.get(path)
        if previous_id is not None and previous_id != company_id:
            raise ValueError(f"Conflicting identities for company path {path}")
        seen_paths[path] = company_id

        display = clean_text(nodes[date_column].get_text(" ", strip=True))
        period = normalize_result_period(display)
        row_cells: list[ResultScreenCell] = []
        for column, node in enumerate(nodes):
            normalized = clean_text(node.get_text(" ", strip=True))
            cell = ResultScreenCell(
                dom_position,
                column,
                company_name,
                headers[column],
                _raw_text(node),
                normalized,
                _first_href(node),
                "company_link" if company_link.find_parent(["td", "th"]) is node else "data",
            )
            row_cells.append(cell)
            all_cells.append(cell)
        parsed_rows.append(
            ResultScreenRow(
                source_position,
                serial_display,
                company_id,
                company_name,
                href,
                path,
                scope,
                display,
                period,
                tuple(row_cells),
            )
        )

    captured = len(parsed_rows)
    is_complete = page_count == 1 and captured == result_count
    # An empty screen is valid only when its metadata also reports zero.
    if captured > result_count:
        raise ValueError("Captured row count exceeds Screener reported result count")
    return ParsedResultScreen(
        query,
        requested_url,
        result_count,
        page_number,
        page_count,
        headers,
        tuple(parsed_rows),
        tuple(all_cells),
        is_complete,
    )
