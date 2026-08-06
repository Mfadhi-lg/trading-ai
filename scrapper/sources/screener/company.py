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
}

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

# The count row has no `showShareholders(...)` button, so it is mapped by label.
# The four holder-category rows take their canonical code from the button's
# onclick classification token (e.g. `foreign_institutions`), which is the
# source's own classification and more faithful than a label heuristic.
SHAREHOLDING_HOLDER_CODES = {
    "no. of shareholders": "no_of_shareholders",
}

# The XBRL shareholding regime changed from Sep 2022 onwards. Each period is
# tagged from its own period end; the boundary is inclusive of Sep 2022.
SHAREHOLDING_REGIME_BOUNDARY = (2022, 9)

# Each concall row exposes up to four source links, keyed by their visible label.
# Phase 1 records only the link metadata; the AI Summary is Screener-generated
# commentary (publisher `screener`), never a primary-source document.
CONCALL_LINK_TYPES = {
    "transcript": "concall_transcript",
    "ai summary": "concall_notes",
    "ppt": "concall_ppt",
    "rec": "concall_recording",
}


@dataclass(frozen=True)
class SourceField:
    position: int
    key_raw: str
    raw_text: str
    display_text: str
    role: str
    link_url: str | None = None
    visibility_state: str = "visible"


@dataclass(frozen=True)
class SourceCell:
    row: int
    column: int
    row_label: str | None
    column_label: str | None
    raw_text: str
    display_text: str
    role: str
    link_url: str | None
    rowspan: int
    colspan: int
    table: int = 0


@dataclass(frozen=True)
class Metric:
    position: int
    label_raw: str
    metric_code: str | None
    display_value: str
    value_decimal: str | None
    value_json: str | None
    unit_code: str | None
    scale_multiplier: str | None
    missing_kind: str | None


@dataclass(frozen=True)
class FinancialObservation:
    row: int
    column: int
    section_code: str
    line_item_raw: str
    line_item_code: str | None
    period_label_raw: str
    period_end: str | None
    period_type: str
    fiscal_year: int | None
    display_value: str
    value_decimal: str | None
    unit_code: str
    scale_multiplier: str
    missing_kind: str | None
    is_expandable: bool = False
    schedule_key: str | None = None


@dataclass(frozen=True)
class GrowthObservation:
    block: int
    row: int
    metric_code: str | None
    metric_label_raw: str
    period_code: str | None
    period_label_raw: str
    display_value: str
    value_decimal: str | None
    unit_code: str
    missing_kind: str | None


@dataclass(frozen=True)
class ShareholdingObservation:
    row: int
    column: int
    frequency: str
    holder_code: str | None
    holder_label_raw: str
    period_label_raw: str
    period_end: str | None
    display_value: str
    value_decimal: str | None
    unit_code: str
    classification_regime: str
    missing_kind: str | None


@dataclass(frozen=True)
class DocumentLink:
    position: int
    document_type: str
    title_raw: str
    period_label_raw: str | None
    publisher: str | None
    source_url: str | None
    availability_status: str


@dataclass(frozen=True)
class CompanyProfileLink:
    position: int
    link_type: str
    label_raw: str
    identifier_value: str | None
    source_url: str


@dataclass(frozen=True)
class CompanyTextObservation:
    position: int
    text_type: str
    heading_raw: str
    display_text: str
    content_origin: str
    is_source_generated: bool


@dataclass(frozen=True)
class Quote:
    display_price: str
    price_decimal: str | None
    currency: str | None
    timestamp_raw: str
    timestamp_utc: str | None
    timezone: str
    timestamp_status: str


