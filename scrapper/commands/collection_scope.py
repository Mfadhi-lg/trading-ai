"""Inspect, seed and monitor collection eligibility without deleting history.

Run --bootstrap --apply once; --monitor --limit 40 hourly performs one
authorized company-page request per due company (no peers/documents).
"""
import argparse
import json
import sqlite3
import uuid
from pathlib import Path

from ..config import DB_PATH, SCREENER_MIN_MARKET_CAP_CRORE, SNAPSHOTS
from ..core.collection_scope import numeric_cap, scope_snapshot
from ..core.http import ScreenerHostClient
from ..db.collection_scope_repo import after, bootstrap_scope, record_scope, scope_summary
from ..db.database import connect, init_db
from ..db.repo import start_run, finish_run, store_blob, record_fetch, utc_now
from ..sources.screener.urls import company_path_url


def apply_observation(conn, row, *, snapshot, now, evidence):
    cap = snapshot["market_cap_crore"]
    if cap is None:
        raise ValueError("Market cap is missing/non-numeric; eligibility is unresolved")
    expected = row["company_path"].strip("/").split("/")[1].upper()
    source = conn.execute("SELECT source_company_id FROM source_identifiers WHERE company_id=? AND source='screener'",
                          (row["company_id"],)).fetchone()
    if source is not None:
        if str(source[0]) != str(snapshot["source_company_id"]):
            raise ValueError("Scope monitor source identity mismatch")
    elif expected not in snapshot["identifiers"]:
        raise ValueError("Scope monitor exchange identity mismatch")
    state = record_scope(conn,path=row["company_path"],company_id=row["company_id"],value=cap,
                         observed_at=now,evidence=evidence,next_check=after(now,days=7))
    conn.execute("INSERT INTO screener_scope_observations(company_path,observed_at_utc,eligibility,market_cap_crore,evidence_json) VALUES (?,?,?,?,?)",
                 (row["company_path"],now,state,str(cap),json.dumps(evidence,sort_keys=True)))
    alias_sql = "SELECT company_path FROM screener_scope_aliases WHERE scope_path=?"
    if state == "below_floor":
        conn.execute(f"""UPDATE screener_company_queue SET collection_disposition='below_market_cap'
           WHERE company_path_normalized IN ({alias_sql}) AND reporting_scope='standalone'
           AND status IN ('pending','failed_terminal','failed_retryable','manual_review')
           AND (collection_disposition IS NULL OR collection_disposition='below_market_cap')""",(row["company_path"],))
        conn.execute("""UPDATE artifact_download_queue SET status='SKIPPED_POLICY',
           last_result_status='SCOPE_BELOW_MARKET_CAP_FLOOR',last_error='Collection deferred; market cap monitored weekly',
           next_attempt_at_utc=NULL,updated_at_utc=? WHERE status IN ('PENDING','RETRYABLE_FAILURE')
           AND artifact_id IN (SELECT artifact_id FROM document_artifacts WHERE company_id=?)""",(now,row["company_id"]))
    else:
        conn.execute(f"""UPDATE screener_company_queue SET collection_disposition=NULL
           WHERE company_path_normalized IN ({alias_sql}) AND collection_disposition='below_market_cap'""",(row["company_path"],))
        if row["eligibility"] != "eligible":
            candidate = conn.execute(f"""SELECT * FROM screener_company_queue
              WHERE company_path_normalized IN ({alias_sql}) AND reporting_scope='standalone'
              AND result_period GLOB '[12][0-9][0-9][0-9]-[01][0-9]' AND result_period>'1970-01'
              AND status IN ('failed_terminal','failed_retryable','manual_review') AND collection_disposition IS NULL
              ORDER BY result_period DESC,queue_item_id DESC LIMIT 1""",(row["company_path"],)).fetchone()
            if candidate:
                conn.execute("""UPDATE screener_company_queue SET status='pending',next_attempt_at_utc=?,
                    last_error_json=?,updated_at_utc=? WHERE queue_item_id=?""",
                    (now,json.dumps({"reason":"market_cap_reentry","observation":evidence}),now,candidate["queue_item_id"]))
                conn.execute("""INSERT INTO screener_company_queue_events
                  (queue_item_id,event_at_utc,from_status,to_status,evidence_json) VALUES (?,?,?,'pending',?)""",
                  (candidate["queue_item_id"],now,candidate["status"],json.dumps({"reason":"market_cap_reentry"})))
        # Only revive scope holds. Publisher policy skips stay untouched.
        conn.execute("""UPDATE artifact_download_queue SET status='PENDING',next_attempt_at_utc=?,
          last_result_status=NULL,last_error=NULL,updated_at_utc=?
          WHERE status='SKIPPED_POLICY' AND last_result_status='SCOPE_BELOW_MARKET_CAP_FLOOR'
          AND artifact_id IN (SELECT artifact_id FROM document_artifacts WHERE company_id=?)""",
          (now,now,row["company_id"]))
        conn.execute("""UPDATE artifact_expectations SET availability_status='expected_not_yet_published',
          next_check_at_utc=?,terminal_reason=NULL,last_error_json='{}',updated_at_utc=?
          WHERE company_id=? AND claim_token IS NULL AND availability_status='source_not_covered'
          AND (terminal_reason='market_cap_not_above_580_crore'
            OR json_extract(last_error_json,'$.type')='ScreenerMarketCapOutOfScope')""",(now,now,row["company_id"]))
    return state


