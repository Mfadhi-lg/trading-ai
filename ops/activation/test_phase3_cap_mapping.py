"""Monitor mapping correction, validated observations and lease recovery."""
from types import SimpleNamespace
from pathlib import Path
from scrapper.tests.database_fixtures import prepare_database
from scrapper.db.database import connect
from scrapper.db.collection_scope_repo import record_scope
from scrapper.commands.collection_scope import monitor
from scrapper.core.http import FetchResponse
import pytest

NOW='2026-10-11T00:00:00+00:00'
@pytest.mark.parametrize("initial_cap",["579","13217"])
@pytest.mark.parametrize('logical,current',[('HEG','HEGAM'),('TCS','TCS')])
def test_verified_mapping_preserves_scope_identity_and_other_paths(tmp_path,logical,current,initial_cap):
    db=tmp_path/'db';prepare_database(db);c=connect(db)
    with c:
        c.execute("INSERT INTO companies VALUES(1,'Fixture','active')")
        c.execute("INSERT INTO source_identifiers(company_id,source,source_company_id,source_symbol,canonical_url,mapping_status,verified_at_utc) VALUES(1,'screener','1301',?,?, 'verified',?)",(current,'https://www.screener.in/company/'+current+'/consolidated/',NOW))
        record_scope(c,path='/company/'+logical+'/',company_id=1,value=initial_cap,observed_at='2026-08-01T00:00:00+00:00',evidence={'fixture':True},next_check=NOW)
        c.execute("UPDATE screener_collection_scope SET claim_token='expired',claim_expires_at_utc='2026-10-10T00:00:00+00:00'")
    import sys
    sys.path.insert(0,str(Path(__file__).parent))
    from financial_priority import verified_source_target
    assert verified_source_target(c,'/company/'+logical+'/')==('/company/'+current+'/','1301')
    urls=[]
    def fetch(url):
        urls.append(url)
        body=f'<div id="company-info" data-company-id="1301"></div><div id="top"><a>NSE: {current}</a></div><ul id="top-ratios"><li><span class="name">Market Cap</span><span class="value">Rs. 580 Cr.</span></li></ul>'.encode()
        return FetchResponse(url,url,200,body,'text/html','utf-8',None,None,'fixture','ok')
    result=monitor(c,limit=1,client=SimpleNamespace(fetch_company=fetch),now_fn=lambda:NOW,snapshot_root=tmp_path/'snapshots')
    assert urls==['https://www.screener.in/company/'+current+'/']
    assert result['checked']==1 and 'error' not in result['outcomes'][0]
    row=c.execute('SELECT * FROM screener_collection_scope').fetchone()
    assert row['company_path']=='/company/'+logical+'/' and row['eligibility']=='eligible' and row['market_cap_crore']=='580'
    assert row['claim_token'] is None and row['check_failures']==0
    assert result['outcomes'][0]['became_eligible']==(initial_cap=='579')
    assert result['outcomes'][0]['reentered']==(initial_cap=='579')
    c.close()

@pytest.mark.parametrize('status,body',[(404,b'missing'),(401,b'login'),(200,b'<html>not a company</html>')])
def test_failed_or_invalid_body_retains_prior_cap_and_backoff(tmp_path,status,body):
    db=tmp_path/'db';prepare_database(db);c=connect(db)
    with c:record_scope(c,path='/company/HEG/',company_id=None,value='13217',observed_at='2026-08-01T00:00:00+00:00',evidence={},next_check=NOW)
    def fetch(url):return FetchResponse(url,url,status,body,'text/html','utf-8',None,None,'fixture','ok' if status==200 else 'error')
    result=monitor(c,limit=1,client=SimpleNamespace(fetch_company=fetch),now_fn=lambda:NOW,snapshot_root=tmp_path/'snapshots')
    assert 'error' in result['outcomes'][0]
    row=c.execute('SELECT * FROM screener_collection_scope').fetchone()
    assert row['market_cap_crore']=='13217' and row['eligibility']=='eligible'
    assert row['check_failures']==1 and row['next_check_at_utc']>NOW and row['claim_token'] is None
    assert c.execute('SELECT count(*) FROM screener_scope_observations').fetchone()[0]==0
    c.close()
