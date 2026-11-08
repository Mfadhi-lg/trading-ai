"""Exact, auditable M05 comparisons across consecutive accepted MF months."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .mutual_funds.changes import (
    CHANGE_ENGINE_VERSION,
    MONTH_RE,
    ChangeGenerationSummary,
    ChangeRefreshSummary,
    _json,
    _month_parts,
    adjacent_month,
    _decimal,
    _decimal_text,
    _pointer_evidence,
    _eligible_holdings,
    _candidate_scheme_ids,
    _change_class,
    _persist_change_validations,
    generate_scheme_month_changes,
    refresh_changes_for_accepted_month,
)
