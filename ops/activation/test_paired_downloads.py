from pathlib import Path
import importlib.util,json,sys,time
import pytest
from paired_downloads import DownloadCompanion


def test_download_child_overlaps_financial_work(tmp_path,capsys):
    ready=tmp_path/'ready';financial=tmp_path/'financial';result=tmp_path/'result.json'
    child='import pathlib,time,json,sys; ready,financial,result=map(pathlib.Path,sys.argv[1:]); ready.touch(); deadline=time.monotonic()+3\nwhile not financial.exists() and time.monotonic()<deadline: time.sleep(.01)\nassert financial.exists(); result.write_text(json.dumps({"stage":"document_stage_outcome","overlap":True}))'
    with DownloadCompanion([sys.executable,'-c',child,str(ready),str(financial),str(result)],log_path=tmp_path/'child.log',result_path=result,timeout=5):
        deadline=time.monotonic()+2
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert ready.exists(),'Download must start before financial work finishes'
        financial.touch()
    assert json.loads(result.read_text())['overlap']


def test_companion_failure_and_parent_failure_are_fenced(tmp_path):
    with pytest.raises(RuntimeError,match='infrastructure'):
        with DownloadCompanion([sys.executable,'-c','raise SystemExit(2)'],log_path=tmp_path/'failed.log',result_path=tmp_path/'missing.json',timeout=3):pass
    child=DownloadCompanion([sys.executable,'-c','import time; time.sleep(10)'],log_path=tmp_path/'cancelled.log',result_path=tmp_path/'cancelled.json',timeout=3)
    with pytest.raises(ValueError):
        with child:raise ValueError('parent failed')
    assert child.process.poll() is not None


def test_fast_schedule_daily_document_budget(tmp_path,monkeypatch):
    import phase4_schedule as schedule
    config={'discovery_limit':5,'screener_request_limit':5,'official_request_limit':2,
        'discovery_daily_slots':24,'discovery_requests_max_per_day':168,
        'discovery_hours':','.join(map(str,range(24))),
        'download_limit':5,'download_daily_slots':72,'processing_daily_slots':72,
        'rating_daily_slots':4,'rating_browser_request_limit':12,
        'source_requests_max_per_day':937,'source_bytes_max_per_day':8026*1024**2}
    monkeypatch.setattr(schedule,'settings',lambda:config)
    from datetime import datetime,timezone
    import automatic_acquisition as dispatcher
    monkeypatch.setattr(dispatcher,'STATE',tmp_path)
    now=datetime(2026,10,31,12,tzinfo=timezone.utc)
    for stage,count in [('DOCUMENT_DISCOVERY',24),('DOCUMENT_DOWNLOAD',72),('DOCUMENT_RATING',4),('DOCUMENT_PATROL',1),('DOCUMENT_PROCESSING',72)]:
        for i in range(count):
            out=tmp_path/f'2026-10-31-{stage}-{i}';out.mkdir()
            assert schedule.reserve_daily_stage(out,stage,now)
        out=tmp_path/f'2026-10-31-{stage}-extra';out.mkdir()
        assert not schedule.reserve_daily_stage(out,stage,now)
    receipts=[json.loads(p.read_text()) for p in tmp_path.glob('*/daily-budget-reservation.json')]
    assert sum(r['source_request_units'] for r in receipts)==937
    assert sum(r['source_byte_units'] for r in receipts)==8026*1024**2


def test_read_only_docker_inspection_is_not_a_competing_writer():
    import importlib.util
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('candidate_owner',root/'artifacts/paired-throughput-rollout/production_ownership.py')
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    verdict,_=guard.classify_process({'args':'docker volume inspect '+guard.VOLUME})
    assert verdict=='unrelated'
    assert guard.classify_process({'args':'docker volume rm '+guard.VOLUME})[0]=='ambiguous'
    assert guard.classify_process({'args':'python writer.py '+guard.VOLUME})[0]=='ambiguous'
    assert guard.classify_process({'args':'docker volume inspect '+guard.VOLUME},[{'name':'/data/scraper.db','access':'w'}])[0]=='conflict'


def test_exact_live_monitor_transport_is_safe_for_the_writer(monkeypatch):
    import acquisition_lifecycle as lifecycle
    import production_ownership as guard
    import shlex
    from types import SimpleNamespace
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if args[:3]==['docker','volume','inspect']:
            return SimpleNamespace(stdout=json.dumps([{'Name':lifecycle.VOLUME,'Labels':{'com.docker.compose.project':'trading-ai'}}]))
        return SimpleNamespace(stdout='{}')
    monkeypatch.setattr(lifecycle,'run',run)
    lifecycle.database_status('/host/acquisition_queries.py',('search','{"term":"fixture","live":true}'))
    command=calls[-1]
    assert guard.classify_process({'args':shlex.join(command)})[0]=='unrelated'
    changed=command.copy();changed.insert(2,'--privileged')
    assert guard.classify_process({'args':shlex.join(changed)})[0]=='ambiguous'
