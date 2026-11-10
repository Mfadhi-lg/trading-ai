"""Synthetic receipts only; no archive, database, or provider access."""
import json

import pytest

from candle_monitor import CandleFeed
from test_unified_monitor import snapshot, output


def save(root, count=1200):
    (root / 'full-inventory.json').write_text(json.dumps(dict(
        created_at='2026-11-08T13:49:51+00:00', expected_companies=2,
        companies_with_all_four_planned_native_histories=1,
        timeframes=[dict(company_id=1, timeframe='Daily', source_rows_returned=count,
                        expected_requests=3, successful_requests=2, failed_requests=1),
                    dict(company_id=1, timeframe='Weekly', source_rows_returned=200,
                        expected_requests=1, successful_requests=1, failed_requests=0),
                    dict(company_id=2, timeframe='Daily', source_rows_returned=0,
                        expected_requests=3, successful_requests=0, failed_requests=0)])))
    (root / 'full-campaign.json').write_text('{"status":"PAUSED"}')


def test_counts_refresh_without_writes_or_double_counting_companies(tmp_path):
    save(tmp_path)
    feed = CandleFeed(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    result = feed.sample()
    assert result['source_rows'] == 1400
    assert result['companies_with_history'] == 1
    assert result['successful_requests'] == 3
    assert result['expected_requests'] == 7
    assert result['failed_requests'] == 1
    assert result['campaign_status'] == 'PAUSED'
    assert feed.sample() == result
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    save(tmp_path, 3000)
    assert feed.sample()['source_rows'] == 3200
    (tmp_path / 'full-campaign.json').write_text('{"status":"RUNNING"}')
    assert feed.sample()['campaign_status'] == 'RUNNING'


@pytest.mark.parametrize('raw', [None, '{', '{}', '[]', '{"timeframes":null}'])
def test_unavailable_receipts_do_not_report_zero(tmp_path, raw):
    save(tmp_path)
    feed = CandleFeed(tmp_path)
    assert feed.sample()['available']
    if raw is None:
        (tmp_path / 'full-inventory.json').unlink()
    else:
        (tmp_path / 'full-inventory.json').write_text(raw)
    assert feed.sample() == {'available': False}


def test_monitor_shows_lifetime_progress_and_snapshot_age(tmp_path):
    save(tmp_path)
    feed, inspection = snapshot()
    feed['historical_candles'] = CandleFeed(tmp_path).sample()
    result = output(feed, inspection, width=160)
    for value in ('1,400 source rows', '1/2 complete (50.0%)', 'requests 3/7 OK',
                  '1 failed', 'Daily 1,200', 'PAUSED', '08 Nov', 'batch snapshot'):
        assert value in result
    compact = output(feed, inspection, width=80, height=24, compact=True)
    assert len(compact.splitlines()) <= 24
    for value in ('Candles', '50.0%', 'batch snapshot', 'Ctrl+C', 'Financials', 'Processing'):
        assert value in compact
    inspection['quarter_data'] = None
    assert '1,400 source rows' in output(feed, inspection, view='details')


def test_terminal_feed_includes_refreshed_candle_summary(tmp_path, monkeypatch):
    import acquisition_terminal as terminal
    import candle_monitor

    save(tmp_path)
    monkeypatch.setattr(candle_monitor, 'STATE', tmp_path)
    monkeypatch.setattr(terminal.lifecycle.dispatcher, 'STATE', tmp_path)
    monkeypatch.setattr(terminal.lifecycle, 'PAUSE', tmp_path / 'paused.json')
    monkeypatch.setattr(terminal.lifecycle, 'schedule_status', lambda: {'jobs': []})
    feed = terminal.ReceiptFeed()
    assert feed.sample()['historical_candles']['source_rows'] == 1400
    save(tmp_path, 3000)
    assert feed.sample()['historical_candles']['source_rows'] == 3200


def test_verification_progress_refreshes_between_inventory_batches(tmp_path):
    from candle_monitor import summary_lines
    save(tmp_path)
    (tmp_path / 'full-campaign.json').write_text(json.dumps(dict(
        status='RUNNING', phase='NATIVE', started_at='2026-11-10T05:35:08+00:00')))
    execution = dict(stage='VERIFYING_SAVED_DATA', requests_checked=12, manifest_requests=20,
                     updated_at='2026-11-10T05:36:00+00:00',
                     runs=[dict(started_at='2026-11-10T05:35:09+00:00', reused=12)])
    path = tmp_path / 'full-execution.json'
    path.write_text(json.dumps(execution))
    feed = CandleFeed(tmp_path)
    result = feed.sample()
    assert result['activity']['checked'] == 12
    assert result['successful_requests'] == 3
    assert 'verifying saved data' in '\n'.join(summary_lines(result, str))
    assert '12/20 checked' in '\n'.join(summary_lines(result, str, short=True))
    assert 'batch snapshot' in '\n'.join(summary_lines(result, str, short=True))
    dashboard, inspection = snapshot()
    dashboard['historical_candles'] = result
    compact = output(dashboard, inspection, width=80, height=24, compact=True)
    assert 'batch snapshot' in compact
    assert 'Ctrl+C' in compact
    execution['requests_checked'] = execution['runs'][0]['reused'] = 13
    path.write_text(json.dumps(execution))
    assert feed.sample()['activity']['checked'] == 13
    execution['runs'][0]['started_at'] = '2026-11-08T00:00:00+00:00'
    path.write_text(json.dumps(execution))
    assert 'activity' not in feed.sample()
    path.write_text('{')
    assert 'activity' not in feed.sample()
