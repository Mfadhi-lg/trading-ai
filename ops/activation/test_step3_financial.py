"""Offline tests of the selected fence, inclusivity and read-only observation."""
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from scrapper.core.collection_scope import numeric_cap
from scrapper.db.collection_scope_repo import record_scope
from scrapper.workflows.screener.ingest import _market_cap_outside_scope
from scrapper.tests.database_fixtures import prepare_database
from scrapper.db.database import connect


def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
    return loaded


financial=module('step3_financial_runtime')
observer=module('verify_unattended_acquisition')


@pytest.mark.parametrize('cap,excluded', [('579.999',True),('580',False),('580.001',False)])
def test_inclusive_floor_agrees_between_ingestion_and_scope(tmp_path,cap,excluded):
    parsed=SimpleNamespace(metrics=[SimpleNamespace(metric_code='market_cap',value_decimal=cap)])
    assert _market_cap_outside_scope(parsed)==excluded
    db=tmp_path/'fixture.db';prepare_database(db);c=connect(db)
    try:
        eligibility=record_scope(c,path='/company/BOUNDARY/',company_id=None,value=cap,
            observed_at='2026-10-09T09:00:00+00:00',evidence={'fixture':True})
        assert eligibility == ('below_floor' if excluded else 'eligible')
    finally:c.close()


@pytest.mark.parametrize('rows',[
    [SimpleNamespace(queue_item_id=18126,result_period='2026-06',reporting_scope='standalone',origin='exchange_filing')],
    [SimpleNamespace(queue_item_id=18127,result_period='2026-09',reporting_scope='standalone',origin='exchange_filing')],
    [],
])
def test_changed_or_historical_due_queue_refuses_before_claim(rows):
    from scrapper.db import screener_discovery_repo as repo
    plan={'queue_item_ids':[18126],'result_period':'2026-09'}
    with patch.object(repo,'preview_due_companies',return_value=rows),patch.object(repo,'claim_due_companies') as claim:
        with pytest.raises(RuntimeError):
            financial.selected_claim(None,plan=plan,now_utc='2026-10-09T09:00:00+00:00',limit=1,worker_id='fixture')
        claim.assert_not_called()


def test_selected_claim_delegates_to_existing_fenced_claimant():
    from scrapper.db import screener_discovery_repo as repo
    row=SimpleNamespace(queue_item_id=18126,result_period='2026-09',reporting_scope='standalone',origin='exchange_filing')
    with patch.object(repo,'preview_due_companies',return_value=[row]),patch.object(repo,'claim_due_companies',return_value=(row,)) as claim:
        assert financial.selected_claim(None,plan={'queue_item_ids':[18126],'result_period':'2026-09'},
            now_utc='2026-10-09T09:00:00+00:00',limit=1,worker_id='fixture')==(row,)
        claim.assert_called_once()


def test_deadline_bypasses_per_company_retry_handler():
    assert not issubclass(financial.DeadlineExceeded,Exception)


def test_existing_coordinator_would_wait_on_historical_company_queue(tmp_path):
    from scrapper.operations.coordinator.collection import priority_ready
    from scrapper.pipeline.screener_discovery import discover_screener_results
    from scrapper.tests.test_screener_discovery import screen_html
    db=tmp_path/'fixture.db';prepare_database(db)
    fixture=tmp_path/'screen.html';fixture.write_bytes(screen_html().replace(b'/consolidated/',b'/'))
    discover_screener_results(query_role='historical_fixture',query='x',fixture=fixture,
        db_path=db,snapshot_root=tmp_path/'snapshots')
    c=connect(db)
    try:
        assert not c.execute("SELECT 1 FROM screener_company_queue WHERE result_period='2026-09'").fetchone()
        assert priority_ready(c,'2099-01-01T00:00:00+00:00') is True
    finally:c.close()


def test_observer_waits_without_launching_or_opening_database(tmp_path):
    import socket, sqlite3,subprocess
    with patch.object(socket,'socket',side_effect=AssertionError('No network')),\
         patch.object(sqlite3,'connect',side_effect=AssertionError('No DB')),\
         patch.object(subprocess,'run',side_effect=AssertionError('No process')):
        result=observer.audit_existing_runs(state=tmp_path,receipts=tmp_path/'receipts')
    assert result['source_requests']==0 and result['forced_attempts']==0
    assert result['first_real_unattended_attempts']['NSE']['status']=='PENDING_SLOT_OR_SOURCE_EVIDENCE'


def test_observer_uses_first_real_launchd_attempt_and_skips_manual(tmp_path):
    for name,job,start,origin in [('2026-10-09-nse-primary','nse-primary','2026-10-09T14:00:10+00:00','manual_tick'),
            ('2026-10-10-nse-primary','nse-primary','2026-10-10T14:00:10+00:00','launchd')]:
        out=tmp_path/name;out.mkdir()
        (out/'admission.json').write_text(json.dumps({'exchange':'NSE','job':job,'started_at_utc':start,
            'scheduled_at_ist':start,'dispatch_origin':origin}))
        (out/'outcome.json').write_text(json.dumps({'status':'PASS'}))
        (out/'runtime.log').write_text(json.dumps({'stage':'source_request_started'})+'\n')
    result=observer.audit_existing_runs(state=tmp_path,receipts=tmp_path/'receipts')
    assert '2026-10-10' in result['first_real_unattended_attempts']['NSE']['path']
