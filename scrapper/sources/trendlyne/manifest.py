"""Load and validate the versioned Trendlyne monthly-MF source manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "manifests" / "trendlyne_monthly_mf.yml"
)
VALID_STATUSES = {"capture", "metadata_only", "conditional", "deferred"}


def load_trendlyne_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("page_type") != "trendlyne_monthly_mutual_fund_holdings":
        raise ValueError("Trendlyne manifest has the wrong page_type")
    if not manifest.get("manifest_version"):
        raise ValueError("Trendlyne manifest has no version")
    approvals = {
        row["id"]
        for row in manifest.get("deferral_approvals", [])
        if row.get("approved") is True
    }
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("Trendlyne manifest components are empty")
    seen: set[str] = set()
    for row in components:
        code = row.get("code")
        if not code or code in seen:
            raise ValueError(f"Invalid or duplicate Trendlyne component {code!r}")
        seen.add(code)
        status = row.get("status")
        if status not in VALID_STATUSES or not row.get("source"):
            raise ValueError(f"Invalid Trendlyne decision for {code!r}")
        if status == "conditional" and not row.get("condition"):
            raise ValueError(f"Conditional Trendlyne component {code!r} has no condition")
        if status == "deferred" and row.get("deferral_approval") not in approvals:
            raise ValueError(f"Deferred Trendlyne component {code!r} is not approved")
    return manifest