@dataclass
class ParsedCompany:
    company_name: str
    nse_symbol: str
    bse_code: str
    reporting_scope: str
    source_company_id: str | None
    source_warehouse_id: str | None
    canonical_url: str | None
    quote: Quote
    identity_fields: list[SourceField] = field(default_factory=list)
    ratio_fields: list[SourceField] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    profile_fields: list[SourceField] = field(default_factory=list)
    profile_links: list[CompanyProfileLink] = field(default_factory=list)
    profile_texts: list[CompanyTextObservation] = field(default_factory=list)
    profile_heading: str = "About"
    analysis_fields: list[SourceField] = field(default_factory=list)
    analysis_texts: list[CompanyTextObservation] = field(default_factory=list)
    analysis_heading: str = "Pros and Cons"
    quarter_cells: list[SourceCell] = field(default_factory=list)
    financials: list[FinancialObservation] = field(default_factory=list)
    quarter_heading: str = "Quarterly Results"
    pnl_cells: list[SourceCell] = field(default_factory=list)
    pnl_financials: list[FinancialObservation] = field(default_factory=list)
    pnl_heading: str = "Profit & Loss"
    balance_cells: list[SourceCell] = field(default_factory=list)
    balance_financials: list[FinancialObservation] = field(default_factory=list)
    balance_heading: str = "Balance Sheet"
    cash_cells: list[SourceCell] = field(default_factory=list)
    cash_financials: list[FinancialObservation] = field(default_factory=list)
    cash_heading: str = "Cash Flows"
    growth_cells: list[SourceCell] = field(default_factory=list)
    growth_observations: list[GrowthObservation] = field(default_factory=list)
    growth_heading: str = "Compounded Growth"
    ratios_cells: list[SourceCell] = field(default_factory=list)
    ratios_financials: list[FinancialObservation] = field(default_factory=list)
    ratios_heading: str = "Ratios"
    shareholding_q_cells: list[SourceCell] = field(default_factory=list)
    shareholding_q_observations: list[ShareholdingObservation] = field(default_factory=list)
    shareholding_y_cells: list[SourceCell] = field(default_factory=list)
    shareholding_y_observations: list[ShareholdingObservation] = field(default_factory=list)
    shareholding_heading: str = "Shareholding Pattern"
    shareholding_classification_warning: str | None = None
    document_fields: list[SourceField] = field(default_factory=list)
    document_links: list[DocumentLink] = field(default_factory=list)
    documents_heading: str = "Documents"


def _node_text(node: Tag | None) -> tuple[str, str]:
    if node is None:
        return "", ""
    return node.get_text("", strip=False), clean_text(node.get_text(" ", strip=True))


def _first_link(node: Tag) -> str | None:
    link = node.find("a", href=True)
    return str(link["href"]) if isinstance(link, Tag) else None


def _quote_timestamp(raw: str, fetched_at_utc: str) -> tuple[str | None, str]:
    match = re.fullmatch(
        r"(\d{1,2}\s+[A-Za-z]{3})\s+(\d{1,2}:\d{2})\s*([ap])\.m\.",
        clean_text(raw),
        flags=re.IGNORECASE,
    )
    if not match:
        return None, "unparsed"
    fetched = datetime.fromisoformat(fetched_at_utc.replace("Z", "+00:00"))
    local_fetch = fetched.astimezone(ZoneInfo("Asia/Kolkata"))
    stamp = datetime.strptime(
        f"{match.group(1)} {local_fetch.year} {match.group(2)} {match.group(3).upper()}M",
        "%d %b %Y %I:%M %p",
    ).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    # Around New Year a Dec quote can be displayed on a page fetched in Jan.
    if (stamp - local_fetch).days > 180:
        stamp = stamp.replace(year=stamp.year - 1)
    return stamp.astimezone(ZoneInfo("UTC")).isoformat(), "derived_year_from_fetch"


