"""Audit actual quarter rows, repair accepted period labels, and refresh gaps."""
from __future__ import annotations

import re
import sys
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from ..config import DB_PATH, EXPORTS, SNAPSHOTS
from ..db.database import connect, init_db
from ..db.repo import start_run, finish_run, utc_now
from ..exports.json_export import write_export
from ..validation.screener import accept_parse, sync_screener_quarter_state


def period_coverage(conn, period: str, symbols=()) -> list[dict]:
    if not re.fullmatch(r"20\d\d-(03|06|09|12)", period):
        raise ValueError("Result period must be a quarter end in YYYY-MM format")
    wanted = {symbol.upper() for symbol in symbols}
    rows = conn.execute("""SELECT a.*, x.source_symbol, x.canonical_url AS source_url,
                         b.first_observed_at_utc
        FROM accepted_page_versions a
        JOIN source_identifiers x ON x.company_id=a.company_id AND x.source='screener'
        JOIN parse_artifacts p ON p.parse_id=a.parse_id
        JOIN response_blobs b ON b.blob_id=p.blob_id
        WHERE a.source='screener' AND a.reporting_scope='standalone'
        AND (? OR NOT EXISTS (SELECT 1 FROM screener_collection_scope scope
            WHERE scope.company_id=a.company_id AND scope.eligibility='below_floor'))
        ORDER BY b.first_observed_at_utc DESC, a.parse_id DESC, a.resolved_source_period DESC""", (bool(wanted),)).fetchall()
    selected = {}
    for raw_row in rows:
        row = dict(raw_row)
        row['source_symbol'] = row['source_symbol'] or urlsplit(row['source_url']).path.strip('/').split('/')[1]
        if wanted and row['source_symbol'].upper() not in wanted:
            continue
        selected.setdefault(row['company_id'], row)
    result = []
    for cid, row in selected.items():
        periods = [r[0] for r in conn.execute("""SELECT DISTINCT substr(period_end,1,7)
            FROM financial_observations WHERE parse_id=? AND section_code='quarters'
              AND reporting_scope='standalone' AND period_end IS NOT NULL
            ORDER BY period_end""", (row['parse_id'],))]
        actual = max(periods) if periods else 'not_applicable'
        mislabeled = [dict(pointer) for pointer in conn.execute("""SELECT a.parse_id,
            a.resolved_source_period AS stored_period, MAX(substr(f.period_end,1,7)) AS actual_period
            FROM accepted_page_versions a JOIN financial_observations f ON f.parse_id=a.parse_id
            WHERE a.company_id=? AND a.source='screener' AND a.reporting_scope='standalone'
              AND f.section_code='quarters' AND f.reporting_scope='standalone'
              AND f.period_end IS NOT NULL
            GROUP BY a.parse_id,a.resolved_source_period
            HAVING a.resolved_source_period NOT IN (
                SELECT substr(q.period_end,1,7) FROM financial_observations q
                WHERE q.parse_id=a.parse_id AND q.section_code='quarters'
                  AND q.reporting_scope='standalone' AND q.period_end IS NOT NULL)""",(cid,))]
        result.append(dict(company_id=cid, symbol=row['source_symbol'],
                           parse_id=row['parse_id'], source_url=row['source_url'],
                           captured_at_utc=row['first_observed_at_utc'],
                           stored_period=row['resolved_source_period'], latest_quarter=actual,
                           target_period=period, has_target_quarter=period in periods,
                           period_tag_mismatch=bool(mislabeled) or row['resolved_source_period'] != actual,
                           mislabeled_pointers=mislabeled))
    found = {r['symbol'].upper() for r in result}
    if wanted - found:
        raise ValueError(f"No accepted standalone capture for: {sorted(wanted - found)}")
    return sorted(result, key=lambda r: r['symbol'])


def restore_valid_historical_periods(conn):
    """Keep real historical quarter views when correcting placeholder labels."""
    rows = conn.execute("""SELECT DISTINCT e.company_id,e.parse_id,e.resolved_source_period,
            b.first_observed_at_utc
        FROM acceptance_events e JOIN parse_artifacts p ON p.parse_id=e.parse_id
        JOIN response_blobs b ON b.blob_id=p.blob_id
        WHERE e.source='screener' AND e.reporting_scope='standalone'
          AND e.action='superseded' AND e.acceptance_mode='period_label_repair'
          AND EXISTS (SELECT 1 FROM financial_observations f WHERE f.parse_id=e.parse_id
              AND f.section_code='quarters' AND f.reporting_scope='standalone'
              AND substr(f.period_end,1,7)=e.resolved_source_period)
        ORDER BY b.first_observed_at_utc DESC,e.parse_id DESC""").fetchall()
    restored=0
    for row in rows:
        current=conn.execute("""SELECT 1 FROM accepted_page_versions WHERE company_id=?
            AND source='screener' AND reporting_scope='standalone' AND resolved_source_period=?""",
            (row['company_id'],row['resolved_source_period'])).fetchone()
        if current is None:
            accept_parse(conn,row['company_id'],row['parse_id'],'screener','standalone',row['resolved_source_period'])
            restored+=1
    return restored


