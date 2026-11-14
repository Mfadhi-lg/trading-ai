"""Terminal regressions use temporary receipts and never operate production."""
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acquisition_terminal as terminal


@pytest.fixture
def receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal.lifecycle.dispatcher, 'STATE', tmp_path)
    monkeypatch.setattr(terminal.lifecycle, 'STATE', tmp_path/'lifecycle')
    monkeypatch.setattr(terminal.lifecycle, 'PAUSE', tmp_path/'lifecycle-paused.json')
    monkeypatch.setattr(terminal.lifecycle, 'schedule_status', lambda: {'jobs': [], 'document_window_expired': False})
    monkeypatch.setattr(terminal.lifecycle, 'run', lambda *a, **k: pytest.fail('Fast terminal feed must not invoke Docker, launchctl or source requests'))
    monkeypatch.setattr(terminal.lifecycle, 'database_status', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Fixture database unavailable')))
    return tmp_path


def save_job(state, name, *, complete=True):
    folder = state/name
    folder.mkdir()
    (folder/'admission.json').write_text(json.dumps({'job':'current-financial','exchange':'FINANCIAL',
        'started_at_utc':'2026-10-22T18:00:00+00:00'}))
    if complete:
        (folder/'outcome.json').write_text(json.dumps({'status':'PASS','finished_at_utc':'2026-10-22T18:00:30+00:00'}))
    (folder/'runtime.log').write_text('partial broken JSON\n'+json.dumps({'stage':'screener_request_started','url':'https://www.screener.in/company/CARERATING/'})+'\n'+json.dumps({'stage':'financial_batch_outcome','result':{'claimed':[{'queue_item_id':1,'company_path_normalized':'/company/CARERATING/','result_period':'2026-09','reporting_scope':'standalone'}],'outcomes':[{'queue_item_id':1,'status':'accepted'}]}})+'\n')
    return folder


def render(feed, inspection, **kwargs):
    stream = StringIO()
    Console(file=stream, width=120, color_system=None).print(terminal.render_monitor(feed, inspection, **kwargs))
    return stream.getvalue()


def test_fast_feed_retains_company_results_and_never_writes(receipts):
    folder = save_job(receipts, '2026-10-22-current-financial-1800')
    before = {p:p.read_bytes() for p in folder.iterdir()}
    feed = terminal.ReceiptFeed().sample()
    assert feed['companies'][0]['symbol']=='CARERATING'
    assert feed['companies'][0]['period']=='2026-09'
    assert feed['jobs'][0]['duration_seconds']==30
    assert {p:p.read_bytes() for p in folder.iterdir()}==before


def test_outcome_arrival_invalidates_receipt_cache(receipts):
    folder=save_job(receipts,'recent',complete=False)
    source=terminal.ReceiptFeed()
    assert source.sample()['jobs'][0]['state']=='unknown'
    (folder/'outcome.json').write_text(json.dumps({'status':'PASS','finished_at_utc':'2026-10-22T18:00:30+00:00'}))
    assert source.sample()['jobs'][0]['state']=='completed'


@pytest.mark.parametrize('stage', ['stop', 'execution_failure'])
def test_blocker_reports_worker_error_from_matching_retained_job(receipts, stage):
    folder=save_job(receipts,'failed-financial')
    stopped={'exchange':'FINANCIAL','exit_code':2,
        'finished_at_utc':'2026-10-22T18:00:30+00:00'}
    (receipts/'FINANCIAL.stopped.json').write_text(json.dumps(stopped))
    with (folder/'runtime.log').open('a') as stream:
        stream.write(json.dumps({'stage':stage,
            'error':'PostgreSQL operation rejected (SQLSTATE 42883)'})+'\n')
    before=(receipts/'FINANCIAL.stopped.json').read_bytes()
    feed=terminal.ReceiptFeed().sample()
    assert feed['blockers']['FINANCIAL.stopped.json']['error']=='PostgreSQL operation rejected (SQLSTATE 42883)'
    assert (receipts/'FINANCIAL.stopped.json').read_bytes()==before


def test_old_unfinished_receipt_does_not_claim_live_worker(receipts):
    save_job(receipts,'old',complete=False)
    feed=terminal.ReceiptFeed().sample()
    output=render(feed,terminal.Inspector().snapshot())
    assert 'RUNNING' not in output and 'FETCHING ACTIVE' not in output
    assert 'receipt only' in output
    assert 'ETA' not in output and '%' not in output


def test_failed_inspection_preserves_timestamped_counts(receipts):
    inspector=terminal.Inspector()
    db={'queues':{'screener_company_queue':[{'state':'pending','n':42}]},'summary':{'companies':12}}
    inspector.accept({'observed_at_utc':'2026-10-22T18:00:00+00:00','quiescent_inspection':{'database':db}})
    inspector.accept({'observed_at_utc':'2026-10-22T18:02:00+00:00','inspection_errors':['active owner']})
    snapshot=inspector.snapshot()
    assert snapshot['database']==db and snapshot['database_at']=='2026-10-22T18:00:00+00:00'
    output=render(terminal.ReceiptFeed().sample(),snapshot)
    assert '42' in output and 'active owner' in output
    assert '23:30:00 IST' in output


def test_pause_beats_observed_running_and_errors_are_literal(receipts):
    terminal.lifecycle.PAUSE.write_text('{}')
    snapshot=terminal.Inspector().snapshot()
    snapshot['processes']=[{'container':'financial','state':'running'}]
    snapshot['raw']={'inspection_errors':['[red]literal diagnostic[/red]']}
    output=render(terminal.ReceiptFeed().sample(),snapshot)
    assert 'PAUSED' in output
    assert '[red]literal diagnostic[/red]' in output


def test_once_reports_deferred_quarter_without_docker(receipts, capsys):
    assert terminal.main(['monitor','--once','--json'])==1
    value=json.loads(capsys.readouterr().out)
    assert value['inspection']['raw'] is None
    assert value['inspection']['quarter_error']
    assert value['inspection']['quarter_data'] is None


def test_shell_exits_without_worker_or_web_start(receipts,monkeypatch):
    commands=iter(['/status','/exit'])
    monkeypatch.setattr('builtins.input',lambda *_:next(commands))
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    assert terminal.shell()==0


def test_document_view_has_queues_instead_of_company_feed(receipts):
    output=render(terminal.ReceiptFeed().sample(),terminal.Inspector().snapshot(),view='ratings')
    assert 'CREDIT-RATING QUEUE' in output
    assert 'RECENT COMPANY RESULTS' not in output


def test_new_cli_routes_do_not_invoke_legacy_docker(tmp_path):
    runner=tmp_path/'python3'
    runner.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    runner.chmod(0o755)
    env={'PATH':str(tmp_path)+':/usr/bin:/bin'}
    wrapper=terminal.ROOT/'ops/host/scraper_lifecycle.sh'
    for args,action in [([], 'shell'),(['monitor'],'monitor'),(['search','CARERATING'],'search'),
                        (['coverage'],'coverage'),(['bse'],'bse')]:
        result=subprocess.run(['/bin/bash',str(wrapper),*args],env=env,capture_output=True,text=True)
        assert result.returncode==0
        assert result.stdout.splitlines()[2]==action
    removed=subprocess.run(['/bin/bash',str(wrapper),'dashboard'],env=env,capture_output=True,text=True)
    assert removed.returncode==2 and 'removed' in removed.stderr


def test_census_mounts_renderer_outside_readonly_host_mount(monkeypatch):
    calls=[]
    def run(args, **kwargs):
        from types import SimpleNamespace
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps([{'Name':terminal.lifecycle.VOLUME,'Labels':{'com.docker.compose.project':'trading-ai'}}]) if args[:3]==['docker','volume','inspect'] else '{}')
    monkeypatch.setattr(terminal.lifecycle,'run',run)
    terminal.lifecycle.database_status('/host/acquisition_queries.py',('company_report','{}'))
    mounts=[calls[-1][i+1] for i,arg in enumerate(calls[-1]) if arg=='--mount']
    renderer=next(m for m in mounts if 'scrapper/view/render.py' in m)
    assert 'target=/acquisition_financial_renderer.py,readonly' in renderer
    assert 'target=/host/' not in renderer
    assert '--read-only' in calls[-1] and '--network=none' in calls[-1]


