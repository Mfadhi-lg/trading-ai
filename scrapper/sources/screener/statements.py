"""Source-specific Screener statement parsing."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from bs4 import BeautifulSoup, Tag
from ...core.nums import clean_text, decimal_text, decimal_values, missing_kind, unit_for_financial_line, unit_for_ratio_line
from .models import SourceCell, FinancialObservation
from .parsing import _node_text, _first_link


LINE_ITEM_CODES = {
    "sales": "sales",
    "expenses": "expenses",
    "operating profit": "operating_profit",
    "opm %": "operating_margin_pct",
    "other income": "other_income",
    "interest": "interest",
    "depreciation": "depreciation",
    "profit before tax": "profit_before_tax",
    "tax %": "tax_pct",
    "net profit": "net_profit",
    "eps in rs": "eps_inr",
    "dividend payout %": "dividend_payout_pct",
    "raw pdf": "raw_pdf",
    # Balance sheet
    "equity capital": "equity_capital",
    "reserves": "reserves",
    "borrowings": "borrowings",
    "other liabilities": "other_liabilities",
    "total liabilities": "total_liabilities",
    "fixed assets": "fixed_assets",
    "cwip": "cwip",
    "investments": "investments",
    "other assets": "other_assets",
    "total assets": "total_assets",
    # Cash flow
    "cash from operating activity": "cash_from_operating_activity",
    "cash from investing activity": "cash_from_investing_activity",
    "cash from financing activity": "cash_from_financing_activity",
    "net cash flow": "net_cash_flow",
    "free cash flow": "free_cash_flow",
    "cfo/op": "cfo_op_pct",
    # Annual ratios (kept distinct from the top snapshot ratio panel)
    "debtor days": "debtor_days",
    "inventory days": "inventory_days",
    "days payable": "days_payable",
    "cash conversion cycle": "cash_conversion_cycle",
    "working capital days": "working_capital_days",
    "roce %": "roce_pct",
}


def _fiscal_year(period_end: str | None) -> int | None:
    if not period_end:
        return None
    date = datetime.strptime(period_end, "%Y-%m-%d").date()
    return date.year if date.month <= 3 else date.year + 1


def _canonical_line_label(label: str) -> str:
    return re.sub(r"\s*\+\s*$", "", clean_text(label)).lower()


def _expandable_marker(row_header: Tag) -> tuple[bool, str | None]:
    """Detect a click-to-expand schedule marker on a statement row header.

    Screener renders drill-down rows as a `Company.showSchedule('<key>', ...)`
    button ending in a blue `+`. Phase 1 records only the marker and its schedule
    key; the expanded schedule dataset itself is a deferred component.
    """
    button = row_header.find("button")
    if not isinstance(button, Tag):
        return False, None
    match = re.search(r"showSchedule\('([^']*)'", str(button.get("onclick", "")))
    return True, match.group(1) if match else None


def _quarter_period(label: str, date_key: str | None) -> tuple[str, str | None]:
    return "quarter", date_key


def _annual_period(label: str, date_key: str | None) -> tuple[str, str | None]:
    if clean_text(label).upper() == "TTM" or (date_key or "").upper() == "TTM":
        return "ttm", None
    return "fiscal_year", date_key


def _parse_statement_table(
    soup: BeautifulSoup,
    section_selector: str,
    section_code: str,
    default_heading: str,
    resolve_period: Callable[[str, str | None], tuple[str, str | None]],
    label: str,
    unit_resolver: Callable[[str, object | None], tuple[str, str]] = unit_for_financial_line,
) -> tuple[str, list[SourceCell], list[FinancialObservation]]:
    """Parse one annual/quarterly statement table into faithful cells + observations."""
    section = soup.select_one(section_selector)
    if not isinstance(section, Tag):
        raise ValueError(f"{label} section is missing")
    heading_node = section.find("h2")
    heading = clean_text(
        heading_node.get_text(" ", strip=True) if heading_node else default_heading
    )
    table = section.find("table")
    if not isinstance(table, Tag):
        raise ValueError(f"{label} table is missing")
    header_nodes = table.select("thead tr th")
    if len(header_nodes) < 2:
        raise ValueError(f"{label} headers are missing")
    period_labels = [clean_text(node.get_text(" ", strip=True)) for node in header_nodes[1:]]
    date_keys = [
        str(node.get("data-date-key")) if node.get("data-date-key") else None
        for node in header_nodes[1:]
    ]

    cells: list[SourceCell] = []
    for column, node in enumerate(header_nodes):
        raw, display = _node_text(node)
        cells.append(
            SourceCell(0, column, None, display or None, raw, display, "column_header", _first_link(node), int(node.get("rowspan", 1)), int(node.get("colspan", 1)))
        )

    financials: list[FinancialObservation] = []
    rows = table.select("tbody > tr")
    for row_position, row in enumerate(rows, start=1):
        row_nodes = row.find_all(["td", "th"], recursive=False)
        if not row_nodes:
            continue
        line_item = clean_text(row_nodes[0].get_text(" ", strip=True))
        canonical_label = _canonical_line_label(line_item)
        line_code = LINE_ITEM_CODES.get(canonical_label)
        is_expandable, schedule_key = _expandable_marker(row_nodes[0])
        # The unit is a per-row property. Most rows are decided by their label,
        # but a row such as cash flow's CFO/OP is a percentage only in its cells.
        data_texts = [clean_text(node.get_text(" ", strip=True)) for node in row_nodes[1:]]
        unit_code, scale = unit_resolver(line_item, data_texts)
        for column, node in enumerate(row_nodes):
            raw, display = _node_text(node)
            column_label = "" if column == 0 else period_labels[column - 1] if column - 1 < len(period_labels) else None
            if column == 0:
                cell_role = "row_header_expandable" if is_expandable else "row_header"
            else:
                cell_role = "data"
            cells.append(
                SourceCell(
                    row_position,
                    column,
                    line_item,
                    column_label,
                    raw,
                    display,
                    cell_role,
                    _first_link(node),
                    int(node.get("rowspan", 1)),
                    int(node.get("colspan", 1)),
                )
            )
            if column == 0 or column - 1 >= len(period_labels):
                continue
            period_label = period_labels[column - 1]
            period_type, period_end = resolve_period(period_label, date_keys[column - 1])
            values = decimal_values(display)
            financials.append(
                FinancialObservation(
                    row=row_position,
                    column=column,
                    section_code=section_code,
                    line_item_raw=line_item,
                    line_item_code=line_code,
                    period_label_raw=period_label,
                    period_end=period_end,
                    period_type=period_type,
                    fiscal_year=_fiscal_year(period_end),
                    display_value=display,
                    value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                    unit_code=unit_code,
                    scale_multiplier=scale,
                    missing_kind=missing_kind(display),
                    is_expandable=is_expandable,
                    schedule_key=schedule_key,
                )
            )
    if not financials:
        raise ValueError(f"{label} table has no observations")
    return heading, cells, financials


def _parse_quarters(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceCell], list[FinancialObservation]]:
    return _parse_statement_table(
        soup,
        "section#quarters",
        "quarters",
        "Quarterly Results",
        _quarter_period,
        "Quarterly results",
    )


def _parse_profit_loss(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceCell], list[FinancialObservation]]:
    return _parse_statement_table(
        soup,
        "section#profit-loss",
        "pnl",
        "Profit & Loss",
        _annual_period,
        "Profit and loss",
    )


def _parse_balance_sheet(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceCell], list[FinancialObservation]]:
    return _parse_statement_table(
        soup,
        "section#balance-sheet",
        "balance_sheet",
        "Balance Sheet",
        _annual_period,
        "Balance sheet",
    )


def _parse_cash_flow(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceCell], list[FinancialObservation]]:
    return _parse_statement_table(
        soup,
        "section#cash-flow",
        "cash_flow",
        "Cash Flows",
        _annual_period,
        "Cash flow",
    )


def _parse_annual_ratios(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceCell], list[FinancialObservation]]:
    """Parse `section#ratios` (annual efficiency/return ratios).

    This is a separate section from the top snapshot ratio panel: its rows land
    in `financial_observations` under `section_code='ratios'`, never in the
    `company_metric_observations` panel, so the two cannot collide. Days,
    percentages, ratios and counts are distinguished per row by ``unit_resolver``.
    """
    return _parse_statement_table(
        soup,
        "section#ratios",
        "ratios",
        "Ratios",
        _annual_period,
        "Annual ratios",
        unit_for_ratio_line,
    )

# Preserve the legacy facade identity for existing serialized callables.
_fiscal_year.__module__ = "scrapper.sources.screener.company"
_canonical_line_label.__module__ = "scrapper.sources.screener.company"
_expandable_marker.__module__ = "scrapper.sources.screener.company"
_quarter_period.__module__ = "scrapper.sources.screener.company"
_annual_period.__module__ = "scrapper.sources.screener.company"
_parse_statement_table.__module__ = "scrapper.sources.screener.company"
_parse_quarters.__module__ = "scrapper.sources.screener.company"
_parse_profit_loss.__module__ = "scrapper.sources.screener.company"
_parse_balance_sheet.__module__ = "scrapper.sources.screener.company"
_parse_cash_flow.__module__ = "scrapper.sources.screener.company"
_parse_annual_ratios.__module__ = "scrapper.sources.screener.company"
