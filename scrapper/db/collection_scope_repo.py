"""Auditable exclusions, exact identifier aliases and periodic eligibility."""
from __future__ import annotations
from .repository_backend import (
    atomic_repository, begin_immediate, commit, execute, executemany, generated_id,
    is_postgresql, rollback, statement, transaction,
)

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from ..config import SCREENER_MIN_MARKET_CAP_CRORE
from ..core.collection_scope import is_scope_exclusion, numeric_cap


def after(value, *, days=0, seconds=0):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Scope timestamps must be timezone-aware")
    return (dt + timedelta(days=days, seconds=seconds)).astimezone(timezone.utc).isoformat(timespec="seconds")


def path_of(value):
    path = urlsplit(value).path if "://" in value else value
    match = re.fullmatch(r"/company/([^/]+)/(?:consolidated/)?", path)
    return f"/company/{match[1]}/" if match else None


@atomic_repository('financial-repositories')
def record_scope(conn, *, path, company_id, value, observed_at, evidence,
                 floor=SCREENER_MIN_MARKET_CAP_CRORE, legacy_below=False, next_check=None):
    cap = numeric_cap(value)
    eligibility = ("below_floor" if legacy_below else "unknown") if cap is None else (
        "below_floor" if cap < numeric_cap(floor) else "eligible")
    encoded = json.dumps(evidence, sort_keys=True)
    execute(conn, statement(conn, 'INSERT INTO screener_collection_scope\n        (company_path,company_id,eligibility,market_cap_crore,floor_crore,\n         observed_at_utc,evidence_json,next_check_at_utc)\n        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(company_path) DO UPDATE SET\n        company_id=coalesce(excluded.company_id,screener_collection_scope.company_id),\n        eligibility=excluded.eligibility,market_cap_crore=excluded.market_cap_crore,\n        floor_crore=excluded.floor_crore,observed_at_utc=excluded.observed_at_utc,\n        evidence_json=excluded.evidence_json,next_check_at_utc=excluded.next_check_at_utc\n        WHERE julianday(excluded.observed_at_utc)>julianday(screener_collection_scope.observed_at_utc)\n        OR excluded.floor_crore<>screener_collection_scope.floor_crore', 'INSERT INTO screener_collection_scope\n        (company_path,company_id,eligibility,market_cap_crore,floor_crore,\n         observed_at_utc,evidence_json,next_check_at_utc)\n        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(company_path) DO UPDATE SET\n        company_id=coalesce(excluded.company_id,screener_collection_scope.company_id),\n        eligibility=excluded.eligibility,market_cap_crore=excluded.market_cap_crore,\n        floor_crore=excluded.floor_crore,observed_at_utc=excluded.observed_at_utc,\n        evidence_json=excluded.evidence_json,next_check_at_utc=excluded.next_check_at_utc\n        WHERE CAST(excluded.observed_at_utc AS timestamptz)>CAST(screener_collection_scope.observed_at_utc AS timestamptz)\n        OR excluded.floor_crore<>screener_collection_scope.floor_crore'),
        (path,company_id,eligibility,str(cap) if cap is not None else None,str(floor),
         observed_at,encoded,next_check or after(observed_at,days=7)))
    execute(conn, statement(conn, 'INSERT OR IGNORE INTO screener_scope_aliases VALUES (?,?,?)', 'INSERT INTO screener_scope_aliases VALUES (?,?,?) ON CONFLICT DO NOTHING'),
                 (path,path,"canonical_source_path"))
    return eligibility


