"""Queue handoff, overlapping workers, restart safety and shared capacity."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

from processing_pipeline import consume_queue, ProcessingCompanion


def test_idle_pipeline_retains_period_recovery_counts():
    result = consume_queue(lambda: {'selected': [], 'outcomes': [],
        'period_recovery': {'checked': 10, 'recovered': 3, 'sweep_complete': True}},
        lambda: True, limit=1)
    assert result['period_recovery'] == {'checked': 10, 'recovered': 3, 'completed_sweeps': 1}


def test_real_saved_document_is_processed_before_producer_finishes():
    from scrapper.tests.test_d03_document_extraction import DocumentExtractionD03Tests, FakeOcr, FIXTURES
    from scrapper.db.database import connect
    from scrapper.operations.workers.document_processing import run_automatic_processing
    from phase4_routine_runtime import routine_authorizer
    fixture = DocumentExtractionD03Tests()
    fixture.setUp()
    now = '2026-11-05T17:00:00+00:00'
    done, first_processed = threading.Event(), threading.Event()
    try:
        with connect(fixture.db_path) as conn:
            conn.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES (?,?,'eligible','580',?,'{}',?)",
                         ('/company/FIXTURE/', fixture.company_id, now, now))
        def guarded(path):
            conn = connect(path)
            conn.set_authorizer(routine_authorizer)
            return conn
        def run_one():
            result = run_automatic_processing(db_path=fixture.db_path, epoch='2026-10-09T00:00:00+00:00',
                limit=1, ocr_engine=FakeOcr(), output_dir=fixture.root/'output',
                now_utc=lambda: now, connection_factory=guarded)
            if result.get('outcomes'):
                first_processed.set()
            return result
        def arrive(name, body=None):
            aid, cid = fixture._content(name, media_type='application/pdf', artifact_type='presentation', body=body)
            with connect(fixture.db_path) as conn:
                conn.execute('UPDATE document_artifacts SET period_end=? WHERE artifact_id=?', ('2026-09-30',aid))
                conn.execute('UPDATE artifact_contents SET created_at_utc=? WHERE content_id=?', (now,cid))
            return cid
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(consume_queue, run_one, done.is_set, limit=10,
                                 wait=lambda _: done.wait(.02), timeout=10)
            try:
                first = arrive('d03_text.pdf')
                assert first_processed.wait(5), 'Processing must not wait for the producer to finish'
                assert not done.is_set()
                second = arrive('second.pdf', (FIXTURES/'d03_text.pdf').read_bytes()+b'\n% second arrival\n')
            finally:
                done.set()
            result = future.result(timeout=10)
        assert [row['content_id'] for row in result['selected']] == [first, second]
        assert all(row['extraction_status'] == 'ok' for row in result['outcomes'])
        assert run_one()['status'] == 'idle', 'A restart must not duplicate completed extraction'
        with connect(fixture.db_path) as conn:
            assert conn.execute('SELECT count(*) FROM document_extractions').fetchone()[0] == 2
            assert conn.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0] == 2
    finally:
        done.set()
        fixture.tearDown()


def test_queue_completion_race_does_not_drop_last_arrival():
    done = False
    queue = []
    def run_one():
        nonlocal done
        if queue:
            return {'selected': [{'content_id': queue.pop()}], 'outcomes': [{'state': 'recorded'}]}
        # Simulate a producer committing after the empty SELECT and then exiting.
        if not done:
            queue.append(1)
            done = True
        return {'status': 'idle', 'selected': [], 'outcomes': []}
    result = consume_queue(run_one, lambda: done, limit=2, wait=lambda _: None)
    assert result['selected'] == [{'content_id': 1}]


def test_processing_failure_is_reported_without_failing_download(tmp_path, capsys):
    child = ProcessingCompanion([sys.executable, '-c', 'raise SystemExit(2)'],
        log_path=tmp_path/'log', result_path=tmp_path/'result', done_path=tmp_path/'done')
    with child:
        download_completed = True
    assert download_completed
    assert child.record['result']['status'] == 'STOP'
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['document_stage'] == 'DOCUMENT_PROCESSING'


def test_parent_failure_terminates_processor(tmp_path):
    child = ProcessingCompanion([sys.executable, '-c', 'import time; time.sleep(30)'],
        log_path=tmp_path/'log', result_path=tmp_path/'result', done_path=tmp_path/'done')
    with pytest.raises(ValueError):
        with child:
            raise ValueError('download infrastructure failed')
    assert child.process.poll() is not None


def test_sqlite_busy_defers_without_infinite_retry():
    def busy():
        raise sqlite3.OperationalError('database is locked')
    result = consume_queue(busy, lambda: True, limit=2)
    assert result['status'] == 'DEFERRED_ENVIRONMENT'


def test_processing_companions_share_daily_processing_capacity(tmp_path, monkeypatch):
    import phase4_schedule as schedule
    import automatic_acquisition as dispatcher
    config = json.loads(schedule.CONFIG.read_text()) | {'processing_limit': 10, 'processing_daily_slots': 2, 'extractions_max_per_day': 20}
    monkeypatch.setattr(schedule, 'settings', lambda: config)
    monkeypatch.setattr(dispatcher, 'STATE', tmp_path)
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    standalone = tmp_path/'2026-11-05-processing'
    pair = tmp_path/'2026-11-05-download'
    extra = tmp_path/'2026-11-05-extra'
    for path in (standalone, pair, extra):
        path.mkdir()
    assert schedule.reserve_daily_stage(standalone, 'DOCUMENT_PROCESSING', now)
    assert schedule.reserve_daily_stage(pair, 'DOCUMENT_DOWNLOAD', now)
    assert schedule.reserve_daily_stage(pair, 'DOCUMENT_PROCESSING', now, reservation_file='processing-budget-reservation.json')
    assert not schedule.reserve_daily_stage(extra, 'DOCUMENT_PROCESSING', now)
    assert not schedule.reserve_daily_stage(extra, 'DOCUMENT_PROCESSING', now, reservation_file='processing-budget-reservation.json')
    assert json.loads((pair/'daily-budget-reservation.json').read_text())['stage'] == 'DOCUMENT_DOWNLOAD'


def test_processing_failure_only_stops_processing_lane(tmp_path, monkeypatch):
    import automatic_acquisition as scheduler
    monkeypatch.setattr(scheduler, 'STATE', tmp_path)
    monkeypatch.setattr(scheduler, 'verify_config', lambda: {})
    def execute(_, out):
        records = [
            {'stage':'document_stage_outcome', 'document_stage':'DOCUMENT_DOWNLOAD',
             'result':{'download':{'processed_job_count':1, 'outcome_counts':{'validated':1}}}},
            {'stage':'document_stage_outcome', 'document_stage':'DOCUMENT_PROCESSING', 'companion':True,
             'result':{'status':'STOP', 'error':'processor failed', 'outcomes':[]}}]
        (out/'runtime.log').write_text('\n'.join(map(json.dumps,records)))
        return 0
    result = scheduler.run_slot('pipeline', 'DOCUMENT_DOWNLOAD', datetime.now(timezone.utc), execute=execute)
    assert result['status'] == 'PASS' and result['successful_jobs'] == 1
    assert result['concurrent_processing']['status'] == 'STOP'
    assert not (tmp_path/'DOCUMENT_DOWNLOAD.stopped.json').exists()
    assert (tmp_path/'DOCUMENT_PROCESSING.stopped.json').exists()


def test_pipeline_admission_updates_plan_and_honors_processing_stop(tmp_path, monkeypatch):
    import hashlib
    import phase4_schedule as schedule
    import automatic_acquisition as dispatcher
    config = json.loads(schedule.CONFIG.read_text()) | {'concurrent_processing_enabled': True}
    monkeypatch.setattr(schedule, 'settings', lambda: config)
    monkeypatch.setattr(dispatcher, 'STATE', tmp_path)
    now = datetime(2026,11,5,12,tzinfo=timezone.utc)
    out = tmp_path/'2026-11-05-download'
    out.mkdir()
    (out/'manifest.json').write_text('{}')
    plan = {'stage':'DOCUMENT_DOWNLOAD', 'focus_membership':{'company_ids':[1]}}
    assert schedule.enable_processing_companion(out, plan, config, now)
    assert plan['concurrent_processing']['limit'] == config['processing_limit']
    assert plan['concurrent_processing']['focus_company_ids'] == [1]
    assert json.loads((out/'manifest.json').read_text())['plan_sha256'] == hashlib.sha256((out/'plan.json').read_bytes()).hexdigest()
    (tmp_path/'DOCUMENT_PROCESSING.stopped.json').write_text('{}')
    blocked = tmp_path/'2026-11-05-blocked'
    blocked.mkdir()
    assert not schedule.enable_processing_companion(blocked, {}, config, now)
    assert not (blocked/'processing-budget-reservation.json').exists()


def test_consumer_leaves_work_beyond_its_budget_queued():
    queue = list(range(11))
    def run_one():
        return {'selected':[{'content_id':queue.pop(0)}], 'outcomes':[{'state':'recorded'}]}
    result = consume_queue(run_one, lambda: True, limit=10)
    assert len(result['selected']) == 10
    assert queue == [10]
