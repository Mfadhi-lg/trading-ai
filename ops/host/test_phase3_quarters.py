from pathlib import Path
import sqlite3
import sys
import time
import pytest
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1]/'activation')]
from reporting_quarters import label,default_quarter,predicate,normalize


def test_indian_fiscal_year_and_non_march():
    assert label('2026-06')=='FY 2026-27 Q1 — Jun 2026'
    assert label('2026-09')=='FY 2026-27 Q2 — Sep 2026'
    assert label('2026-12')=='FY 2026-27 Q3 — Dec 2026'
    assert label('2027-03')=='FY 2026-27 Q4 — Mar 2027'
    assert 'Q3' in label('2026-09',fiscal_year_end_month=12)
    assert 'Q1' in label('2026-09',fiscal_year=2027,fiscal_quarter=1)
    from datetime import date
    assert default_quarter(date(2026,10,30))=='2026-09'
    with pytest.raises(ValueError): normalize('1970-01')


def test_period_filter_never_uses_publication_or_download_date():
    c=sqlite3.connect(':memory:')
    c.execute('CREATE TABLE a(period_end TEXT, published TEXT, downloaded TEXT)')
    c.executemany('INSERT INTO a VALUES (?,?,?)',[
        ('2026-09-30','2026-10-15','2026-12-01'),('2026-06-30','2026-10-15','2026-12-01'),(None,'2026-10-15','2026-12-01')])
    for quarter,n in [('2026-09',1),('2026-06',1),('unknown',1),('all',3),('2025-12',0)]:
        sql,args=predicate('period_end',quarter)
        assert c.execute('SELECT count(*) FROM a WHERE '+sql,args).fetchone()[0]==n


def test_storage_host_changes_duplicates_restarts_disconnect(tmp_path,monkeypatch):
    from datetime import datetime,timezone,timedelta
    from scraper_storage_growth import account
    root=tmp_path/'store';root.mkdir(); ledger=tmp_path/'ledger.json'
    config={'activated_at_utc':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),'external_growth_limit_bytes':100}
    capacity={'root':str(root),'volume_uuid':'test','physical_identity_sha256':'sentinel','free_bytes':10000,'required_free_bytes':100}
    assert account(root,ledger,config,capacity,bootstrap=True)['charged_bytes']==0
    # Unrelated APFS activity consumes free space, but no scraper bytes.
    capacity['free_bytes']=5000
    assert account(root,ledger,config,capacity)['charged_bytes']==0
    (root/'content').write_bytes(b'x'*20)
    assert account(root,ledger,config,capacity)['charged_bytes']==20
    assert account(root,ledger,config,capacity)['charged_bytes']==20 # duplicate/restart
    (root/'content').unlink()
    assert account(root,ledger,config,capacity)['charged_bytes']==20 # no deletion refunds
    (root/'content').write_bytes(b'x'*30)
    assert account(root,ledger,config,capacity)['charged_bytes']==30
    with pytest.raises(RuntimeError,match='identity'):
        account(root,ledger,config,{**capacity,'volume_uuid':'other'})
    with pytest.raises(RuntimeError,match='Reserve'):
        account(root,ledger,config,{**capacity,'free_bytes':10},reserve_bytes=20)
    with pytest.raises(RuntimeError,match='missing'):
        account(root,tmp_path/'absent.json',config,capacity)
    (root/'large').write_bytes(b'x'*90)
    with pytest.raises(RuntimeError,match='exhausted'):
        account(root,ledger,config,capacity)
    import json
    assert json.loads(ledger.read_text())['charged_bytes']==120 # committed even on stop


def test_quarter_render_never_substitutes_global_counts():
    from acquisition_quarter_view import render
    from rich.console import Console
    from io import StringIO
    stream=StringIO(); Console(file=stream,width=150).print(render({'tick':{},'paused':False},
        {'quarter':'2026-09','database':{'queues':{'global':[{'n':147417}]}},'quarter_error':'active owner'}))
    output=stream.getvalue()
    assert 'FY 2026-27 Q2' in output and '147417' not in output
    assert 'Refresh deferred' in output and 'lifetime counts substituted' in output