def recent_source_check(conn, row, period):
    """Reuse a validated response from the last day when June is absent."""
    from ..sources.screener.urls import company_url
    from ..core.collection_scope import scope_snapshot
    from ..sources.screener.company import _parse_quarters
    from bs4 import BeautifulSoup
    from pathlib import Path
    url = company_url(row['symbol'], 'standalone')
    saved = conn.execute("""SELECT f.fetched_at_utc,b.local_path,b.blob_id FROM fetch_observations f
        JOIN response_blobs b ON b.blob_id=f.blob_id
        WHERE f.requested_url=? AND f.resource_kind='company_page' AND f.fetch_status='ok'
        ORDER BY f.fetched_at_utc DESC LIMIT 1""", (url,)).fetchone()
    if not saved or datetime.fromisoformat(saved['fetched_at_utc']) < datetime.now(timezone.utc)-timedelta(days=1):
        return None
    try:
        content = Path(saved['local_path']).read_bytes()
        cap = scope_snapshot(content)['market_cap_crore']
        _, _, quarters = _parse_quarters(BeautifulSoup(content,'html.parser'))
        periods = sorted({q.period_end[:7] for q in quarters if q.period_end})
    except (ValueError,OSError):
        return None
    from ..config import SCREENER_MIN_MARKET_CAP_CRORE
    if cap is not None and cap < SCREENER_MIN_MARKET_CAP_CRORE:
        from ..db.collection_scope_repo import record_scope
        with conn:
            record_scope(conn,path=urlsplit(url).path,company_id=row['company_id'],value=cap,
                observed_at=saved['fetched_at_utc'],evidence={'basis':'saved_quarter_refresh', 'blob_id':saved['blob_id']})
        kind='below_collection_floor'
    elif period not in periods:
        kind='quarter_absent_at_source'
    else:
        return None
    return dict(symbol=row['symbol'],kind=kind,checked_at_utc=saved['fetched_at_utc'],
                blob_id=saved['blob_id'],source_periods=periods,reused_saved_response=True)


