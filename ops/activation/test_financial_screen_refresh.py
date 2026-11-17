"""Real queue admission from bounded offline paginated screen responses."""
import json
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch
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


def grant(now=NOW, **overrides):
    return dict(url=refresh.SCREEN_URL, query=QUERY, day_utc=now[:10],
        authorization='synthetic offline fixture', max_pages=refresh.MAX_PAGES,
        requests_per_capture=refresh.MAX_PAGES, source_requests_per_day=148,
        requests_per_day=148, **overrides)


def run(conn,tmp_path,html,now=NOW,budget=None):
    clients=[]
    def factory(c,**kwargs):
        client=Client(c,responses=html,**kwargs)
        clients.append(client)
        return client
    with patch('scrapper.db.repo.utc_now',return_value=now):
        result=refresh.refresh_if_due(conn,now_utc=now,client_factory=factory,snapshot_root=tmp_path/'snapshots',budget=budget or grant(now),clock=lambda:datetime.fromisoformat(now))
    return result,clients


def test_dynamic_complete_screen_admits_once_and_respects_refresh_interval(database,tmp_path):
    result,clients=run(database,tmp_path,[body(1),body(2)])
    assert result['status']=='refreshed' and result['captured_companies']==2
    assert result['source_requests']==2 and result['enqueue']['new']==2
    assert clients[0].closed
    config=json.loads(database.execute('SELECT config_json FROM scrape_runs WHERE code_version=?',
        (refresh.RUN_VERSION,)).fetchone()[0])
    assert config['capture_complete'] is True
    assert config['period']=='2026-09' and config['max_pages']==refresh.MAX_PAGES
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
    [body(1,pages=38)],
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


def test_mixed_month_formats_admit_once_without_live_requests(database,tmp_path):
    pages = [body(1).replace(b"202609", b"Sep-26"), body(2)]
    # Match the retained publisher header without copying authenticated HTML or
    # pretending its 37-page partial capture is a complete admission fixture.
    pages = [page.replace(b'<th>Last result date</th>',
        b'<th data-tooltip="Last result date">Qtr Date YYYYMM</th>') for page in pages]
    result,clients=run(database,tmp_path,pages)
    assert result['status']=='refreshed' and result['enqueue']['new']==2
    assert len(clients[0].urls)==2 and clients[0].closed
    rows=database.execute('SELECT company_path_normalized,result_period,reporting_scope FROM screener_company_queue ORDER BY queue_item_id').fetchall()
    assert [(r['company_path_normalized'],r['result_period'],r['reporting_scope']) for r in rows]==[
        ('/company/TEST1/','2026-09','standalone'),('/company/TEST2/','2026-09','standalone')]
    result,_=run(database,tmp_path,pages,'2026-11-05T08:00:00+00:00')
    assert result['status']=='refreshed' and result['enqueue']['new']==0
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==2


@pytest.mark.parametrize('display',[b'Dec-26',b'Sep-99',b'202612',b'Sept-26'])
def test_future_or_malformed_period_never_admits(database,tmp_path,display):
    result,_=run(database,tmp_path,[body(1).replace(b'202609',display)])
    assert result['status']=='deferred' and result['queue_admission'] is False
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0


def test_abbreviated_duplicate_page_never_admits(database,tmp_path):
    result,_=run(database,tmp_path,[body(1).replace(b'202609',b'Sep-26'),body(2,identifier='1')])
    assert result['status']=='deferred'
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0


def test_same_saved_bytes_can_be_reconsidered_after_pagination_bound_correction(database,tmp_path,monkeypatch):
    monkeypatch.setattr(refresh,'MAX_PAGES',1)
    assert run(database,tmp_path,[body(1)])[0]['status']=='deferred'
    monkeypatch.setattr(refresh,'MAX_PAGES',2)
    result,_=run(database,tmp_path,[body(1),body(2)])
    assert result['status']=='refreshed'
    assert result['enqueue']['new']==2


@pytest.mark.parametrize('second_id',['2','1'])
def test_changing_counts_defer_without_admitting_observed_rows(database,tmp_path,second_id):
    result,_=run(database,tmp_path,[body(1),body(2,count=3,identifier=second_id)])
    assert result['status']=='deferred' and result['queue_admission'] is False
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0
    assert run(database,tmp_path,[])[0]['status']=='not_due'


