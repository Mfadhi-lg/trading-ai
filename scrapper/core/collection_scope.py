"""Market-cap decisions shared by workers, monitoring and reports."""
from decimal import Decimal, InvalidOperation
import re


def numeric_cap(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def parsed_cap(parsed):
    for metric in getattr(parsed, "metrics", ()):
        if metric.metric_code == "market_cap":
            return numeric_cap(metric.value_decimal)
    return None


def is_scope_exclusion(message):
    text = str(message).lower()
    return any(marker in text for marker in (
        "below screener market-cap scope floor",
        "not above screener market-cap scope floor",
    ))


class MarketCapBelowFloor(RuntimeError):
    def __init__(self, value, floor):
        self.market_cap_crore = str(value)
        self.floor_crore = str(floor)
        super().__init__(f"Company is below Screener market-cap scope floor: {floor:g} INR crore")


def scope_snapshot(content):
    """Read identity and cap even when financial tables have not been published."""
    from bs4 import BeautifulSoup
    from ..sources.screener.company import _parse_ratios
    soup = BeautifulSoup(content, "html.parser")
    info = soup.select_one("#company-info")
    top = soup.select_one("#top")
    if info is None or top is None:
        raise ValueError("Screener identity missing during market-cap check")
    if info.get("data-consolidated") == "true":
        raise ValueError("Market-cap check requires a standalone page")
    identifiers = set()
    for link in top.select("a"):
        text = link.get_text(" ", strip=True)
        match = re.fullmatch(r"(?:NSE|BSE)(?:\s*-\s*SME)?\s*:\s*(\S+)", text, re.I)
        if match:
            identifiers.add(match[1].upper())
    _, metrics = _parse_ratios(soup)
    cap = next((numeric_cap(m.value_decimal) for m in metrics if m.metric_code=="market_cap"),None)
    return {"market_cap_crore":cap,"identifiers":identifiers,
            "source_company_id":info.get("data-company-id")}
