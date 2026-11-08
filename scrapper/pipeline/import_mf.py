"""Local official-AMC workbook planning, import, acceptance, and export."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from ..config import AMC_WORKBOOK_SNAPSHOTS, DB_PATH, MF_IMPORT
from ..db.database import connect, init_db
from ..db.mutual_funds.changes import refresh_changes_for_accepted_month
from ..db.mutual_funds.features import generate_stock_month_features
from ..db.mutual_funds.scheme_acceptance import finalize_ppfas_import
from ..db.mutual_funds.holdings import persist_ppfas_holdings
from ..db.mutual_funds.workbook_inventory import (
    persist_inventory,
    persist_ppfas_structure,
    persist_ppfas_structure_failure,
)
from ..exports.mf_workbook_json_export import write_mf_workbook_export
from ..db.repo import utc_now
from ..sources.amc_workbook.detector import WorkbookInspection, inspect_workbook
from ..sources.amc_workbook.adapters import get_workbook_adapter
from ..sources.amc_workbook.ppfas import (
    PPFASStructureError,
    PPFASWorkbook,
    normalize_ppfas_holdings,
    parse_ppfas_workbook,
)

from ..workflows.mutual_funds.import_mf import (
    PUBLISHER_RE,
    MONTH_RE,
    IMPORT_PLAN_SCHEMA_VERSION,
    ProposedImport,
    normalize_publisher,
    validate_month,
    _candidate_files,
    _destination,
    _known_inventory,
    plan_import,
    plan_directory_import,
    import_plan_payload,
    _copy_immutable,
    execute_import,
)