def complete_37_pages():
    """Synthetic 1,833 companies: 36 pages of 50, final page of 33."""
    pages=[]
    for page in range(1,38):
        rows=''.join(f'<tr data-row-company-id="{i}"><td><a href="/company/TEST{i}/">Company {i}</a></td><td>Sep-26</td></tr>'
            for i in range((page-1)*50+1,min(page*50,1833)+1))
        pages.append((f'<textarea name="query">{QUERY}</textarea>'
            f'<div data-page-info>1833 results found: Showing page {page} of 37</div>'
            '<div data-page-results><table><tr><th>Name</th><th data-tooltip="Last result date">Qtr Date YYYYMM</th></tr>'
            +rows+'</table></div>').encode())
    return pages


def test_complete_37_page_capture_admits_1833_canonical_jobs_once(database,tmp_path):
    pages=complete_37_pages()
    first,clients=run(database,tmp_path,pages)
    assert first['complete'] and first['enqueue']['new']==1833
    assert first['page_count']==37 and len(clients[0].urls)==37
    again,_=run(database,tmp_path,pages,'2026-11-05T08:00:00+00:00')
    assert again['complete'] and again['enqueue']['new']==0
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==1833


@pytest.mark.parametrize('field',['requests_per_capture','source_requests_per_day','requests_per_day'])
def test_remaining_37_pages_must_fit_each_budget_before_page_two(database,tmp_path,field):
    budget=grant();budget[field]=36
    result,clients=run(database,tmp_path,[complete_37_pages()[0]],budget=budget)
    assert result['status']=='deferred' and result['source_requests']==1
    assert len(clients[0].urls)==1 and clients[0].closed
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0


@pytest.mark.parametrize('fault',['page_number','page_count','count','duplicate','query','missing','interrupt'])
def test_37_page_capture_faults_never_admit(database,tmp_path,fault):
    pages=complete_37_pages()
    if fault=='page_number': pages[1]=pages[1].replace(b'page 2 of',b'page 3 of')
    if fault=='page_count': pages[1]=pages[1].replace(b'of 37',b'of 36')
    if fault=='count': pages[1]=pages[1].replace(b'1833 results',b'1834 results')
    if fault=='duplicate': pages[1]=pages[1].replace(b'"51"',b'"1"').replace(b'TEST51/',b'TEST1/')
    if fault=='query': pages[1]=pages[1].replace(b'>202608',b'>202605')
    if fault in ('missing','interrupt'):
        pages=pages[:1]
        expected=StopIteration if fault=='missing' else KeyboardInterrupt
        if fault=='interrupt':
            first=pages[0]
            class Interrupted:
                def __iter__(self):
                    yield first
                    raise KeyboardInterrupt()
            pages=Interrupted()
        with pytest.raises(expected): run(database,tmp_path,pages)
    else:
        result,_=run(database,tmp_path,pages)
        assert result['status']=='deferred'
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0
    config=json.loads(database.execute('SELECT config_json FROM scrape_runs WHERE code_version=?',
        (refresh.RUN_VERSION,)).fetchone()[0])
    assert config['reserved_requests']==37 and config['capture_complete'] is False


def test_interrupted_reservation_consumes_day_capacity_and_cannot_be_reused(database,tmp_path):
    budget=grant();budget['requests_per_day']=37
    with pytest.raises(StopIteration):run(database,tmp_path,[complete_37_pages()[0]],budget=budget)
    result,clients=run(database,tmp_path,[],now='2026-11-04T10:00:00+00:00',budget=budget)
    assert result['status']=='deferred' and result['source_requests']==0 and clients==[]


@pytest.mark.parametrize('change',[
    {'url':refresh.SCREEN_URL+'?page=1'}, {'query':QUERY+' AND Sales >0'},
    {'day_utc':'2026-11-03'}, {'max_pages':38}, {'requests_per_capture':True},
    {'authorization':''},
])
def test_budget_requires_exact_explicit_identity_and_bounds(database,tmp_path,change):
    budget=grant();budget.update(change)
    result,clients=run(database,tmp_path,[],budget=budget)
    assert result['status']=='deferred' and result['source_requests']==0 and clients==[]


def test_no_budget_never_starts_source_requests(database,tmp_path):
    result=refresh.refresh_if_due(database,now_utc=NOW,
        client_factory=lambda *a,**k:pytest.fail('No HTTP without grant'))
    assert result['status']=='deferred' and result['source_requests']==0


def test_37_page_before_after_admission_contract(tmp_path,monkeypatch):
    connections=[]
    try:
        for maximum,expected in ((32,0),(37,1833)):
            monkeypatch.setattr(refresh,'MAX_PAGES',maximum)
            directory=tmp_path/str(maximum);directory.mkdir()
            prepare_database(directory/'fixture.db')
            conn=connect(directory/'fixture.db');connections.append(conn)
            result,clients=run(conn,directory,complete_37_pages())
            assert result['source_requests']==(1 if maximum==32 else 37)
            assert conn.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==expected
            assert result['status']==('deferred' if maximum==32 else 'refreshed')
    finally:
        for conn in connections:conn.close()