def test_small_terminal_keeps_drive_and_blockers_visible(receipts,monkeypatch):
    console=Console(width=80,height=24)
    monkeypatch.setattr(terminal,'Console',lambda *a,**kw:console)
    feed=terminal.ReceiptFeed().sample()
    feed['blockers']={'BSE.stopped.json':{'error':'SOURCE_BLOCKED'}}
    snapshot=terminal.Inspector().snapshot()
    stream=StringIO()
    Console(file=stream,width=80).print(terminal.render_monitor(feed,snapshot,compact=True))
    output=stream.getvalue()
    assert len(output.splitlines())<=24
    assert 'My Passport' in output and 'BSE.stopped.json' in output and 'Ctrl+C' in output


def test_quarterly_coverage_and_retry_meaning_are_visible(receipts):
    snapshot = terminal.Inspector().snapshot()
    snapshot['database'] = {'summary': {
        'quarterly_coverage': [{'period_end':'2026-09-30','reporting_scope':'standalone','companies':45}],
        'queue_diagnostics': [{'queue':'artifact_download_queue','unattempted':23,
            'retry_due':7,'retry_waiting':2,'reasons':[{'reason':'http_403','n':6}]}]}}
    output = render(terminal.ReceiptFeed().sample(), snapshot)
    assert 'Q2 FY2027' in output and '45' in output
    assert 'HTTP 403' in output and 'not an active retry' in output