def monitor(conn, *, limit, client=None, now_fn=utc_now, snapshot_root=SNAPSHOTS):
    if not 1 <= limit <= 100:
        raise ValueError("Monitor limit must be between 1 and 100")
    outcomes=[]
    for _ in range(limit):
        now=now_fn(); token=uuid.uuid4().hex
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("""SELECT * FROM screener_collection_scope
          WHERE next_check_at_utc<=? AND (claim_token IS NULL OR claim_expires_at_utc<=?)
          ORDER BY next_check_at_utc,company_path LIMIT 1""",(now,now)).fetchone()
        if row is None:
            conn.commit(); break
        conn.execute("UPDATE screener_collection_scope SET claim_token=?,claim_expires_at_utc=? WHERE company_path=?",
                     (token,after(now,seconds=600),row["company_path"]))
        conn.commit()
        run_id=None; stop=False
        try:
            client=client or ScreenerHostClient()
            run_id=start_run(conn,"scope-monitor-1",{"mode":"market_cap_monitor","path":row["company_path"]})
            response=client.fetch_company(company_path_url(row["company_path"])[0])
            blob=store_blob(conn,response,row["company_path"].split("/")[2],"standalone",now,snapshot_root,resource_kind="market_cap_check")
            fetch=record_fetch(conn,run_id,row["company_id"],response,"standalone",now,blob,resource_kind="market_cap_check")
            if response.fetch_status != "ok":
                stop=response.status_code in (401,403,429) or response.fetch_status=="rejected"
                raise RuntimeError(f"Market-cap source rejected: HTTP {response.status_code}, {response.fetch_status}")
            snapshot=scope_snapshot(response.content)
            with conn:
                owned=conn.execute("SELECT claim_token FROM screener_collection_scope WHERE company_path=?",(row["company_path"],)).fetchone()
                if not owned or owned[0]!=token:
                    raise RuntimeError("Scope monitor lease was lost")
                state=apply_observation(conn,row,snapshot=snapshot,now=now,evidence={"basis":"market_cap_check","fetch_id":fetch,"blob_id":blob})
                conn.execute("""UPDATE screener_collection_scope SET claim_token=NULL,claim_expires_at_utc=NULL,
                  check_failures=0,last_check_error=NULL WHERE company_path=? AND claim_token=?""",(row["company_path"],token))
            finish_run(conn,run_id,"ok")
            outcomes.append({"path":row["company_path"],"eligibility":state,"cap":str(snapshot["market_cap_crore"])})
        except Exception as exc:
            if run_id is not None:
                finish_run(conn,run_id,"error")
            # Retain prior eligibility and explicitly flag a failed observation.
            with conn:
                conn.execute("""UPDATE screener_collection_scope SET next_check_at_utc=?,claim_token=NULL,
                  claim_expires_at_utc=NULL,check_failures=check_failures+1,last_check_error=?
                  WHERE company_path=? AND claim_token=?""",
                  (after(now,days=min(7,2**min(row["check_failures"],3))),str(exc)[:500],row["company_path"],token))
            outcomes.append({"path":row["company_path"],"error":str(exc)[:500]})
        if stop:
            break
    return {"checked":len(outcomes),"outcomes":outcomes}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=DB_PATH)
    group=p.add_mutually_exclusive_group(); group.add_argument("--bootstrap",action="store_true"); group.add_argument("--monitor",action="store_true")
    p.add_argument("--apply",action="store_true"); p.add_argument("--limit",type=int,default=40)
    args=p.parse_args()
    if (args.bootstrap or args.monitor) and not args.apply:
        p.error("Mutating commands require --apply; omit mode flags for read-only status")
    if args.bootstrap or args.monitor:
        conn=connect(args.db); init_db(conn)
    else:
        conn=sqlite3.connect(f"file:{args.db.resolve()}?mode=ro",uri=True); conn.row_factory=sqlite3.Row
    try:
        result=bootstrap_scope(conn,now=utc_now()) if args.bootstrap else monitor(conn,limit=args.limit) if args.monitor else scope_summary(conn)
        print(json.dumps(result,sort_keys=True))
    finally:
        conn.close()


if __name__=="__main__":
    main()
