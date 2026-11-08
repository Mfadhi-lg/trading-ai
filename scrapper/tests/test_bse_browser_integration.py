"""Focused retained-browser/canonical-ingestion integration; no live access."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scrapper.sources.exchange.browser_results import (
    RetainedBSEBrowserResults, preview_retained_bse_results,
)
from scrapper.core.http import FetchResponse
from scrapper.tests.database_fixtures import prepare_database
from scrapper.tests.test_e01_exchange_universe import BSE_MASTER, registry_thresholds
from scrapper.workflows.exchange.universe import refresh_exchange_universe, discover_exchange_results
from scrapper.db.database import connect
from scrapper.db.exchange_repo import record_source_access_event

URL = 'https://api.bseindia.com/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w?SCRIP_CD=&FlagDur=1&HFQ=&ISUBGROUP_CODE=&segment=C'
CAPTURE = '2026-10-09T04:29:57.897538+00:00'


@pytest.fixture
def evidence(tmp_path):
    raw = json.dumps({'Table':[{'Scrip_cd':500001, 'company_name':'Alpha Industries Limited',
        'quarter_code':'SQ2026-2027', 'Fld_CreateDate':'2026-10-08T22:05:10.33',
        'XMLName':'standalone.xml', 'Consol_XMLName':'consolidated.xml'}]}).encode()
    response = FetchResponse(URL, URL, 200, raw, 'application/json', None, None, None,
                             'public_browser', 'ok')
    metadata = {k:v for k,v in asdict(response).items() if k != 'content'}
    receipt = {'response':metadata, 'day_ist':'2026-10-09', 'slot_ist':'morning',
        'status':'PARSED_PARTIAL_SURFACE', 'result_period':'2026-09', 'accepted_row_count':2}
    verification = {'response':metadata, 'raw_sha256':hashlib.sha256(raw).hexdigest(),
        'raw_bytes':len(raw), 'started_at_utc':'2026-10-09T04:29:53+00:00',
        'finished_at_utc':CAPTURE, 'result_period':'2026-09',
        'browser_result':'PARSED_PARTIAL_SURFACE'}
    paths = {name:tmp_path/name for name in ('receipt.json','raw.json','verification.json')}
    paths['receipt.json'].write_text(json.dumps(receipt))
    paths['raw.json'].write_bytes(raw)
    paths['verification.json'].write_text(json.dumps(verification))
    arguments = dict(receipt_path=paths['receipt.json'], raw_path=paths['raw.json'],
                     verification_path=paths['verification.json'])
    return arguments


def test_retained_transport_keeps_original_query_and_bytes(evidence):
    source = RetainedBSEBrowserResults.load(**evidence)
    assert source(URL, 'BSE').content == evidence['raw_path'].read_bytes()
    assert source.response.auth_mode == 'public_browser'
    assert source.captured_at_utc == CAPTURE
    with pytest.raises(ValueError):
        source(URL.replace('FlagDur=1','FlagDur=5'), 'BSE')
    with pytest.raises(ValueError):
        source(URL, 'NSE')


@pytest.mark.parametrize('case',['hash','metadata','slot','timestamp','period'])
def test_corrupt_or_mismatched_evidence_fails_before_database(evidence,case):
    if case == 'hash':
        evidence['raw_path'].write_bytes(b'changed')
    elif case in ('metadata','slot'):
        r=json.loads(evidence['receipt_path'].read_text())
        if case == 'metadata':
            r['response']['auth_mode']='public'
        else:
            r['slot_ist']='evening'
        evidence['receipt_path'].write_text(json.dumps(r))
    else:
        r=json.loads(evidence['verification_path'].read_text())
        r['finished_at_utc']='2026-10-07T00:00:00+00:00' if case=='timestamp' else r['finished_at_utc']
        if case == 'period':
            r['result_period']='2026-06'
        evidence['verification_path'].write_text(json.dumps(r))
    with patch('sqlite3.connect', side_effect=AssertionError('DB must not be opened')):
        with pytest.raises(ValueError):
            RetainedBSEBrowserResults.load(**evidence)


def test_canonical_preview_and_ingestion_preserve_holds_and_deduplicate(evidence,tmp_path):
    source = RetainedBSEBrowserResults.load(**evidence)
    db=tmp_path/'db.sqlite'
    prepare_database(db)
    master=tmp_path/'master.csv'
    master.write_bytes(BSE_MASTER)
    refresh_exchange_universe(dataset='bse_security_master', file=master,
        effective_date='2026-10-08', now_utc=lambda:CAPTURE, db_path=db,
        snapshot_root=tmp_path/'snapshots', thresholds=registry_thresholds('bse_security_master'))
    conn=connect(db)
    record_source_access_event(conn,dataset='bse_financial_results',action='BLOCKED',
        event_at_utc='2026-10-08T00:00:00+00:00',evidence='retained API 403')
    holds=[dict(r) for r in conn.execute('SELECT * FROM exchange_source_access_events')]
    before=conn.total_changes
    conn.close()
    original_hash=hashlib.sha256(db.read_bytes()).hexdigest()
    preview=preview_retained_bse_results(source,db_path=db)
    assert preview['validation']['status']=='passed'
    assert preview['enqueue']['queued']==1 and preview['enqueue']['reused']==1
    assert hashlib.sha256(db.read_bytes()).hexdigest()==original_hash
    options=dict(dataset='bse_financial_results',url=URL,result_period='2026-09',
        effective_date='2026-10-09',now_utc=lambda:CAPTURE,fetcher=source,
        db_path=db,snapshot_root=tmp_path/'snapshots')
    first=discover_exchange_results(**options)
    assert first['filings']['stored']==2
    assert first['enqueue']['queued']==1 and first['enqueue']['reused']==1
    second=discover_exchange_results(**options)
    assert second['filings']['stored']==0 and second['filings']['already_stored']==2
    assert second['enqueue']['queued']==0
    conn=connect(db,read_only=True)
    assert [dict(r) for r in conn.execute('SELECT * FROM exchange_source_access_events')]==holds
    assert conn.execute('SELECT COUNT(*) FROM screener_company_queue').fetchone()[0]==1
    assert conn.execute('SELECT COUNT(*) FROM financial_observations').fetchone()[0]==0
    row=conn.execute('SELECT auth_mode, requested_url, final_url FROM fetch_observations WHERE source=\'BSE\' AND auth_mode=\'public_browser\' LIMIT 1').fetchone()
    assert tuple(row)==('public_browser',URL,URL)
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    conn.close()
