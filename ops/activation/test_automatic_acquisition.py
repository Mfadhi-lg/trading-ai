"""Focused isolated scheduling verification; no live source or production DB."""
from datetime import datetime
import fcntl
import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location('automatic_acquisition', Path(__file__).with_name('automatic_acquisition.py'))
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


def test_ist_slots_and_no_backlog():
    for instant, names in [
        ('2026-10-09T03:30:30+00:00', ['bse-morning']),
        ('2026-10-09T12:30:30+00:00', ['bse-evening']),
        ('2026-10-09T14:00:30+00:00', ['nse-primary']),
        ('2026-10-09T14:01:00+00:00', []),
        ('2026-10-11T14:00:00+00:00', []),
        ('2026-10-09T15:30:00+00:00', ['market-cap']),
        ('2026-10-09T00:30:00+00:00', ['market-cap']),
    ]:
        # Document slots have their own evolving schedule and tests.
        with patch.object(schedule, 'document_jobs', return_value=[]):
            assert [row[0] for row in schedule.due(datetime.fromisoformat(instant))] == names
    with pytest.raises(ValueError):
        schedule.due(datetime(2026, 10, 9))


@pytest.mark.parametrize('code', [0, 2])
def test_scheduled_execution_records_and_consumes_one_slot(tmp_path, code):
    calls = []
    at = datetime.fromisoformat('2026-10-09T09:00:00+05:30')
    def operation(exchange, out):
        calls.append(exchange)
        (out / 'runtime.log').write_text('source_rows=8 inserted_filings=0 queue_reused=8')
        return code
    with patch.object(schedule, 'STATE', tmp_path), patch.object(schedule, 'verify_config'):
        result = schedule.run_slot('bse-morning', 'BSE', at, execute=operation)
        assert result['status'] == ('PASS' if code == 0 else 'STOP')
        assert schedule.run_slot('bse-morning', 'BSE', at, execute=operation)['status'] == 'slot_already_consumed'
        assert calls == ['BSE']
        assert (tmp_path / '2026-10-09-bse-morning/outcome.json').exists()
        assert (tmp_path / 'BSE.stopped.json').exists() == bool(code)
        if code:
            schedule.run_slot('bse-evening', 'BSE', at, execute=operation)
            assert calls == ['BSE']  # No retry on the next clock tick/slot either.


def test_interrupted_attempt_is_never_replayed(tmp_path):
    at = datetime.fromisoformat('2026-10-09T18:00:00+05:30')
    (tmp_path / '2026-10-09-bse-evening').mkdir()
    with patch.object(schedule, 'STATE', tmp_path), patch.object(schedule, 'verify_config'):
        assert schedule.run_slot('bse-evening', 'BSE', at,
            execute=lambda *_: pytest.fail('Interrupted slot replayed'))['status'] == 'slot_already_consumed'


def test_lost_financial_claim_consumes_slot_without_permanent_stop(tmp_path):
    at = datetime.fromisoformat('2026-10-24T19:15:00+05:30')
    later = datetime.fromisoformat('2026-10-24T19:45:00+05:30')
    calls = []
    def operation(*_):
        calls.append(True)
        return 75 if len(calls) == 1 else 0
    with patch.object(schedule, 'STATE', tmp_path), patch.object(schedule, 'verify_config'):
        result = schedule.run_slot('current-financial', 'FINANCIAL', at, execute=operation)
        assert result['status'] == 'DEFERRED_ENVIRONMENT'
        assert not (tmp_path / 'FINANCIAL.stopped.json').exists()
        assert schedule.run_slot('current-financial', 'FINANCIAL', at, execute=operation)['status'] == 'slot_already_consumed'
        assert len(calls) == 1
        assert schedule.run_slot('current-financial', 'FINANCIAL', later, execute=operation)['status'] == 'PASS'


def test_only_claim_loss_defers_financial_runtime(capsys):
    import step3_financial_runtime as runtime
    from scrapper.db.document_work_claims import OwnershipLost
    with patch.object(runtime, 'main', side_effect=OwnershipLost('expired')):
        assert runtime.entrypoint() == 75
    assert 'claim_deferred' in capsys.readouterr().out
    with patch.object(runtime, 'main', side_effect=RuntimeError('authentication rejected')):
        assert runtime.entrypoint() == 2
    assert '"stage": "stop"' in capsys.readouterr().out


def test_legacy_browser_receipt_skips_without_launch_or_stop_latch(tmp_path):
    at = datetime.fromisoformat('2026-10-09T18:00:00+05:30')
    def consumed(*_):
        raise schedule.SlotConsumed('Original failed daily receipt preserved')
    with patch.object(schedule, 'STATE', tmp_path), patch.object(schedule, 'verify_config'):
        result = schedule.run_slot('bse-evening', 'BSE', at, execute=consumed)
        assert result['status'] == 'SKIPPED_ALREADY_ATTEMPTED'
        assert not (tmp_path / 'BSE.stopped.json').exists()


def test_runtime_counts_and_queue_decisions_are_recorded(tmp_path):
    import json
    (tmp_path / 'runtime.log').write_text(json.dumps({'stage':'outcome', 'query_period':'2026-09',
        'result':{'accepted_row_count':8, 'filings':{'stored':3, 'duplicates':5},
            'enqueue':{'queued':1, 'reused':2}, 'snapshot_id':7, 'fetch_id':9}})+'\n')
    summary = schedule.summarize_runtime(tmp_path)
    assert summary['source_rows'] == 8 and summary['inserted_filings'] == 3
    assert summary['periods'][0]['queue_decisions'] == {'queued':1, 'reused':2}


def test_dispatch_admission_lock_prevents_second_scheduler(tmp_path):
    with patch.object(schedule, 'HERE', tmp_path), patch.object(schedule, 'STATE', tmp_path):
        with (tmp_path / '.scheduler-admission.lock').open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert schedule.tick()['status'] == 'busy'


def test_both_adapters_share_the_same_writer_lock():
    host = Path(__file__).with_name('nse_results.py').read_text()
    assert "HERE / '.nse-owner.lock'" in host
    assert "HERE / '.nse-owner.lock'" in Path(schedule.__file__).read_text()
    from scrapper.operations.coordinator.exchange import ownership
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / 'production.db'
        with ownership(db, 'executor') as first:
            assert first
            with ownership(db, 'executor') as second:
                assert not second
