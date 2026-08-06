"""Verified Trendlyne identities and monthly mutual-fund holding URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from ...config import TRENDLYNE_BASE


@dataclass(frozen=True)
class TrendlyneMapping:
    source_company_id: str
    source_symbol: str
    source_slug: str
    company_name: str
    bse_code: str | None = None

    @property
    def canonical_url(self) -> str:
        return monthly_mf_url(self, "latest")


RBA = TrendlyneMapping(
    source_company_id="357598",
    source_symbol="RBA",
    source_slug="restaurant-brands-asia-ltd",
    company_name="Restaurant Brands Asia Ltd.",
    bse_code="543248",
)

_VERIFIED_MAPPINGS = {RBA.source_symbol: RBA}
_PERIOD = re.compile(r"^(?:latest|[A-Z][a-z]{2}-\d{4})$")
_CANONICAL_PERIOD = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


def verified_mapping(symbol: str) -> TrendlyneMapping:
    normalized = symbol.strip().upper()
    try:
        return _VERIFIED_MAPPINGS[normalized]
    except KeyError as exc:
        raise ValueError(f"No verified Trendlyne mapping for {normalized!r}") from exc


def monthly_mf_url(mapping: TrendlyneMapping, period: str = "latest") -> str:
    if not _PERIOD.fullmatch(period):
        raise ValueError(f"Invalid Trendlyne source period {period!r}")
    return (
        f"{TRENDLYNE_BASE}/equity/monthly-mutual-fund-share-holding/"
        f"{mapping.source_company_id}/{mapping.source_symbol}/{period}/"
        f"{mapping.source_slug}/"
    )


def source_period_for_url(requested_source_period: str) -> str:
    if requested_source_period == "latest":
        return requested_source_period
    if not _CANONICAL_PERIOD.fullmatch(requested_source_period):
        raise ValueError(
            "Trendlyne requested source period must be 'latest' or YYYY-MM"
        )
    return datetime.strptime(requested_source_period, "%Y-%m").strftime("%b-%Y")


def monthly_mf_url_parts(url: str) -> tuple[str, str, str, str]:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) != 6 or parts[:2] != [
        "equity",
        "monthly-mutual-fund-share-holding",
    ]:
        raise ValueError("Not a canonical Trendlyne monthly MF holdings URL")
    return parts[2], parts[3], parts[4], parts[5]
