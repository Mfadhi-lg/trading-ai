"""Export a deduplicated company/quarter document inventory and recovery list.

Month-labelled call links are CANDIDATES, not proof of the financial period.
--prioritize only raises existing pending/retryable PPT and transcript jobs;
it never retries terminal URLs or overrides source policy.
"""
import argparse
import calendar
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from ..config import DB_PATH
from ..core.access_policy import is_blocked_terms_target
from ..db.repo import utc_now
from ..pipeline.promote_document_links import url_fiscal_year_conflicts


def predates_quarter(labels, period):
    from ..documents.readiness.quarter import predates_quarter as _owner
    return _owner(labels, period)


def call_months(period):
    from ..documents.readiness.quarter import call_months as _owner
    return _owner(period)


def build_inventory(conn, *, period):
    from ..workflows.documents.quarter import build_inventory as _owner
    return _owner(conn, period=period, call_months=call_months, predates_quarter=predates_quarter, url_fiscal_year_conflicts=url_fiscal_year_conflicts, is_blocked_terms_target=is_blocked_terms_target)


def repair_period_tags(conn, inventory):
    from ..db.documents.quarter import repair_period_tags as _owner
    return _owner(conn, inventory, utc_now=utc_now)


def prioritize(conn, inventory):
    from ..db.documents.quarter import prioritize as _owner
    return _owner(conn, inventory, is_blocked_terms_target=is_blocked_terms_target)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',type=Path,default=DB_PATH)
    p.add_argument('--period',required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prioritize',action='store_true'); p.add_argument('--repair-period-tags',action='store_true')
    p.add_argument('--apply',action='store_true'); args=p.parse_args()
    if (args.prioritize or args.repair_period_tags) and not args.apply:
        p.error('Mutating options require --apply')
    from ..workflows.documents.quarter import run_inventory
    return run_inventory(args, build_inventory=build_inventory, repair_period_tags=repair_period_tags, prioritize=prioritize)


if __name__=='__main__':
    main()
