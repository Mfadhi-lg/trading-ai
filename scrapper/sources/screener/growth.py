"""Source-specific Screener growth parsing."""

from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag
from ...core.nums import decimal_text, decimal_values, missing_kind
from .models import SourceCell, GrowthObservation
from .parsing import _node_text


GROWTH_METRIC_CODES = {
    "compounded sales growth": "compounded_sales_growth",
    "compounded profit growth": "compounded_profit_growth",
    "stock price cagr": "stock_price_cagr",
    "return on equity": "return_on_equity",
}


GROWTH_PERIOD_CODES = {
    "10 years": "10y",
    "5 years": "5y",
    "3 years": "3y",
    "1 year": "1y",
    "ttm": "ttm",
    "last year": "last_year",
}


def _parse_growth(
    soup: BeautifulSoup,
) -> tuple[list[SourceCell], list[GrowthObservation]]:
    """Parse the four compounded-growth mini-tables inside the P&L section."""
    section = soup.select_one("section#profit-loss")
    if not isinstance(section, Tag):
        raise ValueError("Growth blocks section (profit-loss) is missing")
    tables = section.select("table.ranges-table")
    if not tables:
        raise ValueError("Growth blocks are missing")

    cells: list[SourceCell] = []
    observations: list[GrowthObservation] = []
    for block, table in enumerate(tables):
        header = table.find("th")
        if not isinstance(header, Tag):
            raise ValueError(f"Growth block {block} has no header")
        metric_raw, metric_label = _node_text(header)
        metric_code = GROWTH_METRIC_CODES.get(metric_label.lower())
        cells.append(
            SourceCell(
                0, 0, None, None, metric_raw, metric_label, "column_header", None,
                int(header.get("rowspan", 1)), int(header.get("colspan", 1)), block,
            )
        )
        data_rows = [
            tr for tr in table.find_all("tr", recursive=False) if tr.find("td")
        ]
        for row_position, tr in enumerate(data_rows, start=1):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 2:
                raise ValueError(f"Growth block {block} row {row_position} is malformed")
            label_raw, label_display = _node_text(tds[0])
            value_raw, value_display = _node_text(tds[1])
            period_label = re.sub(r":\s*$", "", label_display)
            period_code = GROWTH_PERIOD_CODES.get(period_label.lower())
            cells.append(
                SourceCell(
                    row_position, 0, metric_label, None, label_raw, label_display,
                    "row_header", None, 1, 1, block,
                )
            )
            cells.append(
                SourceCell(
                    row_position, 1, metric_label, period_label, value_raw,
                    value_display, "data", None, 1, 1, block,
                )
            )
            values = decimal_values(value_display)
            if values:
                value_decimal: str | None = decimal_text(values[0])
                cell_missing = None
            else:
                # A bare "%" or blank cell is unavailable, never a zero value.
                value_decimal = None
                cell_missing = missing_kind(value_display) or "blank"
            observations.append(
                GrowthObservation(
                    block=block,
                    row=row_position,
                    metric_code=metric_code,
                    metric_label_raw=metric_label,
                    period_code=period_code,
                    period_label_raw=period_label,
                    display_value=value_display,
                    value_decimal=value_decimal,
                    unit_code="PERCENT_POINT",
                    missing_kind=cell_missing,
                )
            )
    if not observations:
        raise ValueError("Growth blocks have no observations")
    return cells, observations

# Preserve the legacy facade identity for existing serialized callables.
_parse_growth.__module__ = "scrapper.sources.screener.company"
