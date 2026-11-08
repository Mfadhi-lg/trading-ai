"""Document-only repair from saved Screener pages with rejected finances.

Selection never accepts the failed financial parse. Promotion and queue writes
use the existing caller-supplied acquisition function and canonical repositories.
"""
from ..readiness.quarter import call_months
from ..discovery.promotion import PROMOTER_VERSION
from ...db.documents.recovery.saved_screener_page import list_repair_candidates


def candidates(conn, *, period, limit, _hooks=None):
    months = getattr(_hooks, "call_months", call_months)(period)
    version = getattr(_hooks, "PROMOTER_VERSION", PROMOTER_VERSION)
    return list_repair_candidates(conn, period=period, limit=limit,
                                 months=months, promoter_version=version)


def repair_selected(conn, *, selected, period, enqueue):
    """Apply a bounded selected batch; retain per-company failures and counts."""
    totals={"companies":0,"created_jobs":0,"promoted_links":0,"errors":[]}
    for company_id,parse_id in selected:
        try:
            result=enqueue(
                conn,parse_id=parse_id,verified_company_id=company_id,
                resolved_result_period=period[:7],include_queue_counts=False)
        except Exception as exc:
            totals["errors"].append({"company_id":company_id,"parse_id":parse_id,
                                      "error":f"{type(exc).__name__}: {exc}"})
            continue
        totals["companies"]+=1
        totals["created_jobs"]+=result["queue"]["created_job_count"]
        totals["promoted_links"]+=sum(
            p["status_counts"].get("PROMOTED",0) for p in result["promotions"].values())
    return totals
