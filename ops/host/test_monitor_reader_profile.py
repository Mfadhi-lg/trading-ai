import copy
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
import production_ownership as profile

def test_only_exact_monitor_profile_can_share_writer_volume(monkeypatch):
    from phase4_documents import IMAGE
    # Candidate must use production repository paths after installation.
    monkeypatch.setattr(profile,'__file__',str(ROOT/'ops/activation/production_ownership.py'))
    row={'State':{'Status':'running'},'Image':IMAGE,'Path':'python',
         'Args':['/host/acquisition_live_reader.py','quarter_monitor','{"quarter":"2026-06"}'],
         'Config':{'Labels':{'com.trading-ai.role':'monitor-reader'}},
         'HostConfig':{'ReadonlyRootfs':True,'NetworkMode':'none','CapDrop':['ALL'],'SecurityOpt':['no-new-privileges']},
         'Mounts':[{'Name':profile.VOLUME,'Destination':'/data','RW':True}]+[
            {'Destination':target,'Source':str(ROOT/source),'RW':False} for target,source in
            [('/host','ops/host'),('/activation','ops/activation'),('/acquisition_financial_renderer.py','scrapper/view/render.py')]]}
    assert profile.monitor_reader(row)
    assert profile.container_conflict(row) is None
    for key,value in [('Path','sh'),('Args',['-c','malicious']),('Image','other')]:
        bad=copy.deepcopy(row);bad[key]=value
        assert profile.container_conflict(bad)=='active writable production mount'
    bad=copy.deepcopy(row);bad['HostConfig']['NetworkMode']='bridge'
    assert profile.container_conflict(bad)=='active writable production mount'
    bad=copy.deepcopy(row);bad['Mounts'][1]['RW']=True
    assert profile.container_conflict(bad)=='active writable production mount'
