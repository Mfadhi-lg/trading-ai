"""Exact Decimal aggregation rules shared by M06 feature persistence."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation

from .mutual_funds.feature_metrics import (
    decimal_value,
    decimal_text,
    is_active_equity,
    aggregate_feature_rows,
)
