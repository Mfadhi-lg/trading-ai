"""Pure parser for the first Screener company-page vertical slice."""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString, Tag

from ...core.nums import (
    clean_text,
    decimal_text,
    decimal_values,
    missing_kind,
    unit_for_financial_line,
    unit_for_metric,
    unit_for_ratio_line,
)
from ...config import SCREENER_BASE
from .manifest import shareholding_unavailable
from .growth import (
    GROWTH_METRIC_CODES,
    GROWTH_PERIOD_CODES,
    _parse_growth,
)
from .shareholding import (
    SHAREHOLDING_HOLDER_CODES,
    SHAREHOLDING_REGIME_BOUNDARY,
    _shareholding_period_end,
    _shareholding_regime,
    is_cross_regime_comparison,
    _shareholder_code,
    _parse_shareholding_rows,
    _shareholding_table,
    _parse_shareholding,
)
from .statements import (
    LINE_ITEM_CODES,
    _fiscal_year,
    _canonical_line_label,
    _expandable_marker,
    _quarter_period,
    _annual_period,
    _parse_statement_table,
    _parse_quarters,
    _parse_profit_loss,
    _parse_balance_sheet,
    _parse_cash_flow,
    _parse_annual_ratios,
)
from .profile import _parse_company_profile
from .commentary import parse_commentary_fragment, _parse_pros_cons
from .parsing import _node_text, _first_link
from .identity import _quote_timestamp, _parse_identity_and_quote
from .metrics import METRIC_CODES, _parse_ratios, parse_quick_ratios_fragment
from .models import (
    SourceField,
    SourceCell,
    Metric,
    FinancialObservation,
    GrowthObservation,
    ShareholdingObservation,
    DocumentLink,
    CompanyProfileLink,
    CompanyTextObservation,
    Quote,
    ParsedCompany,
)







from .document_links import _parse_documents


def parse_company_page(content: bytes, fetched_at_utc: str) -> ParsedCompany:
    """Parse bytes without network or database access."""
    soup = BeautifulSoup(content, "html.parser")
    (
        company_name,
        symbol,
        bse_code,
        scope,
        source_company_id,
        source_warehouse_id,
        canonical_url,
        quote,
        identity_fields,
    ) = _parse_identity_and_quote(soup, fetched_at_utc)
    ratio_fields, metrics = _parse_ratios(soup)
    profile_heading, profile_fields, profile_links, profile_texts = _parse_company_profile(soup)
    analysis_heading, analysis_fields, analysis_texts = _parse_pros_cons(soup)
    quarter_heading, quarter_cells, financials = _parse_quarters(soup)
    pnl_heading, pnl_cells, pnl_financials = _parse_profit_loss(soup)
    balance_heading, balance_cells, balance_financials = _parse_balance_sheet(soup)
    cash_heading, cash_cells, cash_financials = _parse_cash_flow(soup)
    ratios_heading, ratios_cells, ratios_financials = _parse_annual_ratios(soup)
    growth_cells, growth_observations = _parse_growth(soup)
    (
        shareholding_heading,
        shareholding_warning,
        shareholding_q_cells,
        shareholding_q_observations,
        shareholding_y_cells,
        shareholding_y_observations,
    ) = _parse_shareholding(soup)
    documents_heading, document_fields, document_links = _parse_documents(soup)
    return ParsedCompany(
        company_name=company_name,
        nse_symbol=symbol,
        bse_code=bse_code,
        reporting_scope=scope,
        source_company_id=source_company_id,
        source_warehouse_id=source_warehouse_id,
        canonical_url=canonical_url,
        quote=quote,
        identity_fields=identity_fields,
        ratio_fields=ratio_fields,
        metrics=metrics,
        profile_fields=profile_fields,
        profile_links=profile_links,
        profile_texts=profile_texts,
        profile_heading=profile_heading,
        analysis_fields=analysis_fields,
        analysis_texts=analysis_texts,
        analysis_heading=analysis_heading,
        quarter_cells=quarter_cells,
        financials=financials,
        quarter_heading=quarter_heading,
        pnl_cells=pnl_cells,
        pnl_financials=pnl_financials,
        pnl_heading=pnl_heading,
        balance_cells=balance_cells,
        balance_financials=balance_financials,
        balance_heading=balance_heading,
        cash_cells=cash_cells,
        cash_financials=cash_financials,
        cash_heading=cash_heading,
        ratios_cells=ratios_cells,
        ratios_financials=ratios_financials,
        ratios_heading=ratios_heading,
        growth_cells=growth_cells,
        growth_observations=growth_observations,
        shareholding_q_cells=shareholding_q_cells,
        shareholding_q_observations=shareholding_q_observations,
        shareholding_y_cells=shareholding_y_cells,
        shareholding_y_observations=shareholding_y_observations,
        shareholding_heading=shareholding_heading,
        shareholding_classification_warning=shareholding_warning,
        shareholding_unavailable_frequencies=[f for f in ("quarterly", "yearly") if shareholding_unavailable(soup, f)],
        document_fields=document_fields,
        document_links=document_links,
        documents_heading=documents_heading,
    )

# Preserve the legacy facade identity for serialized callables.
parse_company_page.__module__ = "scrapper.sources.screener.company"
