"""Screener financial acceptance history, version pointers and quarter state.

SQL, acceptance safeguards and transaction boundaries are preserved. The
validation facade supplies its clock and synchronization hook for compatibility.
"""
from __future__ import annotations
import json
import sqlite3

def accept_parse(conn: sqlite3.Connection, company_id: int, parse_id: int, source: str, scope: str, resolved_source_period: str='not_applicable', *, _utc_now, sync_screener_quarter_state) -> None:
    if scope != 'standalone':
        raise ValueError('automatic financial acceptance requires reporting_scope=standalone')
    artifact = conn.execute('SELECT parse_status, validation_status FROM parse_artifacts WHERE parse_id=?', (parse_id,)).fetchone()
    blocking_rules = [str(row['rule_code']) for row in conn.execute("SELECT rule_code FROM validation_results\n               WHERE parse_id=? AND status='failed'\n                 AND severity IN ('error', 'manual_review')\n               ORDER BY rule_code", (parse_id,)).fetchall()]
    blocking = len(blocking_rules)
    actual_scope = conn.execute('SELECT reporting_scope FROM quote_observations\n           WHERE parse_id=?', (parse_id,)).fetchone()
    scope_matches = actual_scope is not None and actual_scope['reporting_scope'] == scope
    is_blocked = artifact is None or artifact['parse_status'] != 'ok' or artifact['validation_status'] not in {'passed', 'passed_with_warnings'} or blocking or (not scope_matches)
    now = _utc_now()
    if is_blocked:
        reason = {'parse_status': artifact['parse_status'] if artifact else 'missing', 'validation_status': artifact['validation_status'] if artifact else 'missing', 'blocking_validation_count': int(blocking), 'blocking_rule_codes': blocking_rules, 'requested_reporting_scope': scope, 'actual_reporting_scope': actual_scope['reporting_scope'] if actual_scope is not None else None}
        if artifact is not None:
            with conn:
                conn.execute("INSERT INTO acceptance_events\n                       (company_id, source, reporting_scope,\n                        resolved_source_period, parse_id, action,\n                        accepted_at_utc, accepted_by, acceptance_mode,\n                        reason_json)\n                       VALUES (?, ?, ?, ?, ?, 'rejected', ?, 'system',\n                               'automatic', ?)", (company_id, source, scope, resolved_source_period, parse_id, now, json.dumps(reason, sort_keys=True)))
        detail = ', '.join(blocking_rules) if blocking_rules else 'none'
        raise ValueError(f"Parse artifact does not satisfy automatic acceptance rules (parse_status={reason['parse_status']}, validation_status={reason['validation_status']}, requested_scope={scope}, actual_scope={reason['actual_reporting_scope']}, blocking_rules=[{detail}])")
    current = conn.execute('SELECT parse_id FROM accepted_page_versions\n           WHERE company_id=? AND source=? AND reporting_scope=?\n             AND resolved_source_period=?', (company_id, source, scope, resolved_source_period)).fetchone()
    current_parse_id = int(current['parse_id']) if current else None
    if current_parse_id == parse_id:
        if source == 'screener':
            sync_screener_quarter_state(conn, company_id, scope)
        return
    with conn:
        if current_parse_id is not None:
            conn.execute("INSERT INTO acceptance_events\n                   (company_id, source, reporting_scope, resolved_source_period,\n                    parse_id, action, accepted_at_utc, accepted_by,\n                    acceptance_mode, reason_json)\n                   VALUES (?, ?, ?, ?, ?, 'superseded', ?, 'system',\n                           'automatic', ?)", (company_id, source, scope, resolved_source_period, current_parse_id, now, json.dumps({'superseded_by_parse_id': parse_id}, sort_keys=True)))
        conn.execute("INSERT INTO acceptance_events\n               (company_id, source, reporting_scope, resolved_source_period,\n                parse_id, action, accepted_at_utc, accepted_by,\n                acceptance_mode, reason_json)\n               VALUES (?, ?, ?, ?, ?, 'accepted', ?, 'system', 'automatic', ?)", (company_id, source, scope, resolved_source_period, parse_id, now, json.dumps({'previous_parse_id': current_parse_id}, sort_keys=True)))
        conn.execute("INSERT INTO accepted_page_versions\n               (company_id, source, reporting_scope, resolved_source_period, parse_id,\n                accepted_at_utc, accepted_by, acceptance_mode)\n               VALUES (?, ?, ?, ?, ?, ?, 'system', 'automatic')\n               ON CONFLICT(company_id, source, reporting_scope, resolved_source_period)\n               DO UPDATE SET parse_id=excluded.parse_id,\n                             accepted_at_utc=excluded.accepted_at_utc,\n                             accepted_by=excluded.accepted_by,\n                             acceptance_mode=excluded.acceptance_mode", (company_id, source, scope, resolved_source_period, parse_id, now))
    if source == 'screener':
        sync_screener_quarter_state(conn, company_id, scope)

def sync_screener_quarter_state(conn, company_id: int, scope: str, *, _utc_now) -> None:
    """Keep the queue watermark consistent after manual and queued acceptance."""
    latest = conn.execute("SELECT f.parse_id,substr(f.period_end,1,7) AS period\n           FROM accepted_page_versions a JOIN financial_observations f ON f.parse_id=a.parse_id\n           JOIN parse_artifacts p ON p.parse_id=a.parse_id\n           JOIN response_blobs b ON b.blob_id=p.blob_id\n           WHERE a.company_id=? AND a.source='screener' AND a.reporting_scope=?\n             AND f.section_code='quarters' AND f.reporting_scope=a.reporting_scope\n             AND f.period_end IS NOT NULL\n           ORDER BY f.period_end DESC,b.first_observed_at_utc DESC,f.parse_id DESC LIMIT 1", (company_id, scope)).fetchone()
    if latest is not None:
        with conn:
            conn.execute('UPDATE screener_company_collection_state\n                SET latest_accepted_result_period=?,latest_accepted_parse_id=?,updated_at_utc=?\n                WHERE company_id=? AND reporting_scope=?', (latest['period'], latest['parse_id'], _utc_now(), company_id, scope))
