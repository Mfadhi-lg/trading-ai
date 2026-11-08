"""P03 deterministic and resumable Trendlyne month-pilot orchestration."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import DB_PATH, THROTTLE_JITTER, THROTTLE_SECONDS
from ..db.database import connect, init_db
from ..db.repo import utc_now
from ..db.trendlyne_repo import ensure_trendlyne_company
from ..sources.trendlyne.urls import monthly_mf_url, source_period_for_url, verified_mapping
from ..workflows.mutual_funds.trendlyne_vertical import TrendlyneSliceResult, scrape_trendlyne_company

from ..workflows.mutual_funds.trendlyne_pilot import (
    PILOT_CODE,
    PILOT_SYMBOLS,
    MIN_MONTHS,
    MAX_MONTHS,
    MAX_BATCH_SIZE,
    TrendlynePilotRequest,
    TrendlynePilotPlan,
    TrendlynePilotRunResult,
    _canonical_periods,
    _pilot_mappings,
    _accepted_parse_id,
    build_trendlyne_pilot_plan,
    _persist_plan,
    _reconcile_plan,
    register_trendlyne_pilot_plan,
    run_trendlyne_pilot_batch,
)
