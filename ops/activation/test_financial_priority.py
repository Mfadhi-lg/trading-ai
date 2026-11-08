"""Real discovery/queue fixtures for fair scheduling and atomic claim fencing."""
from datetime import datetime
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parent))
from financial_priority import select,historical_slot,unaccepted_refresh,skip_accepted
from scrapper.tests.test_e01_exchange_universe import ExchangeCase,NSE_CURRENT,BSE_MASTER,nse_results
from scrapper.db.database import connect
from scrapper.db.screener_discovery_repo import claim_due_companies,fail_company,mark_running

@pytest.fixture
def queue():
    f=ExchangeCase();f.setUp();f.build_universe()
    line='ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-SEP-2026,Un-Audited,Non-cumulative,Consolidated,05-Oct-2026 18:30:00'
    f.discover('nse_financial_results',nse_results((line,line.replace('30-SEP-2026','30-JUN-2026'),line.replace('30-SEP-2026','31-MAR-2026'))),'2026-10-06',result_period='2026-09')
    c=connect(f.db)
    with c:
        c.execute("UPDATE screener_company_queue SET created_at_utc='2026-10-10T00:00:00+00:00',next_attempt_at_utc=NULL")
        c.execute("UPDATE exchange_result_filings SET discovered_at_utc='2026-10-10T00:00:00+00:00'")
        c.execute("UPDATE exchange_result_filings SET discovered_at_utc='2026-08-01T00:00:00+00:00' WHERE result_period='2026-03'")
    yield c
    c.close();f.tearDown()

NOW='2026-10-11T00:00:00+00:00'
def pick(c,h=False,now=NOW):return select(c,now_utc=now,activated_at='2026-10-09T00:00:00+00:00',historical_slot=h)

def test_live_and_late_progress_despite_history(queue):
    rows,lanes,period=pick(queue)
    assert period=='2026-09' and lanes==[1,2]
    assert [r.result_period for r in rows]==['2026-09','2026-06']
    rows,lanes,_=pick(queue,True)
    assert lanes==[1,3] and rows[1].result_period=='2026-03'

def test_history_cannot_borrow_live_slots_and_budget_is_four():
    slots=[(hour,minute) for hour in range(24) for minute in (15,45) if historical_slot(datetime.fromisoformat(f'2026-10-11T{hour:02d}:{minute}:00+05:30'))]
    assert slots==[(0,45),(6,45),(12,45),(18,45)]

def test_atomic_selected_claim_and_duplicate_owner_refusal(queue):
    rows,_,_=pick(queue);ids=tuple(r.queue_item_id for r in rows)
    claims=claim_due_companies(queue,now_utc=NOW,limit=2,worker_id='first',queue_item_ids=ids)
    assert tuple(r.queue_item_id for r in claims)==ids
    with pytest.raises(RuntimeError):claim_due_companies(queue,now_utc=NOW,limit=2,worker_id='second',queue_item_ids=ids)
    assert queue.execute("SELECT count(*) FROM screener_company_queue_events WHERE to_status='claimed'").fetchone()[0]==2


def test_just_refreshed_revision_is_due_to_worker_in_same_second(queue):
    from unittest.mock import patch
    from step3_financial_runtime import worker_now, selected_claim
    refreshed_at='2026-10-11T00:00:00.123456+00:00'
    with queue:
        queue.execute('UPDATE screener_company_queue SET next_attempt_at_utc=?',(refreshed_at,))
    rows,_,period=pick(queue,now=refreshed_at)
    plan={'queue_item_ids':[r.queue_item_id for r in rows], 'result_period':period,
          'priority_policy':'three-priority-v1'}
    with patch('step3_financial_runtime.datetime') as clock, patch(
            'scrapper.sources.exchange.reporting_periods.priority_result_periods',return_value=[period]):
        clock.now.return_value=datetime.fromisoformat(refreshed_at)
        claims=selected_claim(queue,plan=plan,now_utc=worker_now(),limit=len(rows),worker_id='same-second')
    assert [r.queue_item_id for r in claims]==plan['queue_item_ids']