def test_quarter_database_aggregation_and_realistic_backlog(tmp_path):
    from scrapper.db.database import connect,init_db
    from acquisition_quarter_queries import monitor
    db=tmp_path/'test.db';c=sqlite3.connect(db);c.row_factory=sqlite3.Row;init_db(c,apply_migrations=True)
    # Raw fixture uses the actual migrated schema; FK enforcement is disabled
    # only in this isolated test to avoid unrelated publication fixtures.
    c.execute('PRAGMA foreign_keys=OFF')
    c.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (1,'Current'),(2,'Historical')")
    c.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,market_cap_crore,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES ('/company/CURRENT/',1,'eligible','1000','500','2026-10-30','{}','2026-10-30')")
    for i,period in [(1,'2026-09'),(2,'2026-06')]:
        c.execute("INSERT INTO screener_company_collection_state VALUES (?,?,?,?,?,?,?)",(f'/company/{i}/','standalone',i,period,None,None,'2026-10-30'))
        c.execute("INSERT INTO screener_company_queue (queue_item_id,origin,exchange_filing_id,company_path_normalized,reporting_scope,result_period,queue_reason,status,created_at_utc,updated_at_utc) VALUES (?,'exchange_filing',1,?,'standalone',?,'new_company','pending','2026-10-30','2026-10-30')",(i,f'/company/{i}/',period))
    for i,period in [(1,'2026-09-30'),(2,'2026-06-30'),(3,None)]:
        c.execute("INSERT INTO document_artifacts (artifact_id,company_id,artifact_key,artifact_type,title_raw,period_end,published_at_utc,first_observed_at_utc) VALUES (?,1,?,'presentation','Report',?,'2026-10-30','2026-10-30')",(i,str(i),period))
    c.execute("UPDATE document_artifacts SET artifact_type='credit_rating' WHERE artifact_id=3")
    c.execute("INSERT INTO credit_rating_download_queue(artifact_id,requested_url,status,created_at_utc,updated_at_utc) VALUES(3,'https://example.org/rating','INTERACTION_REQUIRED','2026-10-30','2026-10-30')")
    c.execute("INSERT INTO artifact_download_scopes VALUES ('test','custom','test','[]','{}','2026-10-30','2026-10-30')")
    # 147k historical queue rows stay in SQLite, not Python. Distinct scopes
    # may refer to the same artifact; document counts must not multiply.
    c.executemany("INSERT INTO artifact_download_queue(artifact_id,requested_url,target_host,scope_id,status,created_at_utc,updated_at_utc) VALUES (2,?,'example.org','test','PENDING','2026-10-30','2026-10-30')",((f'https://example.org/{i}',) for i in range(147000)))
    c.execute("INSERT INTO artifact_download_queue(artifact_id,requested_url,target_host,scope_id,status,created_at_utc,updated_at_utc) VALUES (1,'https://example.org/current','example.org','test','SUCCEEDED','2026-10-30','2026-10-30')")
    # A validated download is stored before processing assigns a preferred version.
    c.execute("""INSERT INTO artifact_contents(content_id,artifact_id,blob_id,media_type,content_sha256,
        byte_size,is_official,content_origin,created_at_utc)
        VALUES(1,1,1,'application/pdf',?,12,1,'publisher_original','2026-10-30T00:00:00+00:00')""",('a'*64,))
    from scrapper.db.documents.expectations import ensure_result_artifact_expectations
    expected=ensure_result_artifact_expectations(c,company_id=1,filing_id=1,exchange='NSE',exchange_key='CURRENT',
        result_period='2026-09',period_end='2026-09-30',broadcast_at_utc='2026-10-30T00:00:00+00:00',
        created_at_utc='2026-10-30T00:00:00+00:00')
    c.commit();c.execute('PRAGMA query_only=ON')
    before=c.total_changes
    current=monitor(c,'2026-09');old=monitor(c,'2026-06');unknown=monitor(c,'unknown');all_=monitor(c,'all')
    assert current['queues']['artifact_download_queue']==[{'state':'SUCCEEDED','n':1}]
    assert old['queues']['artifact_download_queue']==[{'state':'PENDING','n':147000}]
    assert len(unknown['downloads'])==1 and len(all_['downloads'])==3
    assert unknown['downloads'][0]['status']=='INTERACTION_REQUIRED'
    assert current['coverage']['with_documents']==1
    assert current['coverage']['with_stored_documents']==1
    assert current['acquisition']['documents_already_stored']==1
    assert current['downloads'][0]['stored_content_id']==1
    assert current['acquisition']['documents_awaiting_publication']==len(expected)
    assert current['acquisition']['document_links_discovered']==1
    assert old['acquisition']['document_links_discovered']==1
    assert current['coverage']['admitted']==1 and all_['coverage']['admitted']==2
    assert current['query_seconds']<3 and c.total_changes==before
    c.close()


