"""Incident regressions; isolated fixtures only, no production or HTTP."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import patch

import pytest
import automatic_acquisition as dispatcher
from acquisition_failures import classify_exception, runtime_entrypoint
from phase4_routine_runtime import routine_authorizer
from phase4_schedule import authorization_state


@pytest.mark.parametrize('code,state',[(75,'DEFERRED_ENVIRONMENT'),(76,'FAILED_JOB'),
    (77,'EXPIRED_AUTHORIZATION'),(78,'BLOCKED_SOURCE_POLICY')])
def test_explicit_document_failures_allow_later_fresh_slots(tmp_path,code,state):
    at=datetime(2026,10,26,0,25,tzinfo=timezone.utc)
    with patch.object(dispatcher,'STATE',tmp_path),patch.object(dispatcher,'verify_config'):
        first=dispatcher.run_slot('document-download','DOCUMENT_DOWNLOAD',at,execute=lambda *_:code)
        assert first['status']==state
        assert not (tmp_path/'DOCUMENT_DOWNLOAD.stopped.json').exists()
        assert dispatcher.run_slot('document-download','DOCUMENT_DOWNLOAD',at,
            execute=lambda *_:pytest.fail('Duplicate attempt'))['status']=='slot_already_consumed'
        assert dispatcher.run_slot('document-download','DOCUMENT_DOWNLOAD',at+timedelta(hours=1),
            execute=lambda *_:0)['status']=='PASS'


def test_unknown_integrity_failure_still_stops_stage(tmp_path):
    with patch.object(dispatcher,'STATE',tmp_path),patch.object(dispatcher,'verify_config'):
        r=dispatcher.run_slot('document-download','DOCUMENT_DOWNLOAD',datetime.now(timezone.utc),execute=lambda *_:2)
        assert r['status']=='STOP'
        assert (tmp_path/'DOCUMENT_DOWNLOAD.stopped.json').exists()


def test_financial_docker_outage_before_runtime_does_not_latch(tmp_path):
    def unavailable(_,out):
        (out/'runtime.log').write_text('Cannot connect to the Docker daemon. Is the docker daemon running?')
        return 125
    with patch.object(dispatcher,'STATE',tmp_path),patch.object(dispatcher,'verify_config'):
        result=dispatcher.run_slot('current-financial','FINANCIAL',datetime.now(timezone.utc),execute=unavailable)
        assert result['status']=='DEFERRED_ENVIRONMENT'
        assert not (tmp_path/'FINANCIAL.stopped.json').exists()


def test_unexpired_retained_lease_defers_without_clearing_or_latching(tmp_path):
    def held(*_):raise RuntimeError('Unexpired production acquisition lease in artifact_download_queue')
    with patch.object(dispatcher,'STATE',tmp_path),patch.object(dispatcher,'verify_config'):
        result=dispatcher.run_slot('document-download','DOCUMENT_DOWNLOAD',datetime.now(timezone.utc),execute=held)
        assert result['status']=='DEFERRED_ENVIRONMENT'
        assert not (tmp_path/'DOCUMENT_DOWNLOAD.stopped.json').exists()
    assert classify_exception(RuntimeError('active writable production mount: foreign-writer'))==('STOP',2)


@pytest.mark.parametrize('error',[
    'Document-store capacity file is stale: measured 1295s ago',
    'External document volume is not mounted',
    'Cannot inspect ownership: docker'])
def test_temporary_preflight_failure_never_consumes_a_job(error):
    assert classify_exception(RuntimeError(error))==('DEFERRED_ENVIRONMENT',75)


@pytest.mark.parametrize('error',['External drive identity changed','Archive hash or size changed',
    'Production DB file identity mismatch','Timed-out document container still active',
    'Document sentinel missing','not authorized','disk I/O error'])
def test_critical_errors_are_never_downgraded(error):
    assert classify_exception(RuntimeError(error))==('STOP',2)


def test_finite_authorization_and_persistent_service_are_separate():
    c={'enabled':True,'activated_at_utc':'2026-10-24T00:00:00+00:00',
       'expires_at_utc':'2026-10-25T00:00:00+00:00'}
    now=datetime(2026,10,26,tzinfo=timezone.utc)
    assert authorization_state(c,now)=='EXPIRED_AUTHORIZATION'
    c['authorization_mode']='continuous'
    with pytest.raises(RuntimeError):authorization_state(c,now)
    c['authorization_evidence']='explicit recovery instruction'
    assert authorization_state(c,now)=='RUNNING'
    c['enabled']=False
    assert authorization_state(c,now)=='PAUSED'


def test_runtime_retains_original_exception_and_structured_deferral(capsys):
    def stale():raise RuntimeError('Document-store capacity file is stale: test')
    assert runtime_entrypoint(stale)==75
    captured=capsys.readouterr()
    assert 'Traceback' in captured.err
    assert json.loads(captured.out)['status']=='DEFERRED_ENVIRONMENT'


def test_write_fence_rejects_direct_trigger_and_pragma_escape():
    c=sqlite3.connect(':memory:')
    try:
        c.executescript('''CREATE TABLE financial_observations(id INTEGER PRIMARY KEY,value TEXT);
            CREATE TABLE artifact_contents(id INTEGER PRIMARY KEY,value TEXT);
            INSERT INTO financial_observations VALUES(1,'original');
            CREATE TRIGGER attempted_escape AFTER INSERT ON artifact_contents
            BEGIN UPDATE financial_observations SET value='changed'; END;''')
        c.set_authorizer(routine_authorizer)
        for sql in ("UPDATE financial_observations SET value='bad'",'DELETE FROM financial_observations',
                    "INSERT INTO financial_observations VALUES(2,'bad')",
                    "INSERT INTO artifact_contents VALUES(1,'trigger')",'PRAGMA writable_schema=ON',
                    'PRAGMA foreign_keys=OFF','DROP TRIGGER attempted_escape',
                    "ATTACH ':memory:' AS bypass"):
            with pytest.raises(sqlite3.DatabaseError):c.execute(sql)
        assert c.execute('SELECT value FROM financial_observations').fetchone()[0]=='original'
    finally:c.close()


def test_batched_financial_metadata_preserves_selection_and_bounds_queries(queue):
    # Fixture is provided by test_financial_priority when this module is collected.
    from financial_priority import select
    trace=[];queue.set_trace_callback(trace.append)
    try:
        selected,lanes,_=select(queue,now_utc='2026-10-11T00:00:00+00:00',
            activated_at='2026-10-09T00:00:00+00:00')
    finally:queue.set_trace_callback(None)
    assert lanes==[1,2]
    assert len(selected)==2 and len(trace)==2


from test_financial_priority import queue


def test_manual_and_scheduled_document_operations_share_daily_budget(tmp_path):
    import phase4_schedule
    now=datetime(2026,10,26,5,tzinfo=timezone.utc)
    with patch.object(dispatcher,'STATE',tmp_path):
        for ordinal in range(4):
            out=tmp_path/('2026-10-26-document-discovery-'+str(ordinal))
            out.mkdir()
            assert phase4_schedule.reserve_daily_stage(out,'DOCUMENT_DISCOVERY',now)
        last=tmp_path/'2026-10-26-document-discovery-final';last.mkdir()
        assert not phase4_schedule.reserve_daily_stage(last,'DOCUMENT_DISCOVERY',now)
        assert not (last/'daily-budget-reservation.json').exists()


def test_local_historical_campaign_is_explicit_bounded_and_networkless(tmp_path):
    import phase4_schedule
    phase4_schedule.prepare(tmp_path,'DOCUMENT_PROCESSING',local_historical_processing=True)
    plan=json.loads((tmp_path/'plan.json').read_text())
    compose=json.loads((tmp_path/'compose.json').read_text())
    assert plan['historical_limit']==0
    assert plan['local_historical_processing_limit']==1
    assert compose['services']['document-once']['network_mode']=='none'
    with pytest.raises(ValueError):phase4_schedule.prepare(tmp_path,'DOCUMENT_DOWNLOAD',local_historical_processing=True)


@pytest.mark.parametrize('failure',[sqlite3.OperationalError('database is locked'),OSError('disk unavailable')])
def test_download_infrastructure_failure_is_not_a_terminal_job(failure):
    from scrapper.tests.test_document_download_queue import DocumentDownloadQueueDA05Tests,FakeHealth,NOW
    from scrapper.db.document_download_queue_repo import materialize_download_queue
    from scrapper.pipeline.run_document_download_worker import run_document_download_worker
    fixture=DocumentDownloadQueueDA05Tests();fixture.setUp()
    try:
        fixture.register('recovery-infrastructure',('presentation','transcript','credit_rating'))
        materialize_download_queue(fixture.conn,scope_id='recovery-infrastructure',created_at_utc=NOW)
        with patch('scrapper.pipeline.run_document_download_worker.download_url',side_effect=failure):
            with pytest.raises(type(failure)):
                run_document_download_worker(fixture.conn,scope_id='recovery-infrastructure',limit=2,
                    document_store=fixture.root,health_check=FakeHealth,throttle_seconds=0,
                    now=lambda:NOW,max_attempts=1)
        assert fixture.conn.execute("SELECT count(*) FROM artifact_download_queue WHERE last_result_status='WORKER_EXCEPTION'").fetchone()[0]==0
    finally:fixture.tearDown()


def test_existing_recovery_classification_handoff_passes_narrow_write_fence():
    from scrapper.tests.test_recovery_reconciliation_ownership import RecoveryReconciliationOwnershipTests
    from scrapper.db.documents.recovery.reconciliation import reconcile_recovery_for_source
    fixture=RecoveryReconciliationOwnershipTests();fixture.setUp()
    try:
        source,content=fixture._attached_content()
        fixture._seed_extraction(content,'Downloaded filing without rating evidence')
        fixture.conn.set_authorizer(routine_authorizer)
        result=reconcile_recovery_for_source(fixture.conn,artifact_source_id=source,content_id=content)
        assert result['classification_status']=='CLASSIFIED'
        with pytest.raises(sqlite3.DatabaseError,match='not authorized'):fixture.conn.execute("UPDATE artifact_recovery_candidates SET candidate_url='https://wrong.example'")
        with pytest.raises(sqlite3.DatabaseError):fixture.conn.execute('DELETE FROM artifact_recovery_targets')
    finally:fixture.tearDown()


def test_stage_deadline_and_database_contention_defer_but_corruption_stops(capsys):
    from acquisition_failures import ExecutionDeadline
    assert classify_exception(sqlite3.OperationalError('database is locked'))==('DEFERRED_ENVIRONMENT',75)
    assert classify_exception(sqlite3.DatabaseError('database disk image is malformed'))==('STOP',2)
    def deadline():raise ExecutionDeadline('bounded stage expired')
    assert runtime_entrypoint(deadline)==75
    assert 'execution_failure' in capsys.readouterr().out


def test_unhandled_request_error_is_still_a_job_failure():
    import requests
    from scrapper.tests.test_document_download_queue import DocumentDownloadQueueDA05Tests,FakeHealth,NOW
    from scrapper.db.document_download_queue_repo import materialize_download_queue
    from scrapper.pipeline.run_document_download_worker import run_document_download_worker
    fixture=DocumentDownloadQueueDA05Tests();fixture.setUp()
    try:
        fixture.register('recovery-network',('presentation','transcript','credit_rating'))
        materialize_download_queue(fixture.conn,scope_id='recovery-network',created_at_utc=NOW)
        with patch('scrapper.pipeline.run_document_download_worker.download_url',side_effect=requests.ConnectionError('temporary source failure')):
            result=run_document_download_worker(fixture.conn,scope_id='recovery-network',limit=2,
                document_store=fixture.root,health_check=FakeHealth,throttle_seconds=0,
                now=lambda:NOW,max_attempts=3)
        assert result['processed_job_count']==2
        assert fixture.conn.execute("SELECT count(*) FROM artifact_download_queue WHERE status='RETRYABLE_FAILURE'").fetchone()[0]==2
    finally:fixture.tearDown()