@atomic_repository('financial-repositories')
def bootstrap_scope(conn, *, now, floor=SCREENER_MIN_MARKET_CAP_CRORE):
    """Seed exclusions from observed caps/errors without deleting history.

    Alias joins use verified identifiers, never company-name similarity.
    Legacy failures establish a bound only; their actual cap is unknown.
    """
    company_paths, aliases = {}, {}
    for row in execute(conn, """SELECT company_id,canonical_url FROM source_identifiers
                               WHERE source='screener' AND mapping_status='verified'"""):
        path = path_of(row["canonical_url"] or "")
        if path:
            company_paths[row["company_id"]] = path
            aliases[path] = (path,row["company_id"],"verified_source_id")
    for row in execute(conn, """SELECT company_id,identifier_value FROM security_identifiers
                               WHERE is_current=1 AND identifier_type IN ('NSE','BSE')"""):
        path = f"/company/{row['identifier_value']}/"
        canonical = company_paths.get(row["company_id"],path)
        prior = aliases.get(path)
        if prior and prior[1] != row["company_id"]:
            raise ValueError(f"Conflicting verified identity: {path}")
        aliases[path] = (canonical,row["company_id"],"verified_security_id")
        company_paths.setdefault(row["company_id"],canonical)
    counts = {"seeded":0,"excluded_queue_items":0,"duplicate_queue_items":0,"document_jobs_held":0,"registry_mappings_linked":0}
    with transaction(conn):
        # Populate the existing bridge only for unambiguous verified identities.
        matches = execute(conn, """SELECT s.current_isin,min(i.company_id) company_id
          FROM security_identifiers i JOIN exchange_securities s
            ON s.exchange=i.identifier_type AND s.exchange_key=i.identifier_value
          WHERE i.is_current=1 AND s.is_present=1 AND s.verification_status='verified'
            AND s.current_isin IS NOT NULL GROUP BY s.current_isin
          HAVING count(distinct i.company_id)=1""").fetchall()
        for match in matches:
            counts['registry_mappings_linked'] += execute(conn, """UPDATE exchange_company_registry
              SET company_id=?,updated_at_utc=? WHERE isin=? AND company_id IS NULL""",
              (match['company_id'],now,match['current_isin'])).rowcount
        rows = execute(conn, """WITH latest AS (
          SELECT company_id,parse_id,accepted_at_utc,row_number() OVER (
          PARTITION BY company_id ORDER BY accepted_at_utc DESC,parse_id DESC) rn
          FROM accepted_page_versions WHERE source='screener' AND reporting_scope='standalone')
          SELECT l.*,m.value_decimal FROM latest l LEFT JOIN company_metric_observations m
          ON m.parse_id=l.parse_id AND m.metric_code='market_cap' WHERE rn=1""").fetchall()
        for row in rows:
            path = company_paths.get(row["company_id"])
            if not path:
                continue
            # Spread initial checks over one week, including historical exclusions.
            delay = int(hashlib.sha256(path.encode()).hexdigest()[:8],16) % (7*86400)
            record_scope(conn,path=path,company_id=row["company_id"],value=row["value_decimal"],
                         observed_at=row["accepted_at_utc"],floor=floor,
                         evidence={"basis":"accepted_standalone_parse","parse_id":row["parse_id"]},
                         next_check=after(now,seconds=delay))
            counts["seeded"] += 1
        failures = execute(conn, """SELECT * FROM screener_company_queue
            WHERE reporting_scope='standalone' AND status IN ('failed_terminal','failed_retryable')
            ORDER BY updated_at_utc,queue_item_id""").fetchall()
        for row in failures:
            error = json.loads(row["last_error_json"] or "{}").get("error",{})
            if not is_scope_exclusion(error.get("message","")):
                continue
            # A historic threshold above today's floor cannot prove exclusion.
            match = re.search(r"floor:\s*([0-9.]+)",error["message"])
            legacy_floor = numeric_cap(match[1]) if match else None
            if legacy_floor is None or legacy_floor > numeric_cap(floor):
                continue
            path,cid,basis = aliases.get(row["company_path_normalized"],
                (row["company_path_normalized"],None,"observed_queue_path"))
            delay = int(hashlib.sha256(path.encode()).hexdigest()[:8],16) % (7*86400)
            record_scope(conn,path=path,company_id=cid,value=error.get("market_cap_crore"),
                         observed_at=row["updated_at_utc"],floor=floor,legacy_below=(legacy_floor < numeric_cap(floor) or "not above" not in error["message"].lower()),
                         evidence={"basis":"historical_scope_exclusion","queue_item_id":row["queue_item_id"],
                                   "recorded_floor":str(legacy_floor),"actual_cap_known":False},
                         next_check=after(now,seconds=delay))
            aliases[row["company_path_normalized"]] = (path,cid,basis)
        for alias,(path,cid,basis) in aliases.items():
            if execute(conn, "SELECT 1 FROM screener_collection_scope WHERE company_path=?",(path,)).fetchone():
                execute(conn, statement(conn, 'INSERT OR IGNORE INTO screener_scope_aliases VALUES (?,?,?)', 'INSERT INTO screener_scope_aliases VALUES (?,?,?) ON CONFLICT DO NOTHING'),(alias,path,basis))
        counts["excluded_queue_items"] = execute(conn, """UPDATE screener_company_queue AS q
            SET collection_disposition='below_market_cap'
            WHERE reporting_scope='standalone' AND status IN ('pending','failed_retryable','failed_terminal','manual_review')
            AND collection_disposition IS NULL AND EXISTS (
              SELECT 1 FROM screener_scope_aliases a JOIN screener_collection_scope s ON s.company_path=a.scope_path
              WHERE a.company_path=q.company_path_normalized AND s.eligibility='below_floor')""").rowcount
        # Retire only duplicate work under a verified alias and the SAME scope/period.
        groups = {}
        for row in execute(conn, """SELECT q.*,a.scope_path FROM screener_company_queue q
              JOIN screener_scope_aliases a ON a.company_path=q.company_path_normalized
              WHERE reporting_scope='standalone' ORDER BY
              CASE WHEN status='accepted' THEN 0 ELSE 1 END,queue_item_id"""):
            key = (row["scope_path"],row["result_period"])
            if key not in groups:
                groups[key]=row["queue_item_id"]
            elif row["status"] in ("pending","failed_retryable","failed_terminal","manual_review") and row["collection_disposition"] is None:
                execute(conn, "UPDATE screener_company_queue SET collection_disposition='duplicate_identity' WHERE queue_item_id=?",(row["queue_item_id"],))
                execute(conn, """INSERT INTO screener_company_queue_events
                    (queue_item_id,event_at_utc,from_status,to_status,evidence_json) VALUES (?,?,?,?,?)""",
                    (row["queue_item_id"],now,row["status"],row["status"],json.dumps({"reason":"duplicate_identity","canonical_queue_item_id":groups[key]})))
                counts["duplicate_queue_items"] += 1
        counts["document_jobs_held"] = execute(conn, """UPDATE artifact_download_queue AS q SET
            status='SKIPPED_POLICY',last_result_status='SCOPE_BELOW_MARKET_CAP_FLOOR',
            last_error='Collection deferred; market cap is monitored weekly',next_attempt_at_utc=NULL,
            updated_at_utc=? WHERE status IN ('PENDING','RETRYABLE_FAILURE') AND EXISTS (
            SELECT 1 FROM document_artifacts d JOIN screener_collection_scope s ON s.company_id=d.company_id
            WHERE d.artifact_id=q.artifact_id AND s.eligibility='below_floor')""",(now,)).rowcount
    return counts