def test_original_retained_page_when_explicitly_available(database,tmp_path):
    import hashlib
    import os
    path=os.environ.get('ACQ02_RETAINED_SCREEN')
    if not path:pytest.skip('Original private retained HTML unavailable; synthetic tests do not certify it')
    raw=Path(path).read_bytes()
    assert len(raw)==63033
    assert hashlib.sha256(raw).hexdigest()=='42ff2bf8bdaa9a120b3e7843c765bb4c0b5d8029b02e8699ade88bc154639b11'
    from scrapper.sources.screener.result_screen import parse_result_screen
    parsed=parse_result_screen(raw,QUERY,refresh.SCREEN_URL)
    assert (len(parsed.rows),parsed.page_count,parsed.result_count)==(50,37,1833)
    with pytest.raises(StopIteration):run(database,tmp_path,[raw])
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0


def test_expired_utc_day_never_dispatches(database,tmp_path):
    result=refresh.refresh_if_due(database,now_utc=NOW,budget=grant(),
        clock=lambda:datetime.fromisoformat('2026-11-05T00:00:00+00:00'),
        snapshot_root=tmp_path/'snapshots',client_factory=lambda c,**k:Client(c,responses=[]))
    assert result['status']=='deferred' and result['source_requests']==0


def test_transport_preserves_limits_and_rejects_midnight_during_permit_wait(database,tmp_path):
    from scrapper.config import REQUEST_TIMEOUT
    checks=[]
    def factory(conn,**kwargs):
        assert kwargs['policy'].max_redirects==0
        assert kwargs['policy'].max_bytes==8*1024*1024
        assert kwargs['policy'].total_timeout_seconds==REQUEST_TIMEOUT
        checks.append(kwargs['check'])
        client=Client(conn,responses=[])
        def get(url):
            kwargs['check']()  # Transport's checkpoint immediately before dispatch.
            pytest.fail('Expired grant must not dispatch')
        client._get=get
        return client
    instants=iter([datetime.fromisoformat(NOW),datetime.fromisoformat('2026-11-05T00:00:00+00:00')])
    result=refresh.refresh_if_due(database,now_utc=NOW,budget=grant(),
        clock=lambda:next(instants),client_factory=factory,snapshot_root=tmp_path/'snapshots')
    assert checks and result['status']=='deferred' and result['source_requests']==0


def test_same_day_unmetered_legacy_run_defers_without_guessing_redirect_usage(database,tmp_path):
    from scrapper.db.repo import start_run,finish_run
    with patch('scrapper.db.repo.utc_now',return_value='2026-11-04T01:00:00+00:00'):
        run_id=start_run(database,refresh.RUN_VERSION,{'period':'2026-09','max_pages':32})
        finish_run(database,run_id,'error')
    result,clients=run(database,tmp_path,[])
    assert result['status']=='deferred' and result['source_requests']==0 and clients==[]
    assert 'Unmetered legacy' in result['reason']


def test_redirect_response_is_retained_without_following_or_admitting(database,tmp_path):
    from scrapper.operations.workers.screener_requests import SharedScreenerClient
    class Redirect:
        def get(self,url,**kwargs):
            response=requests.Response()
            response.status_code=302;response.url=url
            response.headers['Location']=url+'?page=2'
            response._content=b'original redirect bytes';response._content_consumed=True
            response.encoding='utf-8'
            return response
    clients=[]
    def factory(conn,**kwargs):
        client=SharedScreenerClient(conn,cookie_path=tmp_path/'missing-cookie',
            offline_session=Redirect(),**kwargs)
        clients.append(client)
        return client
    # Synchronous offline transport has no child process; Linux fence is tested separately.
    with patch('sys.platform','linux'), patch('scrapper.core.http.load_screener_cookies',
            return_value={'sessionid':'offline-fixture'}), pytest.raises(requests.TooManyRedirects):
        refresh.refresh_if_due(database,now_utc=NOW,budget=grant(),
            clock=lambda:datetime.fromisoformat(NOW),client_factory=factory,
            snapshot_root=tmp_path/'snapshots')
    assert clients[0].transport.dispatched==1
    assert database.execute('SELECT count(*) FROM screener_company_queue').fetchone()[0]==0
    assert database.execute("SELECT count(*) FROM fetch_observations WHERE resource_kind='shared_partial'").fetchone()[0]==1
    assert any(path.read_bytes()==b'original redirect bytes' for path in (tmp_path/'snapshots').rglob('*') if path.is_file())
