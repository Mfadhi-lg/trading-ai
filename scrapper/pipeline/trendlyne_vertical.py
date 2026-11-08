"""Trendlyne monthly-MF ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..config import (
    DB_PATH,
    EXPORTS,
    SNAPSHOTS,
    TRENDLYNE_MANIFEST_VERSION,
    TRENDLYNE_PARSER_NAME,
    TRENDLYNE_PARSER_VERSION,
)
from ..core.http import FetchResponse, fetch_trendlyne
from ..db.database import connect, init_db
from ..db.repo import (
    finish_run,
    get_or_create_parse_artifact,
    start_run,
    utc_now,
)
from ..db.trendlyne_repo import (
    ensure_trendlyne_company,
    record_trendlyne_fetch,
    store_parsed_trendlyne_page,
    store_trendlyne_blob,
)
from ..exports.trendlyne_json_export import write_trendlyne_export
from ..sources.trendlyne.monthly_mf import parse_monthly_mf_page
from ..sources.trendlyne.urls import (
    monthly_mf_url,
    source_period_for_url,
    verified_mapping,
)
from ..validation.trendlyne import accept_trendlyne_parse, validate_trendlyne_parse

from ..workflows.mutual_funds.trendlyne_vertical import (
    TrendlyneSliceResult,
    ingest_trendlyne_response,
    scrape_trendlyne_company,
)
