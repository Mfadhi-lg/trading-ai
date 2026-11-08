from datetime import datetime, timezone
import json
from pathlib import Path


import automatic_acquisition as dispatcher
import phase4_schedule as schedule


def test_downloads_have_three_staggered_opportunities_per_hour(monkeypatch):
    config=json.loads(schedule.CONFIG.read_text())
    monkeypatch.setattr(schedule,'settings',lambda:config)
    at=datetime.fromisoformat('2026-11-02T17:00:00+05:30')
    trigger=next(trigger for _,stage,trigger in schedule.jobs(at) if stage=='DOCUMENT_DOWNLOAD')
    minutes=[]
    previous=None
    for _ in range(3):
        previous=trigger.get_next_fire_time(previous,at)
        minutes.append(previous.minute)
        at=previous
    assert minutes==[8,28,48]


def test_financial_idle_is_reported_without_claiming_success(tmp_path,monkeypatch):
    monkeypatch.setattr(dispatcher,'STATE',tmp_path)
    monkeypatch.setattr(dispatcher,'verify_config',lambda:None)
    def operation(_,out):
        (out/'runtime.log').write_text(json.dumps({'stage':'financial_scheduled_idle','current_period':'2026-09'})+'\n')
        return 0
    result=dispatcher.run_slot('financial','FINANCIAL',datetime.now(timezone.utc),execute=operation)
    assert result['status']=='IDLE'
    assert result['successful_jobs']==result['attempted_jobs']==0
    assert result['stage_can_resume_at_later_eligible_slot']
    assert not (tmp_path/'FINANCIAL.stopped.json').exists()


def test_download_frequency_does_not_increase_daily_budget(tmp_path,monkeypatch):
    config=json.loads(schedule.CONFIG.read_text())
    monkeypatch.setattr(schedule,'settings',lambda:config)
    monkeypatch.setattr(dispatcher,'STATE',tmp_path)
    now=datetime(2026,11,2,12,tzinfo=timezone.utc)
    for ordinal in range(config['download_daily_slots']):
        out=tmp_path/f'2026-11-02-document-download-{ordinal}'
        out.mkdir()
        assert schedule.reserve_daily_stage(out,'DOCUMENT_DOWNLOAD',now)
    extra=tmp_path/'2026-11-02-document-download-extra'
    extra.mkdir()
    assert not schedule.reserve_daily_stage(extra,'DOCUMENT_DOWNLOAD',now)
    reservations=[json.loads(p.read_text()) for p in tmp_path.glob('*/daily-budget-reservation.json')]
    assert sum(r['source_request_units'] for r in reservations)==720
