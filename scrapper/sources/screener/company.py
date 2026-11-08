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







# Each concall row exposes up to four source links, keyed by their visible label.
# Phase 1 records only the link metadata; the AI Summary is Screener-generated
# commentary (publisher `screener`), never a primary-source document.

































































from .document_links import CONCALL_LINK_TYPES, _direct_text, _split_publisher, _parse_documents
from .assembly import parse_company_page
