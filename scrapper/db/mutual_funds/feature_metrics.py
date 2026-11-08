"""Exact Decimal aggregation rules shared by M06 feature persistence."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation


def decimal_value(value: str | None, field: str) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not an exact decimal: {value}") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite: {value}")
    return result


def decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def is_active_equity(classification: dict) -> bool:
    return (
        classification["scheme_management_type"] == "active"
        and classification["is_equity_scheme"]
    )


def aggregate_feature_rows(
    rows: list[sqlite3.Row], classifications: dict[int, dict]
) -> dict:
    counts = {code: 0 for code in (
        "new_entry", "complete_exit", "quantity_increase",
        "quantity_decrease", "unchanged",
    )}
    bought = Decimal(0)
    sold = Decimal(0)
    net = Decimal(0)
    total_weight = Decimal(0)
    weight_change = Decimal(0)
    holder_count = 0
    active_holders = 0
    passive_holders = 0
    arbitrage_holders = 0
    active_accumulation = 0
    for row in rows:
        classification = classifications[int(row["scheme_id"])]
        change_class = str(row["change_class"])
        counts[change_class] += 1
        delta = decimal_value(row["quantity_change_decimal"], "quantity_change")
        if delta > 0:
            bought += delta
        elif delta < 0:
            sold -= delta
        net += delta
        current_quantity = decimal_value(
            row["current_quantity_decimal"], "current_quantity"
        )
        total_weight += decimal_value(row["current_weight_decimal"], "current_weight")
        weight_change += decimal_value(
            row["portfolio_weight_change_decimal"], "portfolio_weight_change"
        )
        active_equity = is_active_equity(classification)
        if current_quantity > 0:
            holder_count += 1
            active_holders += int(active_equity)
            passive_holders += int(
                classification["scheme_management_type"] in {"index", "ETF"}
            )
            arbitrage_holders += int(
                classification["scheme_management_type"] == "arbitrage"
            )
        if active_equity and change_class in {"new_entry", "quantity_increase"}:
            active_accumulation += 1
    accumulation = counts["new_entry"] + counts["quantity_increase"]
    distribution = counts["complete_exit"] + counts["quantity_decrease"]
    return {
        "active_fund_holder_count": holder_count,
        "new_fund_entries": counts["new_entry"],
        "complete_fund_exits": counts["complete_exit"],
        "funds_increasing_quantity": counts["quantity_increase"],
        "funds_decreasing_quantity": counts["quantity_decrease"],
        "funds_unchanged": counts["unchanged"],
        "gross_quantity_bought": decimal_text(bought),
        "gross_quantity_sold": decimal_text(sold),
        "net_quantity_change": decimal_text(net),
        "total_portfolio_weight": decimal_text(total_weight),
        "portfolio_weight_change": decimal_text(weight_change),
        "active_equity_holder_count": active_holders,
        "passive_holder_count": passive_holders,
        "arbitrage_holder_count": arbitrage_holders,
        "accumulation_breadth": accumulation,
        "distribution_breadth": distribution,
        "active_fund_accumulation_breadth": active_accumulation,
    }
