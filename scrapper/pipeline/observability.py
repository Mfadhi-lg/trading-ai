"""Read-only, structured health snapshot for ingestion operations."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any


def operational_snapshot(conn: sqlite3.Connection, *, now_utc: str) -> dict[str, Any]:
    """Return compact JSON-safe evidence for queue, source and scope health.

    This intentionally does no repair: an operator sees policy/data failures as
    failures instead of an observability call changing their state.
    """
    queue = conn.execute("SELECT status, COUNT(*) n FROM screener_company_queue GROUP BY status").fetchall()
    oldest = conn.execute(
        "SELECT MIN(created_at_utc) FROM screener_company_queue WHERE status IN ('pending','failed_retryable')"
    ).fetchone()[0]
    leases = conn.execute(
        "SELECT SUM(status IN ('claimed','running')), SUM(status IN ('claimed','running') AND claim_expires_at_utc < ?) FROM screener_company_queue",
        (now_utc,),
    ).fetchone()
    http = conn.execute(
        "SELECT source,http_status,COUNT(*) n FROM fetch_observations GROUP BY source,http_status"
    ).fetchall()
    scope = conn.execute(
        "SELECT reporting_scope,COUNT(*) n FROM financial_observations GROUP BY reporting_scope"
    ).fetchall()
    audit_count = conn.execute("SELECT COUNT(*) FROM financial_scope_integrity_audit").fetchone()[0]
    return {
        "observed_at_utc": now_utc,
        "queue_depth_by_status": {row["status"]: row["n"] for row in queue},
        "oldest_pending_created_at_utc": oldest,
        "active_leases": int(leases[0] or 0),
        "stale_leases": int(leases[1] or 0),
        "http_status_distribution": [dict(row) for row in http],
        "financial_observations_by_scope": {row["reporting_scope"]: row["n"] for row in scope},
        "standalone_contract_rejections": int(audit_count),
    }