def _parse_identity_and_quote(
    soup: BeautifulSoup, fetched_at_utc: str
) -> tuple[str, str, str, str, str | None, str | None, str | None, Quote, list[SourceField]]:
    company_info = soup.select_one("#company-info")
    top = soup.select_one("#top")
    if not isinstance(company_info, Tag) or not isinstance(top, Tag):
        raise ValueError("Screener company identity container is missing")

    heading = top.find("h1")
    company_name = clean_text(heading.get_text(" ", strip=True) if heading else "")
    if not company_name:
        raise ValueError("Company name is missing")

    nse_symbol = ""
    bse_code = ""
    for link in top.find_all("a", href=True):
        text = clean_text(link.get_text(" ", strip=True))
        if text.upper().startswith("NSE:"):
            nse_symbol = text.split(":", 1)[1].strip().upper()
        elif text.upper().startswith("BSE:"):
            bse_code = text.split(":", 1)[1].strip()
    scope = "consolidated" if company_info.get("data-consolidated") == "true" else "standalone"
    source_company_id = company_info.get("data-company-id")
    if bse_code and not bse_code.isdigit():
        raise ValueError("BSE identifier is invalid")
    if not nse_symbol and not bse_code and not source_company_id:
        raise ValueError("No NSE, BSE or Screener company identity is available")
    source_warehouse_id = company_info.get("data-warehouse-id")
    canonical_node = soup.select_one('link[rel="canonical"]')
    canonical_href = (
        str(canonical_node.get("href", "")).strip()
        if isinstance(canonical_node, Tag)
        else ""
    )
    canonical_url = urljoin(SCREENER_BASE, canonical_href) if canonical_href else None
    quote_box = top.select_one(".font-size-18.strong")
    if not isinstance(quote_box, Tag):
        raise ValueError("Quote header is missing")
    quote_price_node = quote_box.select_one(".flex span")
    time_node = quote_box.select_one(".ink-600.font-size-11")
    _, display_price = _node_text(quote_price_node if isinstance(quote_price_node, Tag) else None)
    _, timestamp_raw = _node_text(time_node if isinstance(time_node, Tag) else None)
    price_values = decimal_values(display_price)
    timestamp_utc, timestamp_status = _quote_timestamp(timestamp_raw, fetched_at_utc)
    quote = Quote(
        display_price=display_price,
        price_decimal=decimal_text(price_values[0]) if price_values else None,
        currency="INR" if "₹" in display_price else None,
        timestamp_raw=timestamp_raw,
        timestamp_utc=timestamp_utc,
        timezone="Asia/Kolkata",
        timestamp_status=timestamp_status,
    )
    fields = [
        SourceField(0, "Company Name", company_name, company_name, "identity"),
        SourceField(1, "NSE", nse_symbol, nse_symbol, "identifier"),
        SourceField(2, "BSE", bse_code, bse_code, "identifier"),
        SourceField(3, "Quote Price", display_price, display_price, "quote"),
        SourceField(4, "Quote Timestamp", timestamp_raw, timestamp_raw, "quote_timestamp"),
        SourceField(
            5,
            "Warehouse ID",
            str(source_warehouse_id or ""),
            str(source_warehouse_id or ""),
            "supplemental_endpoint_identifier",
            visibility_state="metadata",
        ),
        SourceField(
            6,
            "Canonical URL",
            canonical_href,
            canonical_url or "",
            "canonical_url",
            link_url=canonical_url,
            visibility_state="metadata",
        ),
    ]
    return (
        company_name,
        nse_symbol,
        bse_code,
        scope,
        str(source_company_id) if source_company_id else None,
        str(source_warehouse_id) if source_warehouse_id else None,
        canonical_url,
        quote,
        fields,
    )


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


def _parse_company_profile(
    soup: BeautifulSoup,
) -> tuple[
    str,
    list[SourceField],
    list[CompanyProfileLink],
    list[CompanyTextObservation],
]:
    """Parse page description, company links and visible About prose.

    The desktop and mobile link groups contain the same targets. Only the
    desktop group is canonical so responsive markup does not duplicate rows.
    About citation links remain separate ordered links while their displayed
    markers also remain in the source-faithful prose.
    """
    top = soup.select_one("#top")
    profile = soup.select_one("#top .company-profile")
    if not isinstance(top, Tag) or not isinstance(profile, Tag):
        raise ValueError("Screener company profile is missing")

    title = profile.find("div", class_="title")
    heading = clean_text(title.get_text(" ", strip=True) if title else "About")
    about = profile.select_one(".about")
    if not isinstance(about, Tag):
        raise ValueError("Screener About text is missing")

    fields: list[SourceField] = []
    links: list[CompanyProfileLink] = []
    texts: list[CompanyTextObservation] = []

    description = soup.select_one('meta[name="description"]')
    description_text = (
        clean_text(str(description.get("content", "")))
        if isinstance(description, Tag)
        else ""
    )
    if not description_text:
        raise ValueError("Screener page description is missing")
    fields.append(
        SourceField(
            position=len(fields),
            key_raw="Description",
            raw_text=description_text,
            display_text=description_text,
            role="company_description",
            visibility_state="metadata",
        )
    )
    texts.append(
        CompanyTextObservation(
            position=0,
            text_type="company_description",
            heading_raw="Description",
            display_text=description_text,
            content_origin="source_metadata",
            is_source_generated=False,
        )
    )

    desktop_links = top.select_one(".company-links.show-from-tablet-landscape")
    if not isinstance(desktop_links, Tag):
        raise ValueError("Screener company links are missing")
    for anchor in desktop_links.find_all("a", href=True, recursive=False):
        raw_text, label = _node_text(anchor)
        upper = label.upper()
        if upper.startswith("BSE:"):
            link_type = "bse"
            identifier = label.split(":", 1)[1].strip()
        elif upper.startswith("NSE:"):
            link_type = "nse"
            identifier = label.split(":", 1)[1].strip().upper()
        else:
            link_type = "website"
            identifier = None
        url = str(anchor["href"])
        fields.append(
            SourceField(
                position=len(fields),
                key_raw=link_type.upper(),
                raw_text=raw_text,
                display_text=label,
                role=f"company_link_{link_type}",
                link_url=url,
            )
        )
        links.append(
            CompanyProfileLink(
                position=len(links),
                link_type=link_type,
                label_raw=label,
                identifier_value=identifier,
                source_url=url,
            )
        )

    about_raw, about_text = _node_text(about)
    fields.append(
        SourceField(
            position=len(fields),
            key_raw=heading,
            raw_text=about_raw,
            display_text=about_text,
            role="about_text",
        )
    )
    texts.append(
        CompanyTextObservation(
            position=0,
            text_type="about",
            heading_raw=heading,
            display_text=about_text,
            content_origin="source_visible_text",
            is_source_generated=False,
        )
    )
    for anchor in about.find_all("a", href=True):
        raw_text, label = _node_text(anchor)
        url = str(anchor["href"])
        fields.append(
            SourceField(
                position=len(fields),
                key_raw="About Source",
                raw_text=raw_text,
                display_text=label,
                role="about_source_link",
                link_url=url,
            )
        )
        links.append(
            CompanyProfileLink(
                position=len(links),
                link_type="about_source",
                label_raw=label,
                identifier_value=None,
                source_url=url,
            )
        )

    link_types = {link.link_type for link in links}
    required_links = {"website", "about_source"}
    if not required_links <= link_types or not ({"bse", "nse"} & link_types):
        raise ValueError("Screener company profile links are incomplete")
    return heading, fields, links, texts


