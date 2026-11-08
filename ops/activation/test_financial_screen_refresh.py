"""Real queue admission from bounded offline paginated screen responses."""
import json
from pathlib import Path
import sys
import requests
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import financial_screen_refresh as refresh
from scrapper.tests.database_fixtures import prepare_database
from scrapper.db.database import connect

NOW='2026-11-04T08:00:00+00:00'
QUERY='Last result date >202608 AND Market Capitalization >580'


@pytest.fixture
def database(tmp_path):
    db=tmp_path/'scraper.db'
    prepare_database(db)
    conn=connect(db)
    yield conn
    conn.close()


def body(page, *, count=2, pages=2, identifier=None, query=QUERY):
    identifier=identifier or str(page)
    return f'''<textarea name="query">{query}</textarea>
      <div data-page-info>{count} results found: Showing page {page} of {pages}</div>
      <div data-page-results><table><tr><th>Name</th><th>Last result date</th></tr>
      <tr data-row-company-id="{identifier}"><td><a href="/company/TEST{identifier}/">Company {identifier}</a></td>
      <td>202609</td></tr></table></div>'''.encode()


class Client:
    def __init__(self,conn,*,responses,**kwargs):
        self.responses=iter(responses)
        self.urls=[]
        self.closed=False
    def _get(self,url):
        self.urls.append(url)
        response=requests.Response()
        response.status_code=200
        response.url=url
        response._content=next(self.responses)
        response.headers['Content-Type']='text/html'
        response.encoding='utf-8'
        return response
    def close(self):
        self.closed=True


def run(conn,tmp_path,html,now=NOW):
    clients=[]
    def factory(c,**kwargs):
        client=Client(c,responses=html,**kwargs)
        clients.append(client)
        return client
    result=refresh.refresh_if_due(conn,now_utc=now,client_factory=factory,snapshot_root=tmp_path/'snapshots')
    return result,clients


def test_dynamic_complete_screen_admits_once_and_respects_refresh_interval(database,tmp_path):
    result,clients=run(database,tmp_path,[body(1),body(2)])
    assert result['status']=='refreshed' and result['captured_companies']==2
    assert result['source_requests']==2 and result['enqueue']['new']==2
    assert clients[0].closed
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==2
    result,clients=run(database,tmp_path,[])
    assert result['status']=='not_due' and clients==[]
    # A later identical capture reuses evidence and canonical jobs, without duplicates.
    result,_=run(database,tmp_path,[body(1),body(2)],'2026-11-05T08:00:00+00:00')
    assert result['status']=='refreshed'
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==2


@pytest.mark.parametrize('html',[
    [body(1),body(2,identifier='1')],
    [body(1,query='Last result date >202605')],
    [body(1,pages=33)],
])
def test_changed_or_incomplete_screen_never_admits_jobs(database,tmp_path,html):
    result,clients=run(database,tmp_path,html)
    assert result['status']=='deferred'
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0
    assert clients[0].closed
    # Failures have a durable retry delay rather than re-fetching every ten minutes.
    assert run(database,tmp_path,[])[0]['status']=='not_due'


def test_access_rejection_retains_evidence_and_does_not_admit(database,tmp_path):
    with pytest.raises(RuntimeError,match='access unavailable'):
        run(database,tmp_path,[b'<html>Access denied captcha</html>'])
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0
    assert database.execute("SELECT count(*) FROM screener_screen_runs WHERE validation_status='failed'").fetchone()[0]==1


def test_same_saved_bytes_can_be_reconsidered_after_pagination_bound_correction(database,tmp_path,monkeypatch):
    monkeypatch.setattr(refresh,'MAX_PAGES',1)
    assert run(database,tmp_path,[body(1)])[0]['status']=='deferred'
    monkeypatch.setattr(refresh,'MAX_PAGES',2)
    result,_=run(database,tmp_path,[body(1),body(2)])
    assert result['status']=='refreshed'
    assert result['enqueue']['new']==2


@pytest.mark.parametrize('second_id,expected',[('2',2),('1',1)])
def test_new_results_during_pagination_admit_observed_rows_with_partial_coverage(database,tmp_path,second_id,expected):
    result,_=run(database,tmp_path,[body(1),body(2,count=3,identifier=second_id)])
    assert result['status']=='refreshed' and result['complete'] is False
    assert result['coverage_status']=='moving_screen_partial'
    assert result['captured_companies']==expected
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==expected
    # No immediate polling; moving screens are revisited after one hour.
    assert run(database,tmp_path,[])[0]['status']=='not_due'
