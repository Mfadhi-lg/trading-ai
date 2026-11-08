"""Compatibility facade for grouped workbook persistence owners."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..sources.amc_workbook.detector import WorkbookInspection, safety_report_json
from ..sources.amc_workbook.adapters import adapter_for_parsed, get_workbook_adapter
from ..sources.amc_workbook.ppfas import (
    NormalizedHolding,
    PPFASStructureError,
    PPFASWorkbook,
    expected_scheme_codes,
    load_manifest,
    normalized_security_name,
)


from .mutual_funds.workbook_inventory import (
    PersistedImport,
    M02_PASSING_RULES,
    persist_inventory,
    _json,
    _table_position,
    persist_ppfas_structure,
    persist_ppfas_structure_failure,
)
from .mutual_funds.holdings import (
    HoldingsNormalizationSummary,
    M03_VALIDATION_RULE_COUNT,
    M03_VALIDATION_RULES,
    _upsert_m03_validation,
    _security_for_isin,
    persist_ppfas_holdings,
    holdings_normalization_summary,
)

from .mutual_funds.scheme_acceptance import (
    SchemeRegistrySummary,
    ImportFinalization,
    M04_VALIDATION_RULES,
    _resolved_classification,
    persist_scheme_registry,
    _transition_import_state,
    finalize_ppfas_import,
)