def test_expired_claim_recovery_and_retry_backoff(queue):
    rows,_,_=pick(queue);qid=rows[0].queue_item_id
    first=claim_due_companies(queue,now_utc=NOW,limit=1,worker_id='first',lease_seconds=1,queue_item_ids=(qid,))[0]
    recovered=claim_due_companies(queue,now_utc='2026-10-11T00:00:02+00:00',limit=1,worker_id='second',queue_item_ids=(qid,))[0]
    assert recovered.claim_token!=first.claim_token
    mark_running(queue,recovered,worker_id='second',now_utc=NOW)
    fail_company(queue,recovered,worker_id='second',now_utc=NOW,error={'type':'Timeout','message':'retry'},retryable=True)
    assert qid not in [r.queue_item_id for r in pick(queue)[0]]
    assert qid in [r.queue_item_id for r in pick(queue,now='2026-10-11T02:00:00+00:00')[0]]

def test_rollover_demotes_previous_quarter_and_future_is_excluded(queue):
    assert pick(queue,now='2026-12-31T18:31:00+00:00')[2]=='2026-12'
    with queue:queue.execute("UPDATE screener_company_queue SET result_period='2027-03' WHERE result_period='2026-09'")
    assert all(r.result_period!='2027-03' for r in pick(queue)[0])

def test_revision_remains_processable_after_failure(queue):
    rows,_,_=pick(queue);item=rows[1]
    with queue:
        queue.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status,evidence_json) VALUES(?,?,'pending',?)",(item.queue_item_id,NOW,'{"reason":"source_filing_refresh","filing_id":'+str(item.exchange_filing_id)+'}'))
    assert unaccepted_refresh(queue,item.queue_item_id,item.exchange_filing_id)
    claim=claim_due_companies(queue,now_utc=NOW,limit=1,worker_id='r',queue_item_ids=(item.queue_item_id,))[0]
    mark_running(queue,claim,worker_id='r',now_utc=NOW)
    fail_company(queue,claim,worker_id='r',now_utc=NOW,error={'type':'Timeout'},retryable=True)
    assert unaccepted_refresh(queue,item.queue_item_id,item.exchange_filing_id)
    assert skip_accepted(queue,claim,worker_id='r',now_utc=NOW) is False