@atomic_repository('financial-repositories')
def accepted_scope(conn, *, company_id, value, canonical_url, observed_at, parse_id):
    known=execute(conn, 'SELECT company_path FROM screener_collection_scope WHERE company_id=? ORDER BY observed_at_utc DESC LIMIT 1',(company_id,)).fetchone()
    path=known[0] if known else path_of(canonical_url)
    if not path:
        raise ValueError('Accepted source has no valid company path')
    with transaction(conn):
        record_scope(conn,path=path,company_id=company_id,value=value,observed_at=observed_at,
                     evidence={'basis':'accepted_standalone_parse','parse_id':parse_id})
        for row in execute(conn, "SELECT identifier_value FROM security_identifiers WHERE company_id=? AND is_current=1 AND identifier_type IN ('NSE','BSE')",(company_id,)):
            execute(conn, statement(conn, 'INSERT OR IGNORE INTO screener_scope_aliases VALUES (?,?,?)', 'INSERT INTO screener_scope_aliases VALUES (?,?,?) ON CONFLICT DO NOTHING'),
                         (f'/company/{row[0]}/',path,'verified_security_id'))


def scope_summary(conn):
    return {"eligibility":[dict(r) for r in execute(conn, """SELECT eligibility,count(*) companies,
        min(next_check_at_utc) next_check_at_utc FROM screener_collection_scope GROUP BY eligibility""")],
        "dispositions":[dict(r) for r in execute(conn, """SELECT collection_disposition,count(*) queue_items
        FROM screener_company_queue WHERE collection_disposition IS NOT NULL GROUP BY collection_disposition""")]}
