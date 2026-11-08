"""Exact M06 stock-month features and workbook/change lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .mf_change_repo import adjacent_month
from .mf_feature_metrics import (
    aggregate_feature_rows,
    decimal_value,
    is_active_equity,
)

from .mutual_funds.features import (
    FEATURE_ENGINE_VERSION,
    FEATURE_SCOPES,
    FeatureGenerationSummary,
    _json,
    _effective_classification,
    _pair_inputs,
    _summary,
    _persist_validations,
    generate_stock_month_features,
    derive_stock_month_features_through,
)