def test_focus_membership_is_quarter_specific_and_identity_resolved(tmp_path):
    import json
    from reporting_focus import membership
    p=tmp_path/'registry.json';p.write_text(json.dumps({'rows':[
        {'quarter':'FY2027-Q2','label':1,'company_id':7,'identity_resolution_state':'RESOLVED'},
        {'quarter':'FY2026-Q4','label':1,'company_id':8,'identity_resolution_state':'RESOLVED'},
        {'quarter':'FY2027-Q2','label':1,'company_id':9,'identity_resolution_state':'UNRESOLVED'}]}))
    assert membership(p,'2026-09')['company_ids']==[7]
    assert membership(p,'2026-06')['company_ids']==[]


def test_selector_switch_discards_stale_quarter_counts(monkeypatch):
    import acquisition_terminal as t
    inspector=t.Inspector(quarter='2026-09')
    def load(*a,**kw):
        inspector.quarter='2026-06' # User changed quarters during the query.
        return {'data':{'quarter':'2026-09'},'updated_at':'2026-10-30'}
    monkeypatch.setattr(t.observations.backend,'company',load)
    inspector.inspect()
    assert inspector.quarter_data is None and inspector.snapshot()['quarter']=='2026-06'


def test_focus_orders_before_current_eligible_without_history_override():
    from scrapper.tests.test_phase4_automatic_processing import AutomaticTests
    case=AutomaticTests();case.setUp()
    try:
        current=case.arrival('ordinary.pdf',body=b'ordinary')
        focus=case.arrival('focus.pdf',body=b'focus')
        with case.conn:
            case.conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (999,'Focus')")
            case.conn.execute('UPDATE document_artifacts SET company_id=999 WHERE artifact_id=(SELECT artifact_id FROM artifact_contents WHERE content_id=?)',(focus,))
            case.conn.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES ('/company/FOCUS/',999,'eligible','580','2026-10-11','{}','2026-10-11')")
        assert case.select(limit=1,focus_company_ids=(999,))[0][0]['content_id']==focus
        assert case.select(limit=1)[0][0]['content_id']==current
    finally:
        case.fixture.tearDown()


def test_browser_closed_is_environment_only_and_unknown_stop_preserved():
    from acquisition_failures import classify_exception
    assert classify_exception(RuntimeError('Target page, context or browser has been closed'))==('DEFERRED_ENVIRONMENT',75)
    assert classify_exception(RuntimeError('unrecognized integrity problem'))==('STOP',2)


def test_quarter_query_arguments_are_read_only_display_state(monkeypatch):
    import acquisition_terminal as terminal
    calls=[]
    monkeypatch.setattr(terminal,'run_monitor',lambda *a,**kw:calls.append(kw) or 0)
    assert terminal.main(['monitor','--quarter','2026-09','--once'])==0
    assert terminal.main(['monitor','--all-quarters','--once'])==0
    assert [r['quarter'] for r in calls]==['2026-09','all']
