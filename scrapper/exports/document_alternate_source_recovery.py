"""Exact residual/accounting report for DA-06 official-source recovery."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import DOCUMENT_RECOVERY_EXPORTS
from ..db.repo import utc_now


RECOVERY_REPORT_SCHEMA_VERSION = "1.0.0"


def _source_bucket(source_type: str | None) -> str:
    if source_type == "NSE_EXCHANGE_ATTACHMENT":
        return "NSE"
    if source_type == "BSE_EXCHANGE_ATTACHMENT":
        return "BSE"
    if source_type == "ISSUER_IR_ATTACHMENT":
        return "issuer_ir"
    if source_type in {"NSE_DEBT_DOCUMENT", "BSE_DEBT_DOCUMENT"}:
        return "debt_issue_document"
    return "other"


def build_alternate_source_recovery_report(
    conn: sqlite3.Connection, *, generated_at_utc: str | None = None
) -> dict[str, Any]:
    """Compatible delegation to read-only recovery report persistence."""
    from ..db.documents.recovery import reports
    return reports.build_alternate_source_recovery_report(conn, generated_at_utc=generated_at_utc, _hooks=__import__(__name__, fromlist=["*"]))


def write_alternate_source_recovery_report(
    conn: sqlite3.Connection,
    *,
    output: Path | None = None,
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = build_alternate_source_recovery_report(
        conn, generated_at_utc=generated_at_utc
    )
    stamp = payload["generated_at_utc"].replace(":", "").replace("+", "_")
    destination = output or DOCUMENT_RECOVERY_EXPORTS / f"alternate-source-{stamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload, destination

