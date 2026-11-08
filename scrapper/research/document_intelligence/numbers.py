"""Decimal normalization of explicit values. Never impute a missing unit."""
from __future__ import annotations

from decimal import Decimal
import re

NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?"
VALUE = re.compile(
    rf"(?<![\w.])(?P<currency>INR|Rs\.?|₹|USD|US\$|\$|EUR|€)?\s*"
    rf"(?P<approx>~|approximately\s+|around\s+|about\s+)?"
    rf"(?P<low>{NUMBER})(?:\s*%?\s*(?:-|–|to)\s*(?P<high>{NUMBER}))?\s*"
    r"(?P<unit>crores?|crs?\.?|billions?|bn|millions?|mn|lakhs?|%|percent|bps|MTPA|MW|GW|MT|tonnes?|units|times|x)(?!\w)",
    re.I,
)
SCALES = {"crore": "10000000", "cr": "10000000", "billion": "1000000000",
          "bn": "1000000000", "million": "1000000", "mn": "1000000", "lakh": "100000"}
MONEY_RANGE = re.compile(rf'((?:INR|Rs\.?|₹|USD|\$)\s*{NUMBER}\s*(?:crores?|crs?\.?|billions?|bn|millions?|mn))\s+to\s+((?:INR|Rs\.?|₹|USD|\$)\s*{NUMBER}\s*(?:crores?|crs?\.?|billions?|bn|millions?|mn))', re.I)


def decimal_string(value):
    return format(Decimal(value.replace(",", "")), "f")


def normalize_value(raw: str, *, unit_hint: str | None = None):
    """Return explicit scalar/range, base-currency scale, and validation metadata."""
    if m := MONEY_RANGE.fullmatch(raw.strip()):
        lo, hi = normalize_value(m[1]), normalize_value(m[2])
        if lo and hi and all(lo[k] == hi[k] for k in ('currency', 'unit', 'scale')) and Decimal(lo['normalized_value']) <= Decimal(hi['normalized_value']):
            return {**lo, 'normalized_value': {'low': lo['normalized_value'], 'high': hi['normalized_value']}}
        return None
    match = VALUE.fullmatch(raw.strip())
    if match is None and unit_hint:
        hint = re.fullmatch(r"(INR|Rs\.?|₹|USD|US\$|\$|EUR)?\s*(?:in\s+)?(.*)", unit_hint, re.I)
        number = raw.strip()
        if re.fullmatch(rf"\({NUMBER}\)", number):
            number = "-" + number[1:-1]
        match = VALUE.fullmatch(f"{hint[1] or ''}{number} {hint[2]}")
    if match is None:
        return None
    currency_raw = (match["currency"] or "").lower()
    currency = "INR" if currency_raw in {"inr", "rs", "rs.", "₹"} else (
        "USD" if currency_raw in {"usd", "us$", "$"} else "EUR" if currency_raw in {"eur", "€"} else None)
    token = match["unit"].lower().rstrip(".")
    if token not in {"bps", "times", "units"}:
        token = token.rstrip("s")
    scale = SCALES.get(token, "1")
    unit = {"percent": "percent", "%": "percent", "x": "ratio", "times": "ratio",
            "bps": "basis_points", "mw": "MW", "gw": "GW", "mt": "MT", "mtpa": "MTPA",
            "tonne": "tonnes", "units": "units"}.get(token, "currency" if currency else "count")
    low = Decimal(match["low"].replace(",", "")) * Decimal(scale)
    values = format(low, "f")
    if match["high"]:
        high = Decimal(match["high"].replace(",", "")) * Decimal(scale)
        if high < low:
            return None
        values = {"low": format(low, "f"), "high": format(high, "f")}
    return dict(normalized_value=values, currency=currency, unit=unit, scale=scale,
                approximate=bool(match["approx"]))


def values_in(text: str):
    ranges = list(MONEY_RANGE.finditer(text))
    items = []
    for match in ranges:
        if normalized := normalize_value(match.group()):
            items.append((match, normalized))
    for match in VALUE.finditer(text):
        if any(r.start() <= match.start() < r.end() for r in ranges):
            continue
        normalized = normalize_value(match.group())
        if normalized:
            items.append((match, normalized))
    yield from sorted(items, key=lambda item: item[0].start())