def _parse_pros_cons(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceField], list[CompanyTextObservation]]:
    """Parse Pros/Cons as prose and retain Screener's generation disclosure."""
    section = soup.select_one("section#analysis")
    if not isinstance(section, Tag):
        raise ValueError("Screener Pros and Cons section is missing")
    pros = section.select_one(".pros")
    cons = section.select_one(".cons")
    if not isinstance(pros, Tag) or not isinstance(cons, Tag):
        raise ValueError("Screener Pros or Cons list is missing")

    disclosure = section.select_one("p.sub .font-weight-500")
    disclosure_raw, disclosure_text = _node_text(
        disclosure if isinstance(disclosure, Tag) else None
    )
    source_generated = "machine generated" in disclosure_text.lower()

    fields: list[SourceField] = []
    texts: list[CompanyTextObservation] = []
    for container, text_type in ((pros, "pro"), (cons, "con")):
        heading_node = container.select_one(".title")
        heading = clean_text(
            heading_node.get_text(" ", strip=True) if heading_node else text_type.title()
        )
        if isinstance(heading_node, Tag):
            heading_raw, heading_display = _node_text(heading_node)
            fields.append(
                SourceField(
                    position=len(fields),
                    key_raw=heading,
                    raw_text=heading_raw,
                    display_text=heading_display,
                    role="commentary_heading",
                )
            )
        for position, item in enumerate(container.select("ul > li")):
            raw_text, display_text = _node_text(item)
            fields.append(
                SourceField(
                    position=len(fields),
                    key_raw=heading,
                    raw_text=raw_text,
                    display_text=display_text,
                    role=(
                        f"{text_type}_source_generated_commentary"
                        if source_generated
                        else f"{text_type}_commentary"
                    ),
                )
            )
            texts.append(
                CompanyTextObservation(
                    position=position,
                    text_type=text_type,
                    heading_raw=heading,
                    display_text=display_text,
                    content_origin=(
                        "source_generated_commentary"
                        if source_generated
                        else "source_commentary_unverified"
                    ),
                    is_source_generated=source_generated,
                )
            )

    # A source list may legitimately be empty for one side. Preserve both
    # headings and every displayed item, but require at least one commentary
    # item overall so an empty shell cannot pass as captured analysis.
    if not any(text.text_type in {"pro", "con"} for text in texts):
        raise ValueError("Screener Pros and Cons contain no commentary")

    fields.append(
        SourceField(
            position=len(fields),
            key_raw="Disclosure",
            raw_text=disclosure_raw,
            display_text=disclosure_text,
            role="commentary_generation_disclosure",
        )
    )
    texts.append(
        CompanyTextObservation(
            position=0,
            text_type="commentary_disclosure",
            heading_raw="Disclosure",
            display_text=disclosure_text,
            content_origin="source_visible_text",
            is_source_generated=False,
        )
    )

    tooltip = section.select_one(".tooltip")
    if isinstance(tooltip, Tag):
        tooltip_raw, tooltip_text = _node_text(tooltip)
        fields.append(
            SourceField(
                position=len(fields),
                key_raw="Methodology",
                raw_text=tooltip_raw,
                display_text=tooltip_text,
                role="commentary_methodology",
                visibility_state="conditional",
            )
        )
        texts.append(
            CompanyTextObservation(
                position=0,
                text_type="commentary_methodology",
                heading_raw="Methodology",
                display_text=tooltip_text,
                content_origin="source_conditional_text",
                is_source_generated=False,
            )
        )
    return "Pros and Cons", fields, texts


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


