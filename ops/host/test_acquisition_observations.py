"""Offline safety and semantics tests. No production command or write is authorized."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import Mock
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'ops/host'),str(ROOT/'ops/activation')]
from ops.host import acquisition_observations as module
from acquisition_queries import query, monitor_details


def test_quarterly_census_counts_companies_not_cells_or_accepted_versions():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT/'scrapper/db/migrations/001_initial.sql').read_text())
    # Two accepted versions for one company must not inflate coverage. An
    # unaccepted parse and an annual column must not count as quarterly data.
    for parse_id in (1, 2):
        conn.execute('''INSERT INTO accepted_page_versions
            (company_id,source,reporting_scope,source_period,parse_id,accepted_at_utc,accepted_by,acceptance_mode)
            VALUES(1,'screener','standalone',?,?,'now','test','test')''', (str(parse_id),parse_id))
    required = [r['name'] for r in conn.execute('PRAGMA table_info(financial_observations)')
                if r['notnull'] and r['dflt_value'] is None]
    for parse_id, period, kind in [(1,'2026-09-30','quarter'),(2,'2026-09-30','quarter'),
                                  (3,'2026-06-30','quarter'),(1,'2026-03-31','fiscal_year')]:
        for row in (1,2):
            values = dict.fromkeys(required, '')
            values.update(parse_id=parse_id, reporting_scope='standalone', section_code='quarters',
                          row_position=row, column_position=1 if kind=='quarter' else 2,
                          period_end=period, period_type=kind)
            conn.execute('INSERT INTO financial_observations ('+','.join(values)+') VALUES ('+
                         ','.join('?' for _ in values)+')', list(values.values()))
    result = monitor_details(conn)
    assert result['quarterly_coverage'] == [{'period_end':'2026-09-30','reporting_scope':'standalone','companies':1}]
    conn.close()


def test_queue_diagnostics_separate_unattempted_due_and_future_retries():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    for migration in ['001_initial.sql','031_screener_result_discovery.sql']:
        conn.executescript((ROOT/'scrapper/db/migrations'/migration).read_text())
    for i, status, attempt, next_at in [(1,'pending',0,None), (2,'failed_retryable',1,'2000-01-01'),
                                       (3,'failed_retryable',2,'2999-01-01')]:
        conn.execute('''INSERT INTO screener_company_queue
            (candidate_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,
             attempt_count,next_attempt_at_utc,created_at_utc,updated_at_utc,last_error_json)
            VALUES(? ,?,'standalone','2026-09','new_company',?,?,?,'now','now','{"reason":"http_403"}')''',
            (i,f'/company/{i}/',status,attempt,next_at))
    result = monitor_details(conn)['queue_diagnostics'][0]
    assert (result['unattempted'],result['retry_due'],result['retry_waiting']) == (1,1,1)
    assert result['reasons'][0]['n'] == 2
    conn.close()


def test_completion_catalog_retains_identifiers_without_duplicate_companies():
    conn = sqlite3.connect(':memory:')
    for migration in ['001_initial.sql', '002_source_identifiers.sql']:
        conn.executescript((ROOT/'scrapper/db/migrations'/migration).read_text())
    conn.execute("INSERT INTO companies VALUES(1,'TVS Motor Company','active')")
    conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value) VALUES(1,'bse_code','532343')")
    conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value) VALUES(1,'nse_symbol','TVSMOTOR')")
    rows = query(conn, 'company_catalog')['companies']
    assert len(rows) == 1 and rows[0]['company_id'] == 1
    assert '532343' in rows[0]['identifiers'] and 'TVSMOTOR' in rows[0]['identifiers']
    conn.close()

@pytest.fixture
def raw(monkeypatch,tmp_path):
    state=tmp_path/'receipts';state.mkdir()
    monkeypatch.setattr(module.lifecycle.dispatcher,'STATE',state)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    return {'observed_at_utc':'2026-10-22T14:00:00+00:00','status':'PASS','dispatcher_loaded':True,
      'admissions_paused':False,'last-tick.json':{'checked_at_utc':'2026-10-22T13:59:45+00:00'},
      'receipts':{'blocked_stages':{'BSE.stopped.json':{'error':'EMPTY_OR_REJECTED_ROWS','finished_at_utc':'2026-10-22T12:30:00+00:00'}}},
      'containers':[{'name':'legacy','state':'exited','classification':'legacy/manual; never restored'}],
      'active_bounded_stages':[], 'inspection_errors':[],
      'schedule':{'document_expires_at_utc':'2026-10-23T09:12:05+00:00','document_window_expired':False,
                  'jobs':[{'job':'financial','exchange':'FINANCIAL','next_at_ist':'2026-10-22T20:15:00+05:30'}]}}

def test_legacy_stopped_is_not_current_failure(raw):
    result=module.build(raw)
    assert result['acquisition']['state']=='scheduled'
    assert result['stages'][0]['state']=='blocked'
    assert result['stages'][1]['state']=='scheduled'
    assert result['database']['data'] is None
    assert not result['shutdown_readiness']['data']['safe_to_shut_down_mac']
    assert 'percent' not in json.dumps(result).lower()

@pytest.mark.parametrize('paused,active,expected',[(True,False,'paused'),(False,True,'running')])
def test_running_and_paused_are_explicit(raw,paused,active,expected):
    raw['admissions_paused']=paused
    if active:raw['active_bounded_stages']=[{'container':'trading-ai-step3-financial','state':'running'}]
    assert module.build(raw)['acquisition']['state']==expected


def test_missing_drive_and_expired_authorization(raw):
    raw['inspection_errors']=['storage: drive missing']
    raw['schedule']['document_window_expired']=True
    result=module.build(raw)
    assert result['drive']['state']=='blocked'
    assert result['authorization']['state']=='blocked'
    assert result['authorization']['data']['expires_at']=='2026-10-23T09:12:05+00:00'
    assert all(x['state']=='blocked' for x in result['stages'][2:])


def test_stale_heartbeat_cannot_claim_idle_or_running(raw):
    raw['last-tick.json']['checked_at_utc']='2026-10-22T10:00:00+00:00'
    assert module.build(raw)['acquisition']['state']=='unknown'


def test_all_statuses_have_source_and_timestamp(raw):
    result=module.build(raw)
    for item in [result[k] for k in ('acquisition','database','drive','authorization','shutdown_readiness','backup')]+result['stages']:
        assert item['source'] and 'updated_at' in item


def test_log_path_and_secret_redaction(raw,tmp_path):
    with pytest.raises(ValueError):module.logs('../../.env')
    secret='do-not-expose-this-session'
    (tmp_path/'.screener_session').write_text(secret)
    assert secret not in str(module.redact({'error':secret,'cookie':'a=b','url':'https://example.com/a?token=secret'}))
    assert module.redact({'cookie':'a=b'})['cookie']=='[REDACTED]'


def test_bounded_log_tail_and_no_mutation(raw,tmp_path):
    path=module.lifecycle.dispatcher.STATE/'launchd.err';path.write_text('entry\n'*30000)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=module.logs(lines=5)
    assert len(result['data'].splitlines())==5
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_db_search_real_records_bounded_and_literal(tmp_path):
    db=tmp_path/'fixture.db';conn=sqlite3.connect(db)
    conn.executescript((ROOT/'scrapper/db/migrations/001_initial.sql').read_text())
    conn.executescript((ROOT/'scrapper/db/migrations/002_source_identifiers.sql').read_text())
    conn.executemany('INSERT INTO companies VALUES(?,?,?)',[(i,f'Test Company {i}','active') for i in range(10000)])
    conn.commit();conn.close()
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    conn=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);conn.execute('PRAGMA query_only=ON')
    assert len(query(conn,'search',term='Test')['companies'])==41
    assert query(conn,'search',term="' OR 1=1 --")['companies']==[]
    assert query(conn,'search',term='1234')['companies'][0]['company_id']==1234
    conn.close()
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_near_slot_uses_live_snapshot_without_ownership_locks(raw,monkeypatch,tmp_path):
    monkeypatch.setattr(module.lifecycle.dispatcher,"STATE",tmp_path)
    service=module.Backend()
    monkeypatch.setattr(module.lifecycle,'schedule_status',lambda:{'next_scheduled_batch':{'next_at_ist':(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()}})
    run=Mock(return_value={'companies':[{'company_id':1}]})
    monkeypatch.setattr(module.lifecycle,'database_status',run)
    result=service.company('search',term='x')
    assert result['state']=='completed'
    assert result['data']['companies'][0]['company_id']==1
    assert json.loads(run.call_args.args[1][1])['live'] is True


def test_shared_cache_runs_one_read_only_inspection(raw,monkeypatch):
    run=Mock(return_value=raw);monkeypatch.setattr(module.lifecycle,'status',run)
    service=module.Backend()
    assert service.status()==service.status()
    assert run.call_count==1


def test_historical_receipts_unchanged(raw,tmp_path):
    folder=module.lifecycle.dispatcher.STATE/'batch';folder.mkdir()
    (folder/'admission.json').write_text(json.dumps({'job':'financial','exchange':'FINANCIAL','started_at_utc':'2026-10-22T13:00:00+00:00'}))
    (folder/'outcome.json').write_text(json.dumps({'status':'PASS','finished_at_utc':'2026-10-22T13:01:00+00:00'}))
    before={p:p.read_bytes() for p in folder.iterdir()}
    result=module.history()
    assert result['items'][0]['duration_seconds']==60
    assert all(p.read_bytes()==content for p,content in before.items())


def test_company_details_follow_current_schema_and_preserve_unknown_absence(tmp_path):
    conn=sqlite3.connect(':memory:')
    migrations=ROOT/'scrapper/db/migrations'
    # The initial schema plus actual acceptance migration captures the renamed
    # resolved_source_period column; the test must not reproduce an obsolete schema.
    conn.executescript((migrations/'001_initial.sql').read_text())
    conn.executescript((migrations/'003_availability_acceptance_periods.sql').read_text())
    conn.executescript((migrations/'007_document_links.sql').read_text())
    conn.executescript((migrations/'023_document_artifact_discovery.sql').read_text())
    conn.executescript((migrations/'031_screener_result_discovery.sql').read_text())
    conn.executescript((migrations/'032_exchange_universe.sql').read_text())
    conn.executescript((migrations/'030_artifact_reconciliation.sql').read_text())
    conn.execute("INSERT INTO companies VALUES(3,'Fixture company','active')")
    conn.execute('PRAGMA query_only=ON')
    result=query(conn,'company',company_id=3)
    assert result['company']['current_legal_name']=='Fixture company'
    assert result['periods']==[] and result['documents']==[]
    assert 'not discovered / unknown' in result['absence_meaning']
    conn.execute('PRAGMA query_only=OFF')
    conn.executescript((migrations/'002_source_identifiers.sql').read_text())
    conn.execute('PRAGMA query_only=ON')
    report=query(conn,'company_report',term='Fixture company')
    assert report['company']['company_id']==3
    assert 'financials' in report and 'financials_error' not in report


def test_inspection_failure_returns_unknown_instead_of_http_error(raw,monkeypatch):
    monkeypatch.setattr(module.lifecycle,'status',Mock(side_effect=ValueError('partial receipt write')))
    result=module.Backend().status()
    assert result['acquisition']['state']=='unknown'
    assert result['status']=='DEGRADED'
    assert result['database']['data'] is None
    assert result['inspection_errors']


def test_document_stage_uses_mounted_plan_evidence(raw):
    raw['active_bounded_stages']=[{'container':'trading-ai-phase4-routine','service':'document-once','receipt_stage':'DOCUMENT_PROCESSING'}]
    result=module.build(raw)
    assert result['stages'][4]['state']=='running'
    assert result['stages'][3]['state']!='running'


def test_market_cap_adapter_is_not_financial_statement_acquisition(raw):
    raw['active_bounded_stages']=[{'container':'trading-ai-step3-cap','service':'current-financial-once'}]
    result=module.build(raw)
    assert result['acquisition']['state']=='running'
    assert result['stages'][1]['state']!='running'


def test_guarded_company_reader_sets_named_rows_before_lease_check(tmp_path,monkeypatch):
    import acquisition_queries as reader
    import production_ownership
    db=tmp_path/'fixture.db'
    conn=sqlite3.connect(db)
    conn.executescript((ROOT/'scrapper/db/migrations/001_initial.sql').read_text())
    conn.executescript((ROOT/'scrapper/db/migrations/002_source_identifiers.sql').read_text())
    conn.execute("INSERT INTO companies VALUES(3,'Fixture Ltd','active')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(reader,'assert_safe_empty_wal',lambda p:{'files':{}})
    monkeypatch.setattr(reader,'snapshot',lambda p:{})
    def lease_check(conn,**kwargs):
        assert conn.execute('SELECT * FROM companies').fetchone()['company_id']==3
        return []
    monkeypatch.setattr(production_ownership,'assert_no_conflicting_leases',lease_check)
    assert reader.inspect(db,'search',term='Fixture')['companies'][0]['company_id']==3


def test_held_host_owner_without_adapter_cannot_claim_scheduled_idle(raw):
    raw['inspection_errors']=['ownership/queue census unavailable: Active owner has not drained: .scheduler-admission.lock']
    assert module.build(raw)['acquisition']['state']=='unknown'


def test_search_matches_care_ticker_and_normalized_name_without_duplicates():
    conn=sqlite3.connect(':memory:')
    conn.executescript((ROOT/'scrapper/db/migrations/001_initial.sql').read_text())
    conn.executescript((ROOT/'scrapper/db/migrations/002_source_identifiers.sql').read_text())
    conn.execute("INSERT INTO companies VALUES(4259,'CARE Ratings Ltd','active')")
    conn.execute("INSERT INTO companies VALUES(1,'Healthcare Example Ltd','active')")
    conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value) VALUES(4259,'nse_symbol','CARERATING')")
    conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value) VALUES(4259,'isin','INE752H01013')")
    conn.execute("INSERT INTO source_identifiers(company_id,source,source_company_id,source_symbol,source_slug,canonical_url,mapping_status,verified_at_utc) VALUES(4259,'screener','123','CARERATING','CARERATING','https://www.screener.in/company/CARERATING/','verified','2026-10-22')")
    conn.execute('PRAGMA query_only=ON')
    for term in ('CARERATING','carerating','care rating','CARE Ratings','INE752H01013','4259','  CARERATING  '):
        result=query(conn,'search',term=term)['companies']
        assert len(result)==1,term
        assert result[0]['company_id']==4259 and result[0]['symbol']=='CARERATING'
    assert query(conn,'search',term="' OR 1=1 --")['companies']==[]
    assert len(query(conn,'search',term='')['companies'])==2
