"""Local saved-response workflow for the optional M09 mfdata cross-check."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..config import DB_PATH, MFDATA_EXPORTS, MFDATA_MAX_RESPONSE_BYTES, MFDATA_SNAPSHOTS
from ..db.database import connect, init_db
from ..db.mfdata_repo import persist_and_crosscheck_mfdata
from ..db.repo import utc_now
from ..exports.mfdata_crosscheck_json_export import write_mfdata_crosscheck_export
from ..sources.mfdata.adapter import parse_mfdata_holdings

from ..workflows.mutual_funds.crosscheck_mfdata import (
    execute_mfdata_crosscheck,
)