def _shareholding_period_end(label: str) -> str | None:
    """Resolve a `MMM YYYY` shareholding period label to its month-end date."""
    match = re.fullmatch(r"([A-Za-z]{3})\s+(\d{4})", clean_text(label))
    if not match:
        return None
    try:
        month = datetime.strptime(match.group(1), "%b").month
    except ValueError:
        return None
    year = int(match.group(2))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day).isoformat()


def _shareholding_regime(period_end: str | None) -> str:
    """Tag one period by the XBRL classification regime it falls in.

    This is a per-period property derived from that period's own end date, never
    a cross-period comparison. The `cross_regime` distinction belongs to a
    derived comparison of two observations (S10) and is never a row's tag.
    """
    if not period_end:
        return "unknown"
    parsed = datetime.strptime(period_end, "%Y-%m-%d").date()
    if (parsed.year, parsed.month) >= SHAREHOLDING_REGIME_BOUNDARY:
        return "xbrl_sep_2022_plus"
    return "pre_sep_2022"


def is_cross_regime_comparison(
    first: ShareholdingObservation, second: ShareholdingObservation
) -> bool:
    """Whether comparing two shareholding observations crosses the Sep-2022 XBRL
    regime boundary.

    This is a DERIVED property of a *pair* of observations and is never stored as
    a tag on a single row (see PLAN.md 4.1). It is True only when both regimes are
    known and differ; an `unknown` regime yields False because the comparison
    cannot be established either way.
    """
    known = {"pre_sep_2022", "xbrl_sep_2022_plus"}
    if (
        first.classification_regime not in known
        or second.classification_regime not in known
    ):
        return False
    return first.classification_regime != second.classification_regime


def _shareholder_code(row_header: Tag, canonical_label: str) -> str | None:
    button = row_header.find("button")
    if isinstance(button, Tag):
        match = re.search(
            r"showShareholders\('([^']*)'", str(button.get("onclick", ""))
        )
        if match:
            return match.group(1)
    return SHAREHOLDING_HOLDER_CODES.get(canonical_label)


