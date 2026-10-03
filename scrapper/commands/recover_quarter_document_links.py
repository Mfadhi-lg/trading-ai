"""Recover quarter document links from saved pages with a financial manifest failure.

Financial observations remain rejected. The company identity, accepted quarter,
saved company fetch and document link are checked independently before promotion.
"""
import argparse
import json
import sqlite3
from pathlib import Path

from ..config import DB_PATH
from ..db.database import connect, init_db
from ..pipeline.accepted_document_downloads import enqueue_concall_documents_for_accepted_parse
from ..pipeline.promote_document_links import PROMOTER_VERSION
from .quarter_documents import call_months


def candidates(conn, *, period, limit):
    months = call_months(period)
    placeholders = ",".join("?" for _ in months)
    rows = conn.execute(f"""WITH ranked AS (
      SELECT f.company_id,p.parse_id,max(f.fetched_at_utc) fetched_at_utc,
             row_number() OVER (PARTITION BY f.company_id
               ORDER BY max(f.fetched_at_utc) DESC,p.parse_id DESC) rn
      FROM parse_artifacts p
      JOIN fetch_observations f ON f.blob_id=p.blob_id AND f.source='screener'
        AND f.resource_kind='company_page' AND f.company_id IS NOT NULL
      JOIN screener_collection_scope scope ON scope.company_id=f.company_id
        AND scope.eligibility='eligible'
      JOIN source_identifiers sid ON sid.company_id=f.company_id
        AND sid.source='screener' AND sid.mapping_status='verified'
      JOIN document_links link ON link.parse_id=p.parse_id
        AND link.document_type IN ('concall_ppt','concall_transcript','concall_recording')
        AND link.period_label_raw IN ({placeholders})
      WHERE p.parse_status='ok' AND p.validation_status='failed'
        AND EXISTS (SELECT 1 FROM validation_results v WHERE v.parse_id=p.parse_id
          AND v.rule_code='screener_manifest_coverage' AND v.status='failed')
        AND NOT EXISTS (SELECT 1 FROM validation_results v WHERE v.parse_id=p.parse_id
          AND v.severity='error' AND v.status='failed'
          AND v.rule_code!='screener_manifest_coverage')
        AND EXISTS (SELECT 1 FROM accepted_page_versions a
          WHERE a.company_id=f.company_id AND a.source='screener'
            AND a.reporting_scope='standalone' AND a.resolved_source_period=?)
        AND NOT EXISTS (SELECT 1 FROM accepted_page_versions a WHERE a.parse_id=p.parse_id)
      GROUP BY f.company_id,p.parse_id
    ) SELECT company_id,parse_id FROM ranked r WHERE rn=1
      AND EXISTS (SELECT 1 FROM document_links l WHERE l.parse_id=r.parse_id
        AND l.document_type IN ('concall_ppt','concall_transcript','concall_recording')
        AND NOT EXISTS (SELECT 1 FROM document_link_promotions m
          WHERE m.document_link_id=l.document_link_id AND m.promoter_version=?))
      ORDER BY company_id LIMIT ?""",(*months,period[:7],PROMOTER_VERSION,limit)).fetchall()
    return [(r["company_id"],r["parse_id"]) for r in rows]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=DB_PATH)
    parser.add_argument("--period",required=True)
    parser.add_argument("--limit",type=int,default=50)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if not 1<=args.limit<=200:
        parser.error("Limit must be between 1 and 200")
    conn=connect(args.db) if args.apply else sqlite3.connect(f"file:{args.db.resolve()}?mode=ro",uri=True)
    conn.row_factory=sqlite3.Row
    try:
        if args.apply:
            init_db(conn)
        selected=candidates(conn,period=args.period,limit=args.limit)
        if not args.apply:
            print(json.dumps({"period_end":args.period,"candidate_count":len(selected),
                              "sample":selected[:10],"applied":False}))
            return
        totals={"companies":0,"created_jobs":0,"promoted_links":0,"errors":[]}
        for company_id,parse_id in selected:
            try:
                result=enqueue_concall_documents_for_accepted_parse(
                    conn,parse_id=parse_id,verified_company_id=company_id,
                    resolved_result_period=args.period[:7],include_queue_counts=False)
            except Exception as exc:
                totals["errors"].append({"company_id":company_id,"parse_id":parse_id,
                                          "error":f"{type(exc).__name__}: {exc}"})
                continue
            totals["companies"]+=1
            totals["created_jobs"]+=result["queue"]["created_job_count"]
            totals["promoted_links"]+=sum(
                p["status_counts"].get("PROMOTED",0) for p in result["promotions"].values())
        print(json.dumps({"period_end":args.period,"selected_count":len(selected),
                          "applied":True,**totals}))
        if totals["errors"]:
            raise SystemExit(2)
    finally:
        conn.close()


if __name__=="__main__":
    main()
