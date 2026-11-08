"""Exercise the combined display with independent census and receipt evidence."""
from datetime import datetime, timezone
from io import StringIO

import pytest
from rich.console import Console

import acquisition_terminal as terminal
from acquisition_quarter_view import render
from acquisition_monitor_view import financial_rows


def snapshot():
    now = datetime.now(timezone.utc).isoformat()
    feed = {'paused': False, 'tick': {'checked_at_utc': now}, 'blockers': {},
            'schedule': {'jobs': []}, 'jobs': [], 'companies': [
                {'symbol': 'RECENT', 'period': '2026-09', 'scope': 'standalone',
                 'status': 'accepted', 'updated_at': now},
                {'symbol': 'WRONG_QUARTER', 'period': '2026-06', 'scope': 'standalone',
                 'status': 'accepted', 'updated_at': now}]}
    inspection = {'quarter': '2026-09', 'quarter_at': now, 'processes': [], 'processes_at': now,
                  'quarter_data': {'queues': {
                      'screener_company_queue': [{'state': 'pending', 'n': 42}, {'state': 'running', 'n': 3}],
                      'artifact_download_queue': [{'state': 'SUCCEEDED', 'n': 12}],
                      'credit_rating_download_queue': [{'state': 'INTERACTION_REQUIRED', 'n': 7}]},
                      'coverage': {'standalone': 10, 'consolidated': 8, 'both_scopes': 6},
                      'processing': [{'state': 'ok', 'n': 12}],
                      'downloads': [{'current_legal_name': '[red]Literal[/red]', 'artifact_type': 'credit_rating',
                                     'status': 'INTERACTION_REQUIRED', 'extraction_status': 'not_started'}]}}
    return feed, inspection


def output(feed, inspection, width=140, height=50, compact=False, view='all'):
    stream = StringIO()
    Console(file=stream, width=width, height=height).print(render(feed, inspection, view, compact))
    return stream.getvalue()


def test_all_views_share_financial_download_rating_and_processing_data():
    feed, inspection = snapshot()
    expected = output(feed, inspection)
    assert output(feed, inspection, view='ratings') == expected
    assert output(feed, inspection, view='downloads') == expected
    for text in ('Financials', 'Downloads', 'Ratings', 'Processing', 'RECENT', 'Needs interaction', '[red]Literal[/red]'):
        assert text in expected
    assert 'WRONG_QUARTER' not in expected
    assert 'WAITING' in expected and 'RUNNING ·' not in expected


def test_receipt_outcomes_survive_deferred_census_without_global_totals():
    feed, inspection = snapshot()
    inspection.update(quarter_data=None, quarter_error='active owner')
    inspection['database'] = {'queues': {'screener_company_queue': [{'state': 'pending', 'n': 999999}]}}
    result = output(feed, inspection)
    assert 'RECENT' in result and 'active owner' in result
    assert '999,999' not in result and 'no lifetime counts substituted' in result


def test_small_terminal_keeps_every_pipeline_and_controls_visible():
    feed, inspection = snapshot()
    inspection['quarter_data']['acquisition'] = {'unknown_period_documents_for_quarter_companies': 82278}
    feed['blockers'] = {'DOCUMENT_RATING.stopped.json': {'error': 'Browser closed'}}
    result = output(feed, inspection, width=80, height=24, compact=True)
    assert len(result.splitlines()) <= 24
    for text in ('Financials', 'Downloads', 'Ratings', 'Processing', 'RECENT', 'My Passport', 'NEEDS ATTENTION', 'Ctrl+C'):
        assert text in result
    assert 'ETA' not in result


def test_worker_claim_requires_a_process_observation():
    feed, inspection = snapshot()
    inspection.update(processes=None, processes_at=None)
    assert 'CHECKING' in output(feed, inspection)
    inspection.update(processes=[{'container': 'financial-adapter'}], processes_at=datetime.now(timezone.utc).isoformat())
    assert 'RUNNING ·' in output(feed, inspection)
    feed['paused'] = True
    assert 'PAUSED' in output(feed, inspection)


def test_newer_census_outcome_replaces_an_old_receipt_for_the_same_job():
    feed, inspection = snapshot()
    feed['companies'][0].update(status='failed_retryable', updated_at='2026-10-01T00:00:00+00:00')
    inspection['quarter_data']['financial_jobs'] = [{
        'company_path_normalized': '/company/RECENT/', 'result_period': '2026-09',
        'reporting_scope': 'standalone', 'status': 'accepted',
        'updated_at_utc': '2026-10-31T00:00:00+00:00'}]
    rows = financial_rows(feed, inspection['quarter_data'], '2026-09')
    assert len(rows) == 1 and rows[0][3] == 'accepted' and rows[0][5] == 'census'


def test_monitor_routes_to_the_combined_view(monkeypatch):
    assert terminal.resolve_shell_command('/monitor') == 'monitor'
    calls = []
    monkeypatch.setattr(terminal, 'run_monitor', lambda *a, **kw: calls.append((a, kw)) or 0)
    assert terminal.main(['monitor', '--quarter', '2026-09', '--once']) == 0
    assert calls[0][0][4] == 'monitor'
    assert calls[0][1]['quarter'] == '2026-09'


@pytest.mark.parametrize('command', ['downloads', 'ratings', 'rating'])
def test_redundant_commands_are_removed(command):
    import subprocess
    with pytest.raises(ValueError, match='Unknown command'):
        terminal.resolve_shell_command('/' + command)
    assert '/' + command not in terminal.COMMANDS
    result = subprocess.run(['/bin/bash', str(terminal.ROOT / 'ops/host/scraper_lifecycle.sh'), command],
                            capture_output=True, text=True, env={'PATH': '/usr/bin:/bin'})
    assert result.returncode == 2 and 'Use ./scraper monitor' in result.stderr
