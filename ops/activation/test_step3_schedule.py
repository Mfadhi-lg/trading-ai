"""Focused current-only scheduling, independent cap admission and slot budgets."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import step3_schedule
import automatic_acquisition as scheduler
import step3_financial_runtime as runtime


def enabled():
    config=step3_schedule.settings()
    return config | {'financial_enabled':True,'cap_enabled':True}


@pytest.mark.parametrize('moment,names',[
    ('2026-10-10T00:00:40+05:30',['market-cap']),
    ('2026-10-10T08:45:40+05:30',['current-financial']),
    ('2026-10-10T09:00:40+05:30',['bse-morning']),
    ('2026-10-10T18:00:40+05:30',['bse-evening']),
    ('2026-10-10T19:30:40+05:30',['nse-primary']),
    ('2026-10-10T19:45:40+05:30',['current-financial']),
    ('2026-10-10T19:46:40+05:30',[]),
])
def test_shared_ist_slots_are_staggered_and_do_not_replay(moment,names):
    config=enabled()
    with patch.object(step3_schedule,'settings',return_value=config):
        assert [row[0] for row in scheduler.due(datetime.fromisoformat(moment))]==names


def row(queue,period='2026-09'):
    return SimpleNamespace(queue_item_id=queue,result_period=period,reporting_scope='standalone',origin='exchange_filing')


def test_current_batch_never_fills_with_historical_rows():
    assert [r.queue_item_id for r in runtime.current_prefix([row(18137),row(8,'2026-06')],'2026-09')]==[18137]
    assert runtime.current_prefix([row(8,'2026-06')],'2026-09')==[]
    assert runtime.current_prefix([row(9,'2026-12'),row(18137)],'2026-09')==[]


def test_current_prefix_fences_standalone_official_origin():
    bad=row(7);bad.reporting_scope='consolidated'
    assert runtime.current_prefix([bad],'2026-09')==[]
    bad=row(7);bad.origin='screener_screen'
    assert runtime.current_prefix([bad],'2026-09')==[]


@pytest.mark.parametrize('lane',['FINANCIAL','CAP'])
def test_durable_admission_runs_each_lane_once_per_slot(tmp_path,lane):
    calls=[]
    at=datetime.fromisoformat('2026-10-10T00:00:00+05:30')
    with patch.object(scheduler,'STATE',tmp_path),patch.object(scheduler,'verify_config'):
        operation=lambda kind,out:calls.append(kind) or 0
        assert scheduler.run_slot('fixture',lane,at,execute=operation)['status']=='PASS'
        assert scheduler.run_slot('fixture',lane,at,execute=operation)['status']=='slot_already_consumed'
    assert calls==[lane]


def test_cap_path_does_not_wait_for_old_financial_queue():
    source=Path(runtime.__file__).read_text()
    assert 'priority_ready' not in source and 'run_pending' not in source
    assert "monitor(conn,limit=plan.get('max_scope_checks',1),shared_permits=True" in source
    config=enabled();assert config['cap_max_checks']==40 and config['financial_max_companies']==2


def test_existing_accepted_canary_is_not_a_fixed_scheduled_selection():
    source=Path(step3_schedule.__file__).read_text()
    assert 'queue_item_ids=[]' in source and "priority_result_periods()[0]" in source
    assert "'.nse-owner.lock'" in source


def test_different_hour_slots_keep_their_own_bounded_admissions(tmp_path):
    calls=[]
    operation=lambda kind,out:calls.append(kind) or 0
    with patch.object(scheduler,'STATE',tmp_path),patch.object(scheduler,'verify_config'):
        for moment in ['2026-10-10T00:00:00+05:30','2026-10-10T01:00:00+05:30']:
            assert scheduler.run_slot('market-cap','CAP',datetime.fromisoformat(moment),execute=operation)['status']=='PASS'
    assert calls==['CAP','CAP']
    assert (tmp_path/'2026-10-10-market-cap-0000/admission.json').exists()
    assert (tmp_path/'2026-10-10-market-cap-0100/admission.json').exists()
