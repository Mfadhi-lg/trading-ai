"""Source-faithful parser for a Trendlyne monthly-MF holdings page."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from ...core.nums import decimal_text, decimal_values, missing_kind as numeric_missing_kind
from .urls import monthly_mf_url_parts


def _display(node: Tag | None) -> str | None:
    if node is None:
        return None
    return " ".join(node.get_text(" ", strip=True).split())


def month_to_period(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%b %Y").strftime("%Y-%m")
    except ValueError:
        return None


@dataclass(frozen=True)
class SourceField:
    position: int
    key: str
    raw_text: str
    display_text: str
    link_url: str | None
    role: str
    visibility_state: str = "visible"


@dataclass(frozen=True)
class SourceCell:
    row: int
    column: int
    row_label: str | None
    column_label: str | None
    raw_text: str
    display_text: str
    link_url: str | None
    role: str
    rowspan: int = 1
    colspan: int = 1
    visibility_state: str = "visible"


@dataclass(frozen=True)
class FundSourceRow:
    row: int
    scheme_name_raw: str
    source_scheme_id: str
    source_url: str


@dataclass(frozen=True)
class MonthLink:
    position: int
    raw_text: str
    display_text: str
    source_period: str
    source_url: str
    source_url_period_raw: str
    source_company_id: str
    source_symbol: str
    source_slug: str
    is_active: bool


@dataclass(frozen=True)
class FundObservation:
    table: int
    row: int
    column: int
    source_scheme_id: str
    scheme_name_raw: str
    metric_code: str
    period_role: str
    period_label_raw: str
    source_period: str
    period_end: str
    period_type: str
    display_value: str
    value_decimal: str | None
    unit_code: str
    scale_multiplier: str
    missing_kind: str | None


@dataclass(frozen=True)
class MfPageSummary:
    headline_raw: str
    headline_display_text: str
    headline_period_label_raw: str
    headline_buyers_display: str
    headline_buyers: str
    headline_sellers_display: str
    headline_sellers: str
    headline_net_change_display: str
    headline_net_change: str
    table_total_shares_display: str
    table_total_shares: str
    table_total_change_display: str
    table_total_change: str
    computed_buyers: str
    computed_sellers: str
    computed_holders: str
    computed_net_change: str
    period_label_raw: str
    source_period: str
    period_end: str


@dataclass(frozen=True)
class ParsedMonthlyMfPage:
    company_name_raw: str
    source_company_id: str
    source_symbol: str
    source_slug: str
    bse_code: str | None
    canonical_url: str
    canonical_source_company_id: str
    canonical_symbol: str
    canonical_period: str
    selected_month_raw: str | None
    active_month_raw: str | None
    active_month_url: str | None
    active_url_period_raw: str | None
    heading_raw: str | None
    heading_month_raw: str | None
    resolved_source_period: str | None
    identity_fields: tuple[SourceField, ...]
    period_fields: tuple[SourceField, ...]
    month_links: tuple[MonthLink, ...]
    fund_cells: tuple[SourceCell, ...]
    fund_rows: tuple[FundSourceRow, ...]
    fund_observations: tuple[FundObservation, ...]
    summary_fields: tuple[SourceField, ...]
    summary: MfPageSummary
    fund_header_row_count: int
    fund_footer_row_count: int
    fund_logical_column_count: int


def _node_text(node: Tag) -> tuple[str, str]:
    return node.get_text("", strip=False), _display(node) or ""


def _first_link(node: Tag) -> str | None:
    link = node.find("a", href=True)
    return str(link["href"]) if isinstance(link, Tag) else None


def _placed_cells(rows: list[Tag], row_offset: int) -> list[tuple[int, int, Tag]]:
    """Place physical DOM cells on their logical grid while retaining spans."""
    placed: list[tuple[int, int, Tag]] = []
    occupied_until: dict[int, int] = {}
    for local_row, row in enumerate(rows):
        logical_row = row_offset + local_row
        column = 0
        for node in row.find_all(["th", "td"], recursive=False):
            while occupied_until.get(column, -1) > logical_row:
                column += 1
            rowspan = int(node.get("rowspan", 1))
            colspan = int(node.get("colspan", 1))
            placed.append((logical_row, column, node))
            if rowspan > 1:
                for covered_column in range(column, column + colspan):
                    occupied_until[covered_column] = max(
                        occupied_until.get(covered_column, -1),
                        logical_row + rowspan,
                    )
            column += colspan
    return placed


def _scheme_id(source_url: str | None) -> str | None:
    if not source_url:
        return None
    match = re.fullmatch(r"/mutual-fund/nav/(\d+)/[^/]+/", urlparse(source_url).path)
    return match.group(1) if match else None


def _parse_month_links(soup: BeautifulSoup) -> tuple[MonthLink, ...]:
    nodes = soup.select(".mf-sh-month-dd-menu a[href]")
    if not nodes:
        raise ValueError("Trendlyne month history links are missing")
    links: list[MonthLink] = []
    for position, node in enumerate(nodes):
        raw_text, display_text = _node_text(node)
        source_url = str(node["href"])
        company_id, symbol, url_period, slug = monthly_mf_url_parts(source_url)
        try:
            display_period = datetime.strptime(display_text, "%b %Y").strftime(
                "%Y-%m"
            )
            url_source_period = datetime.strptime(url_period, "%b-%Y").strftime(
                "%Y-%m"
            )
        except ValueError as exc:
            raise ValueError(
                f"Unparseable Trendlyne month history link at position {position}"
            ) from exc
        if display_period != url_source_period:
            raise ValueError(
                f"Trendlyne month history label/URL mismatch at position {position}"
            )
        links.append(
            MonthLink(
                position=position,
                raw_text=raw_text,
                display_text=display_text,
                source_period=display_period,
                source_url=source_url,
                source_url_period_raw=url_period,
                source_company_id=company_id,
                source_symbol=symbol,
                source_slug=slug,
                is_active="active" in (node.get("class") or []),
            )
        )
    if len({link.source_period for link in links}) != len(links):
        raise ValueError("Trendlyne month history contains duplicate periods")
    return tuple(links)


def fund_column_contract(
    logical_width: int,
) -> dict[int, tuple[str, str, str, str]]:
    """Return the strict metric contract for one current and N prior months."""
    if logical_width < 8 or (logical_width - 6) % 2:
        raise ValueError(
            f"Trendlyne fund table width {logical_width} cannot represent month groups"
        )
    contract = {
        1: ("aum", "current", "INR_CRORE", "10000000"),
        2: ("aum_percentage", "current", "PERCENT_POINT", "1"),
        3: ("shares_held", "current", "COUNT", "1"),
        4: ("month_change", "current", "COUNT", "1"),
        5: ("month_change_percentage", "current", "PERCENT_POINT", "1"),
    }
    for column in range(6, logical_width, 2):
        contract[column] = ("shares_held", "comparison", "COUNT", "1")
        contract[column + 1] = (
            "month_change_percentage",
            "comparison",
            "PERCENT_POINT",
            "1",
        )
    return contract


def _fund_period(label: str | None) -> tuple[str, str, str]:
    period_label = (label or "").split(" / ", 1)[0]
    try:
        parsed = datetime.strptime(period_label, "%b-%Y")
    except ValueError as exc:
        raise ValueError(f"Unparseable Trendlyne fund period {period_label!r}") from exc
    source_period = parsed.strftime("%Y-%m")
    period_end = parsed.replace(day=monthrange(parsed.year, parsed.month)[1])
    return period_label, source_period, period_end.strftime("%Y-%m-%d")


def _normalized_fund_value(
    display_value: str, unit_code: str
) -> tuple[str | None, str | None]:
    missing_token = display_value
    if unit_code == "PERCENT_POINT" and missing_token.endswith("%"):
        missing_token = missing_token[:-1]
    cell_missing_kind = numeric_missing_kind(missing_token)
    if cell_missing_kind is not None:
        return None, cell_missing_kind
    values = decimal_values(display_value)
    if len(values) != 1:
        return None, "parse_error"
    return decimal_text(values[0]), None


def _normalize_fund_observations(
    cells: tuple[SourceCell, ...], fund_rows: tuple[FundSourceRow, ...]
) -> tuple[FundObservation, ...]:
    data_cells = {
        (cell.row, cell.column): cell for cell in cells if cell.role == "data"
    }
    logical_width = max((cell.column + cell.colspan for cell in cells), default=0)
    column_contract = fund_column_contract(logical_width)
    observations: list[FundObservation] = []
    for fund_row in fund_rows:
        for column, (
            metric_code,
            period_role,
            unit_code,
            scale_multiplier,
        ) in column_contract.items():
            cell = data_cells.get((fund_row.row, column))
            if cell is None:
                raise ValueError(
                    f"Trendlyne fund row {fund_row.row} is missing data column {column}"
                )
            period_label, source_period, period_end = _fund_period(cell.column_label)
            value_decimal, cell_missing_kind = _normalized_fund_value(
                cell.display_text, unit_code
            )
            observations.append(
                FundObservation(
                    table=0,
                    row=fund_row.row,
                    column=column,
                    source_scheme_id=fund_row.source_scheme_id,
                    scheme_name_raw=fund_row.scheme_name_raw,
                    metric_code=metric_code,
                    period_role=period_role,
                    period_label_raw=period_label,
                    source_period=source_period,
                    period_end=period_end,
                    period_type="month",
                    display_value=cell.display_text,
                    value_decimal=value_decimal,
                    unit_code=unit_code,
                    scale_multiplier=scale_multiplier,
                    missing_kind=cell_missing_kind,
                )
            )
    return tuple(observations)


_HEADLINE_PATTERN = re.compile(
    r"(?P<buyers>[+-]?[\d,]+)\s+MFs?\s+bought\s+and\s+"
    r"(?P<sellers>[+-]?[\d,]+)\s+MFs?\s+sold\s+.+?\s+in\s+the\s+month\s+of\s+"
    r"(?P<period>[A-Z][a-z]{2}\s+\d{4})\s+for\s+a\s+net\s+change\s+of\s+"
    r"(?P<net_change>[+-]?[\d,]+)\s+stocks\b",
    re.IGNORECASE,
)


def _required_count(display_value: str, source: str) -> str:
    value, cell_missing_kind = _normalized_fund_value(display_value, "COUNT")
    if value is None or cell_missing_kind is not None:
        raise ValueError(f"Trendlyne {source} is not a numeric count")
    return value


def _parse_summary(
    soup: BeautifulSoup,
    fund_cells: tuple[SourceCell, ...],
    fund_observations: tuple[FundObservation, ...],
) -> tuple[tuple[SourceField, ...], MfPageSummary]:
    headline_node = soup.select_one("#share-change-analysis h2")
    if headline_node is None:
        raise ValueError("Trendlyne mutual-fund headline summary is missing")
    headline_raw, headline_display = _node_text(headline_node)
    headline_match = _HEADLINE_PATTERN.fullmatch(headline_display)
    if headline_match is None:
        raise ValueError("Trendlyne mutual-fund headline summary structure changed")

    footer = {
        cell.column: cell
        for cell in fund_cells
        if cell.role in {"summary_label", "summary_data"}
    }
    logical_width = max((cell.column + cell.colspan for cell in fund_cells), default=0)
    if set(footer) != set(range(logical_width)) or footer[0].display_text != "Total:":
        raise ValueError("Trendlyne mutual-fund total row structure changed")
    table_shares_display = footer[3].display_text
    table_change_display = footer[4].display_text
    table_shares = _required_count(table_shares_display, "table total shares")
    table_change = _required_count(table_change_display, "table total change")

    current_changes = [
        observation
        for observation in fund_observations
        if observation.metric_code == "month_change"
        and observation.period_role == "current"
    ]
    current_shares = [
        observation
        for observation in fund_observations
        if observation.metric_code == "shares_held"
        and observation.period_role == "current"
    ]
    if not current_changes or len(current_changes) != len(current_shares):
        raise ValueError("Trendlyne current-period fund rows are incomplete")
    numeric_changes = [
        Decimal(observation.value_decimal)
        for observation in current_changes
        if observation.value_decimal is not None
    ]
    computed_buyers = sum(value > 0 for value in numeric_changes)
    computed_sellers = sum(value < 0 for value in numeric_changes)
    computed_holders = sum(
        observation.value_decimal is not None for observation in current_shares
    )
    computed_net_change = sum(numeric_changes, Decimal("0"))
    period_labels = {observation.period_label_raw for observation in current_changes}
    source_periods = {observation.source_period for observation in current_changes}
    period_ends = {observation.period_end for observation in current_changes}
    if len(period_labels) != 1 or len(source_periods) != 1 or len(period_ends) != 1:
        raise ValueError("Trendlyne summary fund rows span multiple current periods")

    summary_fields = (
        SourceField(
            0,
            "headline_summary",
            headline_raw,
            headline_display,
            None,
            "untrusted_source_summary",
        ),
    )
    return summary_fields, MfPageSummary(
        headline_raw=headline_raw,
        headline_display_text=headline_display,
        headline_period_label_raw=headline_match.group("period"),
        headline_buyers_display=headline_match.group("buyers"),
        headline_buyers=_required_count(
            headline_match.group("buyers"), "headline buyer count"
        ),
        headline_sellers_display=headline_match.group("sellers"),
        headline_sellers=_required_count(
            headline_match.group("sellers"), "headline seller count"
        ),
        headline_net_change_display=headline_match.group("net_change"),
        headline_net_change=_required_count(
            headline_match.group("net_change"), "headline net change"
        ),
        table_total_shares_display=table_shares_display,
        table_total_shares=table_shares,
        table_total_change_display=table_change_display,
        table_total_change=table_change,
        computed_buyers=str(computed_buyers),
        computed_sellers=str(computed_sellers),
        computed_holders=str(computed_holders),
        computed_net_change=decimal_text(computed_net_change),
        period_label_raw=next(iter(period_labels)),
        source_period=next(iter(source_periods)),
        period_end=next(iter(period_ends)),
    )


def _parse_fund_table(
    soup: BeautifulSoup,
) -> tuple[tuple[SourceCell, ...], tuple[FundSourceRow, ...], int, int, int]:
    tables = soup.select(".mf-shareholding-table table")
    if len(tables) != 1:
        raise ValueError(
            f"Trendlyne fund table count is {len(tables)}; expected exactly one"
        )
    table = tables[0]
    head_rows = table.select("thead > tr")
    body_rows = table.select("tbody > tr")
    foot_rows = table.select("tfoot > tr")
    if len(head_rows) != 2 or not body_rows or len(foot_rows) != 1:
        raise ValueError("Trendlyne fund table header/body/footer structure changed")

    header_placed = _placed_cells(head_rows, 0)
    logical_width = max(
        column + int(node.get("colspan", 1))
        for _, column, node in header_placed
    )
    if logical_width < 2:
        raise ValueError("Trendlyne fund table has no data columns")

    # Build the effective hierarchical label for each logical column. This keeps
    # the two period groups distinct even where leaf labels repeat.
    header_labels: list[list[str]] = [[] for _ in range(logical_width)]
    for _, column, node in header_placed:
        display = _display(node) or ""
        for covered_column in range(column, column + int(node.get("colspan", 1))):
            if display:
                header_labels[covered_column].append(display)
    column_labels = [" / ".join(parts) for parts in header_labels]

    cells: list[SourceCell] = []
    for row, column, node in header_placed:
        raw, display = _node_text(node)
        cells.append(
            SourceCell(
                row=row,
                column=column,
                row_label=None,
                column_label=display or None,
                raw_text=raw,
                display_text=display,
                link_url=_first_link(node),
                role="column_header",
                rowspan=int(node.get("rowspan", 1)),
                colspan=int(node.get("colspan", 1)),
            )
        )

    fund_rows: list[FundSourceRow] = []
    body_offset = len(head_rows)
    for body_position, row in enumerate(body_rows):
        logical_row = body_offset + body_position
        nodes = row.find_all(["th", "td"], recursive=False)
        if len(nodes) != logical_width:
            raise ValueError(
                f"Trendlyne fund row {body_position} has {len(nodes)} cells; "
                f"expected {logical_width}"
            )
        scheme_name = _display(nodes[0]) or ""
        source_url = _first_link(nodes[0])
        source_scheme_id = _scheme_id(source_url)
        if not scheme_name or not source_url or not source_scheme_id:
            raise ValueError(
                f"Trendlyne fund row {body_position} has incomplete scheme identity"
            )
        fund_rows.append(
            FundSourceRow(
                row=logical_row,
                scheme_name_raw=scheme_name,
                source_scheme_id=source_scheme_id,
                source_url=source_url,
            )
        )
        for column, node in enumerate(nodes):
            raw, display = _node_text(node)
            cells.append(
                SourceCell(
                    row=logical_row,
                    column=column,
                    row_label=scheme_name,
                    column_label=column_labels[column],
                    raw_text=raw,
                    display_text=display,
                    link_url=_first_link(node),
                    role="scheme" if column == 0 else "data",
                    rowspan=int(node.get("rowspan", 1)),
                    colspan=int(node.get("colspan", 1)),
                )
            )

    footer_offset = body_offset + len(body_rows)
    for footer_position, row in enumerate(foot_rows):
        logical_row = footer_offset + footer_position
        nodes = row.find_all(["th", "td"], recursive=False)
        if len(nodes) != logical_width:
            raise ValueError(
                f"Trendlyne total row has {len(nodes)} cells; expected {logical_width}"
            )
        row_label = _display(nodes[0]) or ""
        for column, node in enumerate(nodes):
            raw, display = _node_text(node)
            cells.append(
                SourceCell(
                    row=logical_row,
                    column=column,
                    row_label=row_label,
                    column_label=column_labels[column],
                    raw_text=raw,
                    display_text=display,
                    link_url=_first_link(node),
                    role="summary_label" if column == 0 else "summary_data",
                    rowspan=int(node.get("rowspan", 1)),
                    colspan=int(node.get("colspan", 1)),
                )
            )

    if len({row.source_scheme_id for row in fund_rows}) != len(fund_rows):
        raise ValueError("Trendlyne fund table contains duplicate scheme IDs")
    return (
        tuple(cells),
        tuple(fund_rows),
        len(head_rows),
        len(foot_rows),
        logical_width,
    )


def parse_monthly_mf_page(content: bytes) -> ParsedMonthlyMfPage:
    """Parse identity/month fields, fund source cells, and fund observations."""
    soup = BeautifulSoup(content, "html.parser")

    card = soup.select_one(".stock_info_card[data-myid]")
    company_name = _display(soup.select_one(".stock_info_heading"))
    exchange_text = _display(soup.select_one(".stock_exchange_details"))
    canonical_node = soup.select_one("link[rel='canonical'][href]")
    selected = _display(soup.select_one(".choose-month-styles button.dropdown-toggle"))
    active = soup.select_one(".mf-sh-month-dd-menu a.active[href]")
    heading = _display(soup.select_one("#share-change-analysis h1"))

    if card is None or not card.get("data-myid"):
        raise ValueError("Trendlyne company identity is missing")
    if not company_name or not exchange_text:
        raise ValueError("Trendlyne displayed company identity is incomplete")
    if canonical_node is None:
        raise ValueError("Trendlyne canonical URL is missing")

    canonical_url = str(canonical_node["href"])
    canonical_id, canonical_symbol, canonical_period, canonical_slug = (
        monthly_mf_url_parts(canonical_url)
    )
    symbol_match = re.search(r"\bNSE\s*:\s*([A-Z0-9&.-]+)", exchange_text)
    bse_match = re.search(r"\bBSE\s*:\s*(\d+)", exchange_text)
    if not symbol_match:
        raise ValueError("Trendlyne NSE symbol is missing")

    active_month = _display(active)
    active_url = str(active["href"]) if active is not None else None
    active_url_period = None
    if active_url:
        try:
            _, _, active_url_period, _ = monthly_mf_url_parts(active_url)
        except ValueError:
            active_url_period = None
    heading_match = re.search(r"\b([A-Z][a-z]{2} \d{4})\b", heading or "")
    heading_month = heading_match.group(1) if heading_match else None
    resolved = month_to_period(selected)
    month_links = _parse_month_links(soup)
    (
        fund_cells,
        fund_rows,
        fund_header_row_count,
        fund_footer_row_count,
        fund_logical_column_count,
    ) = _parse_fund_table(soup)
    fund_observations = _normalize_fund_observations(fund_cells, fund_rows)
    summary_fields, summary = _parse_summary(soup, fund_cells, fund_observations)

    identity_fields = (
        SourceField(0, "company_name", company_name, company_name, None, "identity"),
        SourceField(
            1,
            "trendlyne_company_id",
            str(card["data-myid"]),
            str(card["data-myid"]),
            None,
            "source_identifier",
            "metadata",
        ),
        SourceField(
            2,
            "NSE",
            symbol_match.group(1),
            symbol_match.group(1),
            None,
            "security_identifier",
        ),
        SourceField(
            3,
            "BSE",
            bse_match.group(1) if bse_match else "",
            bse_match.group(1) if bse_match else "",
            None,
            "security_identifier",
        ),
        SourceField(
            4,
            "canonical_url",
            canonical_url,
            canonical_url,
            canonical_url,
            "canonical_link",
            "metadata",
        ),
    )
    period_fields = (
        SourceField(0, "selected_month", selected or "", selected or "", None, "period"),
        SourceField(
            1,
            "active_month",
            active_month or "",
            active_month or "",
            active_url,
            "period_link",
        ),
        SourceField(2, "page_heading", heading or "", heading or "", None, "heading"),
    )
    return ParsedMonthlyMfPage(
        company_name_raw=company_name,
        source_company_id=str(card["data-myid"]),
        source_symbol=symbol_match.group(1),
        source_slug=canonical_slug,
        bse_code=bse_match.group(1) if bse_match else None,
        canonical_url=canonical_url,
        canonical_source_company_id=canonical_id,
        canonical_symbol=canonical_symbol,
        canonical_period=canonical_period,
        selected_month_raw=selected,
        active_month_raw=active_month,
        active_month_url=active_url,
        active_url_period_raw=active_url_period,
        heading_raw=heading,
        heading_month_raw=heading_month,
        resolved_source_period=resolved,
        identity_fields=identity_fields,
        period_fields=period_fields,
        month_links=month_links,
        fund_cells=fund_cells,
        fund_rows=fund_rows,
        fund_observations=fund_observations,
        summary_fields=summary_fields,
        summary=summary,
        fund_header_row_count=fund_header_row_count,
        fund_footer_row_count=fund_footer_row_count,
        fund_logical_column_count=fund_logical_column_count,
    )
