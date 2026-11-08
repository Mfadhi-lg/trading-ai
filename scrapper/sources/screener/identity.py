"""Source-specific Screener identity parsing helpers."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup, Tag
from ...config import SCREENER_BASE
from ...core.nums import clean_text, decimal_text, decimal_values
from .models import Quote, SourceField
from .parsing import _node_text


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

# Preserve the legacy facade identity for existing serialized callables.
_quote_timestamp.__module__ = "scrapper.sources.screener.company"
_parse_identity_and_quote.__module__ = "scrapper.sources.screener.company"