def test_heartbeat_and_worker_are_distinct(receipts):
    feed = terminal.ReceiptFeed().sample()
    feed['tick']['checked_at_utc'] = datetime.now(timezone.utc).isoformat()
    snapshot = terminal.Inspector().snapshot()
    assert 'CHECKING' in render(feed, snapshot)
    snapshot.update(processes=[], processes_at=datetime.now(timezone.utc).isoformat())
    assert 'WAITING' in render(feed, snapshot)
    snapshot['processes'] = [{'container':'financial','state':'running'}]
    assert 'RUNNING' in render(feed, snapshot)


def test_company_suggestions_match_name_ticker_code_and_cache(monkeypatch):
    calls = []
    rows = [{'company_id':1,'symbol':'TVSMOTOR','current_legal_name':'TVS Motor Company','identifiers':'532343'},
            {'company_id':2,'symbol':'OFSS','current_legal_name':'Oracle Financial Services','identifiers':'532466'}]
    def load(action):
        calls.append(action)
        return {'data':{'companies':rows}}
    monkeypatch.setattr(terminal.observations.backend, 'company', load)
    catalog = terminal.CompanySuggestions()
    for term in ['tvs motor', 'TVSMOTOR', '532343', '1']:
        assert catalog.matches(term)[0]['company_id'] == 1
    assert catalog.matches('OFSS')[0]['company_id'] == 2
    assert calls == ['company_catalog']


def test_deferred_suggestions_do_not_query_on_every_keystroke(monkeypatch):
    calls = []
    def load(action):
        calls.append(action)
        return {'error':'active owner'}
    monkeypatch.setattr(terminal.observations.backend, 'company', load)
    catalog = terminal.CompanySuggestions()
    assert catalog.matches('t') == catalog.matches('tv') == []
    assert catalog.error == 'active owner' and len(calls) == 1


@pytest.mark.parametrize('command', ['/company', '/compa', '/comp'])
def test_completer_replaces_whole_name_with_unique_id(monkeypatch, command):
    from prompt_toolkit.document import Document
    from prompt_toolkit.completion import CompleteEvent
    catalog = terminal.CompanySuggestions()
    catalog.rows = [{'company_id':42,'symbol':'TVSMOTOR','current_legal_name':'TVS Motor Company'}]
    completer = terminal.shell_completer(catalog).completer
    values = list(completer.get_completions(Document(command + ' tvs motor'), CompleteEvent()))
    assert values[0].text == '42' and values[0].start_position == -9


def test_bare_search_is_helpful_without_argparse_dump(receipts, monkeypatch, capsys):
    commands = iter(['/search', '/company', '/exit'])
    monkeypatch.setattr('builtins.input', lambda *_:next(commands))
    monkeypatch.setattr(sys.stdin, 'isatty', lambda:False)
    assert terminal.shell() == 0
    captured = capsys.readouterr()
    assert 'TVSMOTOR' in captured.out and 'usage:' not in captured.err


def test_bse_explains_retained_validation_failure(receipts, monkeypatch, capsys):
    evidence = receipts/'browser'
    evidence.mkdir()
    monkeypatch.setattr(terminal.lifecycle.dispatcher, 'RECEIPTS', evidence)
    (evidence/'2026-10-22-evening.json').write_text(json.dumps({'response':{'status_code':200}}))
    (evidence/'2026-10-22-evening.raw').write_bytes(b'invalid dataset')
    # Even an unreadable retained response is diagnosed without opening a browser.
    assert terminal.show_bse(as_json=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['http_status'] == 200 and result['error'] and result['automatic_retry'] is False


def test_shell_expands_prefixes_and_explains_ambiguous_input(monkeypatch, capsys):
    commands = iter(['/compa TVSMOTOR', '/comp OFSS', '/mon --once', '/co', '/nonesuch', '/ex'])
    monkeypatch.setattr('builtins.input', lambda *_:next(commands))
    monkeypatch.setattr(sys.stdin, 'isatty', lambda:False)
    calls = []
    monkeypatch.setattr(terminal, 'main', lambda argv:calls.append(argv))
    assert terminal.shell() == 0
    assert calls == [['company','TVSMOTOR'], ['company','OFSS'], ['monitor','--once']]
    captured = capsys.readouterr()
    assert 'Keep typing: /coverage, /company' in captured.out
    assert 'Unknown command: /nonesuch' in captured.out
    assert not captured.err
