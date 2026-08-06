"""Pure parser for Screener's supplemental peer-table response."""

from __future__ import annotations

from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from ...core.nums import clean_text, decimal_text, decimal_values, missing_kind
from .company import SourceCell, _first_link, _node_text


METRICS_BY_TOOLTIP = {
    "Current Price": ("current_price", "INR", "1"),
    "Price to Earning": ("price_to_earnings", "RATIO", "1"),
    "Market Capitalization": ("market_cap", "INR_CRORE", "10000000"),
    "Dividend yield": ("dividend_yield", "PERCENT_POINT", "1"),
    "Net Profit latest quarter": ("net_profit_latest_quarter", "INR_CRORE", "10000000"),
    "YOY Quarterly profit growth": ("quarterly_profit_growth_yoy", "PERCENT_POINT", "1"),
    "Sales latest quarter": ("sales_latest_quarter", "INR_CRORE", "10000000"),
    "YOY Quarterly sales growth": ("quarterly_sales_growth_yoy", "PERCENT_POINT", "1"),
    "Return on capital employed": ("roce", "PERCENT_POINT", "1"),
}


@dataclass(frozen=True)
class PeerObservation:
    row_kind: str
    peer_source_company_id: str | None
    peer_name_raw: str
    peer_source_url: str | None
    is_subject_company: bool
    row: int
    column: int
    metric_code: str | None
    metric_label_raw: str
    display_value: str
    value_decimal: str | None
    unit_code: str | None
    scale_multiplier: str | None
    missing_kind: str | None


@dataclass
class ParsedPeerTable:
    heading: str = "Peer comparison"
    cells: list[SourceCell] = field(default_factory=list)
    observations: list[PeerObservation] = field(default_factory=list)


def _headers(table: Tag) -> list[Tag]:
    first_row = table.select_one("tbody > tr")
    if not isinstance(first_row, Tag):
        raise ValueError("Peer table has no header row")
    headers = first_row.find_all("th", recursive=False)
    if len(headers) < 3:
        raise ValueError("Peer table headers are missing")
    return headers


def parse_peer_table(
    content: bytes, expected_source_company_id: str | None = None
) -> ParsedPeerTable:
    """Parse a saved XHR response; a loading placeholder is never table data."""
    soup = BeautifulSoup(content, "html.parser")
    holder = soup.select_one("[data-page-results]")
    table = holder.find("table") if isinstance(holder, Tag) else None
    if not isinstance(table, Tag):
        raise ValueError("Supplemental peer response has no results table")

    headers = _headers(table)
    labels = [clean_text(header.get_text(" ", strip=True)) for header in headers]
    metrics: list[tuple[str | None, str | None, str | None]] = []
    for header in headers:
        tooltip = clean_text(str(header.get("data-tooltip", "")))
        metrics.append(METRICS_BY_TOOLTIP.get(tooltip, (None, None, None)))

    cells: list[SourceCell] = []
    for column, header in enumerate(headers):
        raw, display = _node_text(header)
        cells.append(
            SourceCell(
                0, column, None, display or None, raw, display,
                "column_header", _first_link(header),
                int(header.get("rowspan", 1)), int(header.get("colspan", 1)),
            )
        )

    observations: list[PeerObservation] = []
    company_rows = table.select("tbody > tr[data-row-company-id]")
    for row_position, row in enumerate(company_rows, start=1):
        nodes = row.find_all(["td", "th"], recursive=False)
        if len(nodes) != len(headers):
            raise ValueError(f"Peer row {row_position} has an unexpected column count")
        source_company_id = str(row.get("data-row-company-id"))
        name = clean_text(nodes[1].get_text(" ", strip=True))
        source_url = _first_link(nodes[1])
        is_subject = bool(
            expected_source_company_id
            and source_company_id == expected_source_company_id
        )
        for column, node in enumerate(nodes):
            raw, display = _node_text(node)
            cells.append(
                SourceCell(
                    row_position, column, name, labels[column], raw, display,
                    "row_header" if column < 2 else "data", _first_link(node),
                    int(node.get("rowspan", 1)), int(node.get("colspan", 1)),
                )
            )
            if column < 2:
                continue
            values = decimal_values(display)
            metric_code, unit_code, scale = metrics[column]
            observations.append(
                PeerObservation(
                    row_kind="company",
                    peer_source_company_id=source_company_id,
                    peer_name_raw=name,
                    peer_source_url=source_url,
                    is_subject_company=is_subject,
                    row=row_position,
                    column=column,
                    metric_code=metric_code,
                    metric_label_raw=labels[column],
                    display_value=display,
                    value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                    unit_code=unit_code,
                    scale_multiplier=scale,
                    missing_kind=missing_kind(display),
                )
            )

    footer = table.select_one("tfoot > tr")
    if isinstance(footer, Tag):
        row_position = len(company_rows) + 1
        nodes = footer.find_all(["td", "th"], recursive=False)
        if len(nodes) != len(headers):
            raise ValueError("Peer median row has an unexpected column count")
        name = clean_text(nodes[1].get_text(" ", strip=True))
        for column, node in enumerate(nodes):
            raw, display = _node_text(node)
            cells.append(
                SourceCell(
                    row_position, column, name, labels[column], raw, display,
                    "summary_label" if column < 2 else "summary_data",
                    _first_link(node), int(node.get("rowspan", 1)),
                    int(node.get("colspan", 1)),
                )
            )
            if column < 2:
                continue
            values = decimal_values(display)
            metric_code, unit_code, scale = metrics[column]
            observations.append(
                PeerObservation(
                    row_kind="median",
                    peer_source_company_id=None,
                    peer_name_raw=name,
                    peer_source_url=None,
                    is_subject_company=False,
                    row=row_position,
                    column=column,
                    metric_code=metric_code,
                    metric_label_raw=labels[column],
                    display_value=display,
                    value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                    unit_code=unit_code,
                    scale_multiplier=scale,
                    missing_kind=missing_kind(display),
                )
            )

    if not company_rows or not observations:
        raise ValueError("Peer response has no peer observations")
    return ParsedPeerTable(cells=cells, observations=observations)