@pytest.mark.parametrize("decision_kind",["manual_review","already_accepted"])
@pytest.mark.parametrize("prior_status",["accepted","pending","failed_retryable"])
def test_source_revision_reuses_identity_and_preserves_accepted_pointer(queue,decision_kind,prior_status):
    from financial_priority import refresh_revisions
    from scrapper.db.exchange.filings import _decide
    # Accepted fixture provenance; acceptance behavior itself has retained repo tests.
    with queue:
        queue.execute("INSERT INTO companies(company_id,current_legal_name) VALUES(1,'Alpha')")
        queue.execute("INSERT INTO response_blobs(content_sha256,byte_size,local_path,first_observed_at_utc,created_at_utc) VALUES('fixture',1,'fixture','2026-10-06T00:00:00+00:00','2026-10-06T00:00:00+00:00')")
        bid=queue.execute('SELECT max(blob_id) FROM response_blobs').fetchone()[0]
        queue.execute("INSERT INTO parse_artifacts(blob_id,parser_name,parser_version,manifest_version,parsed_at_utc,parse_status,validation_status) VALUES(?,'fixture','1','1',?,'ok','passed')",(bid,NOW))
        pid=queue.execute('SELECT max(parse_id) FROM parse_artifacts').fetchone()[0]
        queue.execute("INSERT INTO accepted_page_versions VALUES(1,'screener','standalone','2026-06',?,'2026-10-06T00:00:00+00:00','system','automatic')",(pid,))
        queue.execute("UPDATE screener_company_collection_state SET company_id=1,latest_accepted_result_period='2026-06' WHERE company_path_normalized='/company/ALPHA/'")
        row=queue.execute("SELECT * FROM screener_company_queue WHERE result_period='2026-06'").fetchone()
        queue.execute("UPDATE screener_company_queue SET status=?,accepted_parse_id=?,next_attempt_at_utc=? WHERE queue_item_id=?",(prior_status,pid,'2026-10-12T00:00:00+00:00' if prior_status=='failed_retryable' else None,row['queue_item_id']))
        original=queue.execute('SELECT * FROM exchange_result_filings WHERE filing_id=?',(row['exchange_filing_id'],)).fetchone()
        cols=[key for key in original.keys() if key!='filing_id'];values=[original[key] for key in cols]
        values[cols.index('filing_key')]='revision-fixture';values[cols.index('broadcast_at_utc')]='2026-10-07T13:00:00+00:00';values[cols.index('discovered_at_utc')]='2026-10-10T00:00:00+00:00'
        queue.execute('INSERT INTO exchange_result_filings('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',values)
        filing=queue.execute('SELECT * FROM exchange_result_filings ORDER BY filing_id DESC LIMIT 1').fetchone()
        decision=_decide(queue,filing,now_utc=NOW)
        assert decision['decision']=='manual_review'
        queue.execute("INSERT INTO exchange_screener_enqueues(filing_id,decision,decision_reason,company_path_normalized,reporting_scope,result_period,path_basis,created_at_utc) VALUES(?,?,?,'/company/ALPHA/','standalone','2026-06','nse_symbol',?)",(filing['filing_id'],decision_kind,decision['reason'],NOW))
    before=[tuple(r) for r in queue.execute('SELECT * FROM accepted_page_versions')]
    assert refresh_revisions(queue,now_utc=NOW,activated_at='2026-10-09T00:00:00+00:00')==[row['queue_item_id']]
    assert refresh_revisions(queue,now_utc=NOW,activated_at='2026-10-09T00:00:00+00:00')==[]
    assert [tuple(r) for r in queue.execute('SELECT * FROM accepted_page_versions')]==before
    changed=queue.execute('SELECT * FROM screener_company_queue WHERE queue_item_id=?',(row['queue_item_id'],)).fetchone()
    assert changed['exchange_filing_id']==filing['filing_id'] and changed['accepted_parse_id']==pid
    if prior_status=='failed_retryable':
        assert changed['status']=='failed_retryable' and changed['next_attempt_at_utc']=='2026-10-12T00:00:00+00:00'
    assert unaccepted_refresh(queue,row['queue_item_id'],filing['filing_id'])

def test_new_official_filing_reuses_screen_identity_atomically(queue,tmp_path):
    from scrapper.pipeline.screener_discovery import discover_screener_results
    from scrapper.tests.test_screener_discovery import screen_html
    fixture=tmp_path/'screen.html';fixture.write_bytes(screen_html())
    db=Path(queue.execute('PRAGMA database_list').fetchone()[2])
    discover_screener_results(query_role='old_screen',query='x',fixture=fixture,
        db_path=db,snapshot_root=tmp_path/'snapshots')
    candidate=queue.execute("SELECT candidate_id FROM screener_result_candidates WHERE company_path_normalized='/company/ALPHA/consolidated/' LIMIT 1").fetchone()[0]
    old=queue.execute("SELECT * FROM screener_company_queue WHERE company_path_normalized='/company/ALPHA/' AND result_period='2026-06'").fetchone()
    count=queue.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]
    with queue:
        queue.execute("UPDATE screener_company_queue SET origin='screener_screen',candidate_id=?,exchange_filing_id=NULL,created_at_utc='2026-08-01T00:00:00+00:00' WHERE queue_item_id=?",(candidate,old['queue_item_id']))
    rows,lanes,_=pick(queue)
    assert old['queue_item_id'] in [r.queue_item_id for r in rows] and lanes==[1,2]
    claimed=claim_due_companies(queue,now_utc=NOW,limit=1,worker_id='attach',queue_item_ids=(old['queue_item_id'],))[0]
    assert claimed.origin=='exchange_filing' and claimed.candidate_id is None
    assert claimed.exchange_filing_id==old['exchange_filing_id']
    assert queue.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==count
    import json
    event=queue.execute("SELECT evidence_json FROM screener_company_queue_events WHERE queue_item_id=? AND json_extract(evidence_json,'$.reason')='official_source_attached'",(old['queue_item_id'],)).fetchone()
    assert json.loads(event[0])['previous_candidate_id']==candidate
    assert queue.execute('SELECT 1 FROM screener_result_candidates WHERE candidate_id=?',(candidate,)).fetchone()


