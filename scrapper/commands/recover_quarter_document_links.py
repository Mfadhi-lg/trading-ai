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
from ..documents.readiness.quarter import call_months


def candidates(conn, *, period, limit):
    """Compatible selection facade retaining local dependency patch points."""
    from ..documents.recovery import saved_screener_page
    return saved_screener_page.candidates(
        conn, period=period, limit=limit,
        _hooks=__import__(__name__, fromlist=["*"]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=DB_PATH)
    parser.add_argument("--period",required=True)
    parser.add_argument("--limit",type=int,default=50)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if not 1<=args.limit<=200:
        parser.error("Limit must be between 1 and 200")
    conn=connect(args.db, read_only=not args.apply)
    conn.row_factory=sqlite3.Row
    try:
        if args.apply:
            init_db(conn)
        selected=candidates(conn,period=args.period,limit=args.limit)
        if not args.apply:
            print(json.dumps({"period_end":args.period,"candidate_count":len(selected),
                              "sample":selected[:10],"applied":False}))
            return
        from ..documents.recovery.saved_screener_page import repair_selected
        totals=repair_selected(conn,selected=selected,period=args.period,
            enqueue=enqueue_concall_documents_for_accepted_parse)
        print(json.dumps({"period_end":args.period,"selected_count":len(selected),
                          "applied":True,**totals}))
        if totals["errors"]:
            raise SystemExit(2)
    finally:
        conn.close()


if __name__=="__main__":
    main()