def _parse_shareholding_rows(
    table: Tag, frequency: str
) -> tuple[list[SourceCell], list[ShareholdingObservation]]:
    """Parse one shareholding table (quarterly or yearly) into faithful cells
    plus normalized observations.

    The four holder-category rows are percentage points; the `No. of
    Shareholders` row is a COUNT. Each period is regime-tagged from its own end
    date. ``frequency`` is part of every observation's identity, so the quarterly
    and yearly tables can never overwrite one another.
    """
    header_nodes = table.select("thead tr th")
    if len(header_nodes) < 2:
        raise ValueError(f"{frequency.capitalize()} shareholding headers are missing")
    period_labels = [clean_text(node.get_text(" ", strip=True)) for node in header_nodes[1:]]

    cells: list[SourceCell] = []
    for column, node in enumerate(header_nodes):
        raw, display = _node_text(node)
        cells.append(
            SourceCell(0, column, None, display or None, raw, display, "column_header", _first_link(node), int(node.get("rowspan", 1)), int(node.get("colspan", 1)))
        )

    observations: list[ShareholdingObservation] = []
    rows = table.select("tbody > tr")
    for row_position, row in enumerate(rows, start=1):
        row_nodes = row.find_all(["td", "th"], recursive=False)
        if not row_nodes:
            continue
        holder_label = clean_text(row_nodes[0].get_text(" ", strip=True))
        canonical_label = _canonical_line_label(holder_label)
        holder_code = _shareholder_code(row_nodes[0], canonical_label)
        is_category = row_nodes[0].find("button") is not None
        unit_code = "PERCENT_POINT" if is_category else "COUNT"
        for column, node in enumerate(row_nodes):
            raw, display = _node_text(node)
            column_label = (
                "" if column == 0 else period_labels[column - 1] if column - 1 < len(period_labels) else None
            )
            cell_role = (
                ("row_header_expandable" if is_category else "row_header")
                if column == 0
                else "data"
            )
            cells.append(
                SourceCell(
                    row_position, column, holder_label, column_label, raw, display,
                    cell_role, _first_link(node),
                    int(node.get("rowspan", 1)), int(node.get("colspan", 1)),
                )
            )
            if column == 0 or column - 1 >= len(period_labels):
                continue
            period_label = period_labels[column - 1]
            period_end = _shareholding_period_end(period_label)
            values = decimal_values(display)
            observations.append(
                ShareholdingObservation(
                    row=row_position,
                    column=column,
                    frequency=frequency,
                    holder_code=holder_code,
                    holder_label_raw=holder_label,
                    period_label_raw=period_label,
                    period_end=period_end,
                    display_value=display,
                    value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                    unit_code=unit_code,
                    classification_regime=_shareholding_regime(period_end),
                    missing_kind=missing_kind(display),
                )
            )
    if not observations:
        raise ValueError(f"{frequency.capitalize()} shareholding table has no observations")
    return cells, observations


def _shareholding_table(section: Tag, container_id: str, frequency: str) -> Tag:
    container = section.select_one(f"#{container_id}")
    if not isinstance(container, Tag):
        raise ValueError(f"{frequency.capitalize()} shareholding container is missing")
    table = container.find("table")
    if not isinstance(table, Tag):
        raise ValueError(f"{frequency.capitalize()} shareholding table is missing")
    return table


def _parse_shareholding(
    soup: BeautifulSoup,
) -> tuple[
    str,
    str | None,
    list[SourceCell],
    list[ShareholdingObservation],
    list[SourceCell],
    list[ShareholdingObservation],
]:
    """Parse both the quarterly (S09) and yearly (S10) shareholding tables.

    The two tables are parsed independently and each observation carries a
    distinct ``frequency``, so quarterly and yearly rows coexist without
    overwriting one another. The Sep-2022 XBRL regime tag is a per-period
    property derived from each period's own end date; ``cross_regime`` is never a
    row tag — it is a derived comparison of two observations (see
    :func:`is_cross_regime_comparison`).
    """
    section = soup.select_one("section#shareholding")
    if not isinstance(section, Tag):
        raise ValueError("Shareholding section is missing")
    heading_node = section.find("h2")
    heading = clean_text(
        heading_node.get_text(" ", strip=True) if heading_node else "Shareholding Pattern"
    )
    warning_node = section.select_one("p.sub.small")
    warning = (
        clean_text(warning_node.get_text(" ", strip=True)) if warning_node else None
    )

    q_cells, q_obs = _parse_shareholding_rows(
        _shareholding_table(section, "quarterly-shp", "quarterly"), "quarterly"
    )
    y_cells, y_obs = _parse_shareholding_rows(
        _shareholding_table(section, "yearly-shp", "yearly"), "yearly"
    )
    return heading, warning, q_cells, q_obs, y_cells, y_obs


def _direct_text(node: Tag) -> str:
    """Text of a node's own string children only, ignoring nested elements.

    A Screener document anchor holds its title as direct text and its date or
    publisher as a nested ``.ink-600`` element; this keeps the two separate
    without a string-subtraction heuristic.
    """
    return clean_text(
        "".join(str(child) for child in node.children if isinstance(child, NavigableString))
    )


def _split_publisher(meta_text: str) -> tuple[str | None, str | None]:
    """Split a `<date> from <publisher>` meta line into (period, publisher).

    Screener renders annual-report and credit-rating meta as ``from bse`` or
    ``3 Jul from care``. The publisher is the source's own claim, never treated
    as the actual document publisher. When no ``from`` clause is present the
    whole text is the period label and no publisher is claimed.
    """
    match = re.search(r"\bfrom\s+(.+)$", meta_text, flags=re.IGNORECASE)
    if not match:
        cleaned = clean_text(meta_text)
        return (cleaned or None), None
    period = clean_text(meta_text[: match.start()])
    return (period or None), clean_text(match.group(1)) or None