def test_five_company_batch_keeps_late_capacity_and_history_fence(queue):
    original=dict(queue.execute("SELECT * FROM screener_company_queue WHERE result_period='2026-09'").fetchone())
    keys=list(original)
    with queue:
        for i in range(3):
            row=dict(original,queue_item_id=None,company_path_normalized=f'/company/EXTRA{i}/')
            queue.execute('INSERT INTO screener_company_queue ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',[row[k] for k in keys])
    rows,lanes,_=select(queue,now_utc=NOW,activated_at='2026-10-09T00:00:00+00:00',limit=5)
    assert len(rows)==5 and lanes==[1,2,1,1,1]
    assert all(r.result_period!='2026-03' for r in rows)


def test_current_market_cap_reentry_does_not_wait_for_historical_slot(queue):
    from scrapper.db.collection_scope_repo import record_scope
    with queue:
        queue.execute("UPDATE exchange_result_filings SET discovered_at_utc='2026-08-01T00:00:00+00:00'")
        queue.execute("UPDATE screener_company_queue SET status='accepted' WHERE result_period='2026-09'")
        queue.execute("UPDATE screener_company_queue SET last_error_json=? WHERE result_period='2026-06'",
            ('{"reason":"market_cap_reentry","observation":{"basis":"market_cap_check"}}',))
        record_scope(queue,path='/company/ALPHA/',company_id=None,value='1000',
            observed_at=NOW,evidence={'basis':'market_cap_check'})
    rows,lanes,_=pick(queue)
    assert [r.result_period for r in rows]==['2026-06'] and lanes==[2]
    # Old work without re-entry remains behind the existing history boundary.
    assert all(r.result_period!='2026-03' for r in rows)
    qid=rows[0].queue_item_id
    with queue:
        queue.execute('''INSERT INTO screener_company_queue_events
            (queue_item_id,event_at_utc,to_status,evidence_json) VALUES(?,?,'pending',?)''',
            (qid,NOW,'{"reason":"market_cap_reentry"}'))
    item=claim_due_companies(queue,now_utc=NOW,limit=1,worker_id='reentry',queue_item_ids=(qid,))[0]
    mark_running(queue,item,worker_id='reentry',now_utc=NOW)
    fail_company(queue,item,worker_id='reentry',now_utc=NOW,
        error={'type':'Timeout','message':'retry'},retryable=True)
    assert pick(queue)[0]==()
    # Retry errors replace last_error_json; the durable re-entry event retains priority.
    assert qid in [r.queue_item_id for r in pick(queue,now='2026-10-11T02:00:00+00:00')[0]]


def test_market_cap_reentry_still_requires_current_scope_eligibility(queue):
    from scrapper.db.collection_scope_repo import record_scope
    with queue:
        queue.execute("UPDATE screener_company_queue SET last_error_json='{" + '"reason":"market_cap_reentry"' + "}'")
        record_scope(queue,path='/company/ALPHA/',company_id=None,value='100',
            observed_at=NOW,evidence={'basis':'market_cap_check'})
    assert pick(queue)[0]==()
