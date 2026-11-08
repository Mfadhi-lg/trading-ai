"""Source-specific Screener metrics parsing helpers."""

from __future__ import annotations

import json
from bs4 import BeautifulSoup, Tag
from ...core.nums import clean_text, decimal_text, decimal_values, missing_kind, unit_for_metric
from .models import Metric, SourceField
from .parsing import _node_text, _first_link


METRIC_CODES = {
    "market cap": "market_cap",
    "current price": "current_price",
    "high / low": "high_low",
    "stock p/e": "stock_pe",
    "book value": "book_value",
    "dividend yield": "dividend_yield",
    "roce": "roce",
    "roe": "roe",
    "face value": "face_value",
    # Custom quick ratios
    "profit after tax": "profit_after_tax",
    "promoter holding": "promoter_holding",
    "pledged percentage": "pledged_percentage",
    "qtr p/e": "qtr_pe",
    "price to quarterly earning": "price_to_quarterly_earning",
    "qtr profit var": "qtr_profit_var",
    "yoy quarterly profit growth": "yoy_quarterly_profit_growth",
    "eps": "eps",
    "debt to equity": "debt_to_equity",
    "change in prom hold": "change_in_promoter_holding",
    "change in promoter holding": "change_in_promoter_holding",
    "roce3yr avg": "roce_3yr_avg",
    "roce 5yr": "roce_5yr",
    "average return on capital employed 5years": "roce_5yr_avg",
    "peg ratio": "peg_ratio",
    "debt": "debt",
    "industry pe": "industry_pe",
    "roe 5yr": "roe_5yr",
    "average return on equity 5years": "roe_5yr_avg",
    "roe 3yr": "roe_3yr",
    "average return on equity 3years": "roe_3yr_avg",
    "intrinsic value": "intrinsic_value",
    "3yrs pe": "pe_3yrs",
    "historical pe 3years": "pe_3yrs_historical",
}


def _parse_ratios(soup: BeautifulSoup) -> tuple[list[SourceField], list[Metric]]:
    ratio_list = soup.select_one("#top-ratios")
    if not isinstance(ratio_list, Tag):
        raise ValueError("Top ratio panel is missing")
    fields: list[SourceField] = []
    metrics: list[Metric] = []
    for position, item in enumerate(ratio_list.find_all("li", recursive=False)):
        label_node = item.select_one(".name")
        value_node = item.select_one(".value")
        raw_value, display_value = _node_text(value_node if isinstance(value_node, Tag) else None)
        label = clean_text(label_node.get_text(" ", strip=True) if label_node else "")
        if not label:
            continue
        values = decimal_values(display_value)
        unit_code, scale = unit_for_metric(label, display_value)
        fields.append(
            SourceField(position, label, raw_value, display_value, "metric", _first_link(item))
        )
        metrics.append(
            Metric(
                position=position,
                label_raw=label,
                metric_code=METRIC_CODES.get(label.lower()),
                display_value=display_value,
                value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                value_json=json.dumps([decimal_text(value) for value in values]) if len(values) > 1 else None,
                unit_code=unit_code,
                scale_multiplier=scale,
                missing_kind=missing_kind(display_value),
            )
        )
    if not metrics:
        raise ValueError("Top ratio panel has no rows")
    return fields, metrics


def parse_quick_ratios_fragment(
    content: bytes,
    position_offset: int = 0,
) -> tuple[list[SourceField], list[Metric]]:
    """Parse the AJAX quick-ratios HTML fragment into fields and metrics.

    The fragment contains ``<li data-source="quick-ratio">`` elements returned
    by ``/api/company/{warehouseId}/quick_ratios/``.  Positions are offset by
    *position_offset* so items can be appended after the default ratios.

    Returns empty lists when the fragment is empty (no custom ratios configured).
    """
    soup = BeautifulSoup(content, "html.parser")
    fields: list[SourceField] = []
    metrics: list[Metric] = []
    for index, item in enumerate(soup.find_all("li", recursive=False)):
        position = position_offset + index
        label_node = item.select_one(".name")
        value_node = item.select_one(".value")
        raw_value, display_value = _node_text(value_node if isinstance(value_node, Tag) else None)
        label = clean_text(label_node.get_text(" ", strip=True) if label_node else "")
        if not label:
            continue
        values = decimal_values(display_value)
        unit_code, scale = unit_for_metric(label, display_value)
        fields.append(
            SourceField(position, label, raw_value, display_value, "metric", _first_link(item))
        )
        metrics.append(
            Metric(
                position=position,
                label_raw=label,
                metric_code=METRIC_CODES.get(label.lower()),
                display_value=display_value,
                value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                value_json=json.dumps([decimal_text(value) for value in values]) if len(values) > 1 else None,
                unit_code=unit_code,
                scale_multiplier=scale,
                missing_kind=missing_kind(display_value),
            )
        )
    return fields, metrics

# Preserve the legacy facade identity for existing serialized callables.
_parse_ratios.__module__ = "scrapper.sources.screener.company"
parse_quick_ratios_fragment.__module__ = "scrapper.sources.screener.company"