def _parse_documents(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceField], list[DocumentLink]]:
    """Parse the Documents section link lists (announcements, annual reports,
    credit ratings and concalls) as metadata only.

    Phase 1 records source titles, periods, publisher claims, positions,
    availability and URLs in source order. No PDF, PPT or recording is
    downloaded. A concall link with no ``href`` (rendered as a bare label) is a
    source-declared unavailable slot, kept distinct from an available link.
    """
    section = soup.select_one("section#documents")
    if not isinstance(section, Tag):
        raise ValueError("Documents section is missing")
    heading_node = section.find("h2")
    heading = clean_text(
        heading_node.get_text(" ", strip=True) if heading_node else "Documents"
    )

    fields: list[SourceField] = []
    links: list[DocumentLink] = []

    def add(
        document_type: str,
        title: str,
        period: str | None,
        publisher: str | None,
        url: str | None,
        source_node: Tag,
    ) -> None:
        position = len(links)
        raw_text, display_text = _node_text(source_node)
        fields.append(
            SourceField(
                position=position,
                key_raw=title,
                raw_text=raw_text,
                display_text=display_text,
                role=document_type,
                link_url=url,
            )
        )
        links.append(
            DocumentLink(
                position=position,
                document_type=document_type,
                title_raw=title,
                period_label_raw=period,
                publisher=publisher,
                source_url=url,
                availability_status="available" if url else "unavailable",
            )
        )

    announcements = section.select_one("#company-announcements-tab")
    if isinstance(announcements, Tag):
        for li in announcements.select("ul.list-links > li"):
            anchor = li.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            meta = li.find(class_="ink-600")
            period = clean_text(meta.get_text(" ", strip=True)) if isinstance(meta, Tag) else None
            add(
                "announcement",
                _direct_text(anchor),
                period,
                None,
                str(anchor["href"]),
                anchor,
            )

    annual_reports = section.select_one("div.annual-reports")
    if isinstance(annual_reports, Tag):
        for li in annual_reports.select("ul.list-links > li"):
            anchor = li.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            title = _direct_text(anchor)
            meta = li.find(class_="ink-600")
            _, publisher = (
                _split_publisher(clean_text(meta.get_text(" ", strip=True)))
                if isinstance(meta, Tag)
                else (None, None)
            )
            add(
                "annual_report",
                title,
                title,
                publisher,
                str(anchor["href"]),
                anchor,
            )
        # The DRHP button is a distinct prospectus link below the report list.
        for button in annual_reports.select("a.button[href]"):
            add(
                "drhp",
                _direct_text(button),
                None,
                None,
                str(button["href"]),
                button,
            )

    credit_ratings = section.select_one("div.credit-ratings")
    if isinstance(credit_ratings, Tag):
        for li in credit_ratings.select("ul.list-links > li"):
            anchor = li.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            meta = li.find(class_="ink-600")
            period, publisher = (
                _split_publisher(clean_text(meta.get_text(" ", strip=True)))
                if isinstance(meta, Tag)
                else (None, None)
            )
            add(
                "credit_rating",
                _direct_text(anchor),
                period,
                publisher,
                str(anchor["href"]),
                anchor,
            )

    concalls = section.select_one("div.concalls")
    if isinstance(concalls, Tag):
        for li in concalls.select("ul.list-links > li"):
            period_node = li.find("div", class_="nowrap")
            period = (
                clean_text(period_node.get_text(" ", strip=True))
                if isinstance(period_node, Tag)
                else None
            )
            for element in li.find_all(class_="concall-link"):
                label = _direct_text(element)
                document_type = CONCALL_LINK_TYPES.get(label.lower(), "concall_other")
                # An anchor carries an href; the AI Summary button carries a
                # Screener modal endpoint; a bare label element has neither.
                url = element.get("href") or element.get("data-url")
                url = str(url) if url else None
                publisher = "screener" if document_type == "concall_notes" and url else None
                add(document_type, label, period, publisher, url, element)

    if not links:
        raise ValueError("Documents section has no links")
    return heading, fields, links


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
        document_fields=document_fields,
        document_links=document_links,
        documents_heading=documents_heading,
    )
