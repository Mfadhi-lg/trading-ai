"""Exact, locale-aware numeric parsing for source display strings."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"[+\-−]?\d[\d,]*(?:\.\d+)?")
_MISSING = {"", "-", "—", "–", "na", "n/a", "nan"}


def clean_text(value: object | None) -> str:
    """Collapse source whitespace without changing punctuation or signs."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def missing_kind(text: str) -> str | None:
    normalized = clean_text(text).lower()
    if normalized == "":
        return "blank"
    if normalized in {"-", "—", "–"}:
        return "dash"
    if normalized in _MISSING:
        return "not_available"
    return None


def decimal_values(text: str) -> list[Decimal]:
    """Return all exact decimals in display order; never pass through float."""
    values: list[Decimal] = []
    for token in _NUMBER.findall(clean_text(text)):
        canonical = token.replace(",", "").replace("−", "-")
        try:
            values.append(Decimal(canonical))
        except InvalidOperation:
            continue
    return values


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def unit_for_metric(label: str, display_value: str) -> tuple[str | None, str | None]:
    label_lower = clean_text(label).lower()
    value = clean_text(display_value)
    if "%" in value or label_lower.endswith(" %") or label_lower in {"roce", "roe"}:
        return "PERCENT_POINT", "1"
    if "cr" in value.lower() or label_lower == "market cap":
        return "INR_CRORE", "10000000"
    if "₹" in value or label_lower in {"current price", "book value", "face value"}:
        return "INR", "1"
    return "RATIO", "1"


def unit_for_financial_line(
    label: str, sample_values: object | None = None
) -> tuple[str, str]:
    """Unit for a statement row (quarterly results, P&L, cash flow, …).

    The label decides the unit for most rows. A few source rows (e.g. cash flow's
    ``CFO/OP``) express a percentage only in their cell values, not in the label,
    so an optional iterable of that row's display values is inspected too.
    """
    normalized = clean_text(label).lower()
    percent_in_values = any(
        "%" in clean_text(value) for value in (sample_values or ())
    )
    if "%" in normalized or percent_in_values:
        return "PERCENT_POINT", "1"
    if "eps" in normalized:
        return "INR", "1"
    return "INR_CRORE", "10000000"


# Retained name from the quarterly-only slice.
unit_for_quarter_line = unit_for_financial_line


def unit_for_ratio_line(
    label: str, sample_values: object | None = None
) -> tuple[str, str]:
    """Unit for a Screener annual-ratios row.

    The ratios table mixes measurement kinds that a crore-defaulting statement
    resolver would mislabel: turnover-cycle rows in days, ROCE in percentage
    points, coverage/leverage rows as bare ratios, and plain counts. The kind is
    decided from the label first, then the row's own values as a fallback.
    """
    normalized = clean_text(label).lower()
    percent_in_values = any(
        "%" in clean_text(value) for value in (sample_values or ())
    )
    if "%" in normalized or percent_in_values:
        return "PERCENT_POINT", "1"
    if "days" in normalized or "cash conversion cycle" in normalized:
        return "DAYS", "1"
    if "ratio" in normalized or "times" in normalized or " to " in normalized:
        return "RATIO", "1"
    return "COUNT", "1"


# Backwards-compatible helper for any untouched draft modules. New persistence
# must use decimal_values/decimal_text instead.
def to_float(text: object | None) -> float | None:
    values = decimal_values(clean_text(text))
    return float(values[0]) if values else None