def run_period_coverage(*, period, symbols=(), repair=False, refresh=False, limit=25,
                        db_path=DB_PATH, snapshot_root=SNAPSHOTS, export_root=EXPORTS):
    if limit < 1 or limit > 100:
        raise ValueError('Refresh limit must be between 1 and 100')
    conn = connect(db_path)
    init_db(conn)
    repaired = []
    refreshed = []
    failures = []
    try:
        restored = restore_valid_historical_periods(conn) if repair else 0
        before = period_coverage(conn, period, symbols)
        if repair:
            for row in before:
                sync_screener_quarter_state(conn,row['company_id'],'standalone')
                if not row['period_tag_mismatch'] or row['latest_quarter'] == 'not_applicable':
                    continue
                run_id = start_run(conn, 'period-coverage-v1', {'mode':'period_label_repair', 'company_id':row['company_id']})
                try:
                    accept_parse(conn, row['company_id'], row['parse_id'], 'screener',
                                 'standalone', row['latest_quarter'])
                    with conn:
                        for pointer in row['mislabeled_pointers']:
                            conn.execute("""INSERT INTO acceptance_events
                                (company_id,source,reporting_scope,resolved_source_period,parse_id,
                                 action,accepted_at_utc,accepted_by,acceptance_mode,reason_json)
                                VALUES (?,'screener','standalone',?,?,'superseded',?,'system',
                                        'period_label_repair',?)""",
                                (row['company_id'],pointer['stored_period'],pointer['parse_id'],utc_now(),
                                 json.dumps({'reason':'incorrect_period_label','actual_period':pointer['actual_period']})))
                            conn.execute("""DELETE FROM accepted_page_versions
                                WHERE company_id=? AND source='screener' AND reporting_scope='standalone'
                                AND parse_id=? AND resolved_source_period=?""",
                                (row['company_id'],pointer['parse_id'],pointer['stored_period']))
                            conn.execute("""DELETE FROM company_period_scope_policy
                                WHERE company_id=? AND reporting_scope='standalone'
                                  AND parse_id=? AND period=?""",
                                (row['company_id'],pointer['parse_id'],pointer['stored_period']))
                        conn.execute("""UPDATE screener_company_collection_state
                            SET latest_accepted_result_period=?, latest_accepted_parse_id=?, updated_at_utc=?
                            WHERE company_id=? AND reporting_scope='standalone'""",
                            (row['latest_quarter'], row['parse_id'], utc_now(), row['company_id']))
                        conn.execute("""INSERT INTO company_period_scope_policy
                            (company_id,period,reporting_scope,parse_id,accepted_at_utc)
                            VALUES (?,?,'standalone',?,?)
                            ON CONFLICT(company_id,period,reporting_scope) DO UPDATE SET
                            parse_id=excluded.parse_id,accepted_at_utc=excluded.accepted_at_utc""",
                            (row['company_id'],row['latest_quarter'],row['parse_id'],utc_now()))
                    path = write_export(conn, run_id, row['company_id'], row['parse_id'], row['symbol'], export_root)
                    finish_run(conn, run_id, 'ok')
                    repaired.append(dict(symbol=row['symbol'], period=row['latest_quarter'], export_path=str(path)))
                    if len(repaired) % 50 == 0:
                        print(f"Repaired {len(repaired)} period labels", file=sys.stderr, flush=True)
                except Exception:
                    finish_run(conn, run_id, 'error')
                    raise
        if refresh:
            from ..pipeline.screener_vertical import scrape_screener_company_path
            from ..core.http import ScreenerHostClient
            from ..sources.screener.urls import company_url
            from urllib.parse import urlsplit
            client = None
            gaps = [row for row in before if not row['has_target_quarter']]
            for row in gaps[:limit]:
                checked = recent_source_check(conn,row,period)
                if checked is not None:
                    failures.append(checked)
                    print(f"Reused recent source check for {row['symbol']}: {checked['kind']}",file=sys.stderr,flush=True)
                    continue
                print(f"Refreshing {row['symbol']} for {period}", file=sys.stderr, flush=True)
                try:
                    path = urlsplit(company_url(row['symbol'], 'standalone')).path
                    client = client or ScreenerHostClient()
                    result = scrape_screener_company_path(path, 'standalone',
                        expected_result_period=period, host_client=client,
                        db_path=db_path, snapshot_root=snapshot_root, export_root=export_root)
                    refreshed.append(dict(symbol=row['symbol'], parse_id=result.parse_id, export_path=str(result.export_path)))
                except (ValueError, RuntimeError, OSError) as exc:
                    from ..core.collection_scope import MarketCapBelowFloor
                    if isinstance(exc, MarketCapBelowFloor):
                        from ..db.collection_scope_repo import record_scope
                        with conn:
                            record_scope(conn, path=path, company_id=row['company_id'],
                                value=exc.market_cap_crore, observed_at=utc_now(),
                                evidence={'reason':'quarter_refresh_scope_check', 'blob_id':getattr(exc,'blob_id',None)},
                                floor=exc.floor_crore)
                        kind = 'below_collection_floor'
                    elif str(exc).startswith('Screen result period is absent'):
                        kind = 'quarter_absent_at_source'
                    else:
                        kind = 'refresh_failed'
                    failures.append(dict(symbol=row['symbol'], kind=kind, error=str(exc)))
                    print(f"Gap retained for {row['symbol']}: {exc}", file=sys.stderr, flush=True)
                    # Stop this bounded batch on access rejection; retain all gaps.
                    if any(word in str(exc).lower() for word in ('403', '429', 'blocked', 'captcha')):
                        break
        after = period_coverage(conn, period, symbols)
        source_absences = [f for f in failures if f.get('kind') == 'quarter_absent_at_source']
        exclusions = [f for f in failures if f.get('kind') == 'below_collection_floor']
        refresh_failures = [f for f in failures if f.get('kind') == 'refresh_failed']
        return dict(period=period, company_count=len(after),restored_historical_periods=restored,
                    covered_count=sum(r['has_target_quarter'] for r in after),
                    missing_count=sum(not r['has_target_quarter'] for r in after),
                    period_tag_mismatch_count=sum(r['period_tag_mismatch'] for r in after),
                    repaired=repaired, refreshed=refreshed, failures=failures,
                    source_absences=source_absences,exclusions=exclusions,refresh_failures=refresh_failures,
                    companies=after)
    finally:
        conn.close()
