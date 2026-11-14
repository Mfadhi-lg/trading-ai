"""Canonical result-to-document handoff and bounded read-only timing report."""
from __future__ import annotations

from ..repository_backend import execute
from ...documents.availability import parse_utc
from ...documents.freshness import download_timings, measured_delays
from .expectations import ensure_result_artifact_expectations


def register_filing_document_expectations(conn, filing, outcome, *, now_utc):
    """Start link discovery before financial parsing, for an unambiguous issuer.

    The registry identity is not an application company ID. Resolve the exact
    current exchange identifier and require a single eligible application issuer.
    Unknown/ambiguous identities remain in the existing financial queue only.
    """
    if outcome.get('decision') not in ('queued', 'reused'):
        return ()
    path = outcome.get('company_path_normalized', '')
    parts = path.strip('/').split('/')
    if len(parts) != 2 or parts[0] != 'company':
        return ()
    if outcome.get('path_basis') not in ('nse_symbol', 'bse_scrip_code'):
        return ()
    kind = 'NSE' if outcome.get('path_basis') == 'nse_symbol' else 'BSE'
    matches = execute(conn, '''SELECT DISTINCT i.company_id FROM security_identifiers i
        WHERE i.identifier_type=? AND i.identifier_value=? AND i.is_current=1
          AND EXISTS (SELECT 1 FROM screener_collection_scope sc
            WHERE sc.company_id=i.company_id AND sc.eligibility='eligible')''', (kind, parts[1])).fetchall()
    if len(matches) != 1:
        return ()
    return ensure_result_artifact_expectations(
        conn, company_id=int(matches[0]['company_id']), filing_id=int(filing['filing_id']),
        exchange=str(filing['exchange']), exchange_key=str(filing['exchange_key']),
        result_period=str(filing['result_period']), period_end=str(filing['period_end_date']),
        broadcast_at_utc=str(filing['broadcast_at_utc']), created_at_utc=now_utc)


def register_recent_filing_expectations(conn, *, now_utc, limit=100):
    """Repair the finite fresh lane, including a prior interrupted handoff.

    Run after the financial admission transaction: the expectation repository
    owns its own commits, which must not commit a half-finished queue decision.
    """
    from datetime import timedelta
    from ...documents.availability import format_utc
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Fresh filing handoff limit must be 1–100')
    cutoff = format_utc(parse_utc(now_utc) - timedelta(days=1))
    rows = execute(conn, '''SELECT f.*,d.decision,d.company_path_normalized,d.path_basis
        FROM exchange_result_filings f JOIN exchange_screener_enqueues d USING(filing_id)
        WHERE d.decision IN ('queued','reused') AND f.broadcast_at_utc>=? AND f.broadcast_at_utc<=?
          AND f.period_end_date<>'1970-01-01' AND d.path_basis IN ('nse_symbol','bse_scrip_code')
          AND (SELECT count(DISTINCT i.company_id) FROM security_identifiers i
            JOIN screener_collection_scope sc ON sc.company_id=i.company_id
            WHERE sc.eligibility='eligible' AND i.is_current=1
              AND i.identifier_type=CASE d.path_basis WHEN 'nse_symbol' THEN 'NSE' ELSE 'BSE' END
              AND d.company_path_normalized='/company/' || i.identifier_value || '/')=1
          AND (SELECT count(*) FROM artifact_expectations x JOIN artifact_events e USING(event_id)
            WHERE e.event_key='exchange-result-filing:' || CAST(f.filing_id AS TEXT)
              AND x.artifact_type IN ('presentation','transcript','recording','credit_rating'))<4
        ORDER BY f.broadcast_at_utc DESC,f.filing_id DESC LIMIT ?''', (cutoff, now_utc, limit)).fetchall()
    return [expectation for row in rows for expectation in
            register_filing_document_expectations(conn, row, dict(row), now_utc=now_utc)]


def acquisition_freshness_report(conn, *, since_utc, limit=1000):
    """Inspect explicit caller-selected stores only; no connection/initialization.

    Link delay uses publication evidence when present. Filing-relative link age
    would include the issuer's own publication delay, so is not mislabeled as
    discovery latency. Sample truncation is explicit and no SLA is certified.
    """
    parse_utc(since_utc)
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('Timing sample limit must be 1–10000')
    filings = execute(conn, '''SELECT broadcast_at_utc,discovered_at_utc
        FROM exchange_result_filings WHERE discovered_at_utc>=?
        ORDER BY discovered_at_utc DESC,filing_id DESC LIMIT ?''', (since_utc, limit + 1)).fetchall()
    links = execute(conn, '''SELECT published_at_utc,first_observed_at_utc
        FROM document_artifacts WHERE first_observed_at_utc>=?
        AND artifact_type IN ('presentation','transcript','credit_rating')
        ORDER BY first_observed_at_utc DESC,artifact_id DESC LIMIT ?''', (since_utc, limit + 1)).fetchall()
    attempts = execute(conn, '''SELECT elapsed_ms,byte_size,result_status
        FROM artifact_download_attempts WHERE started_at_utc>=?
        ORDER BY started_at_utc DESC,download_attempt_id DESC LIMIT ?''', (since_utc, limit + 1)).fetchall()
    backlog = execute(conn, '''SELECT status,count(*) AS count FROM artifact_download_queue
        WHERE status IN ('PENDING','RETRYABLE_FAILURE','LEASED') GROUP BY status''').fetchall()
    return {'since_utc': since_utc, 'sample_limit': limit,
            'truncated': {'filings': len(filings) > limit, 'links': len(links) > limit,
                          'downloads': len(attempts) > limit},
            'filing_detection': measured_delays(filings[:limit], earlier='broadcast_at_utc',
                                                later='discovered_at_utc', target_seconds=900),
            'link_discovery': measured_delays(links[:limit], earlier='published_at_utc', later='first_observed_at_utc'),
            'download_requests': download_timings(attempts[:limit]),
            'download_backlog': {row['status']: row['count'] for row in backlog},
            'filing_timestamp_basis': 'RETAINED_DISCOVERY_TIME; legacy transports may use poll admission time',
            'live_target_verified': False}
