"""Exchange recovery regressions; no production access or network calls."""
from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from requests.exceptions import ReadTimeout
import automatic_acquisition as schedule
import nse_results_runtime as runtime


def test_timeout_defers_once_and_other_failures_propagate(capsys):
    target = 'scrapper.sources.exchange.transport.fetch_exchange_recovery_surface'
    with patch(target, side_effect=ReadTimeout('slow source')) as fetch:
        assert runtime.fetch_nse_response('https://www.nseindia.com/api/test', '2026-09') is None
        fetch.assert_called_once()
    assert json.loads(capsys.readouterr().out)['stage'] == 'source_transport_deferred'
    with patch(target, side_effect=RuntimeError('invalid source')):
        with pytest.raises(RuntimeError, match='invalid source'):
            runtime.fetch_nse_response('https://www.nseindia.com/api/test', '2026-09')


def test_nse_deferral_preserves_slot_and_allows_next_day(tmp_path):
    calls = []
    def execute(*_):
        calls.append(True)
        return 75 if len(calls) == 1 else 0
    at = datetime.fromisoformat('2026-10-31T19:30:00+05:30')
    later = datetime.fromisoformat('2026-11-02T19:30:00+05:30')
    with patch.object(schedule, 'STATE', tmp_path), patch.object(schedule, 'verify_config'):
        assert schedule.run_slot('nse-primary', 'NSE', at, execute=execute)['status'] == 'DEFERRED_ENVIRONMENT'
        assert not (tmp_path / 'NSE.stopped.json').exists()
        assert schedule.run_slot('nse-primary', 'NSE', at, execute=execute)['status'] == 'slot_already_consumed'
        assert len(calls) == 1
        assert schedule.run_slot('nse-primary', 'NSE', later, execute=execute)['status'] == 'PASS'


def test_bse_mounts_and_verifies_applied_migration_offline(tmp_path):
    (tmp_path / 'checks.json').write_text(json.dumps({'application_hashes': {}, 'operator_hashes': {}}))
    with patch('production_ownership.owner_gate', return_value={}), \
            patch.object(schedule.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
        assert schedule.ingest_bse_capture(tmp_path, 'fixture-image') == 0
    config = json.loads((tmp_path / 'compose.json').read_text())
    service = config['services']['nse-results']
    migration = 'scrapper/db/migrations/076_retire_crisil_icra_terms_holds.sql'
    mounts = [m for m in service['volumes'] if m.get('target') == '/app/' + migration]
    assert len(mounts) == 1 and mounts[0]['read_only']
    assert mounts[0]['bind']['create_host_path'] is False
    assert service['network_mode'] == 'none'
    checks = json.loads((tmp_path / 'checks.json').read_text())
    assert checks['application_hashes'][migration] == schedule.verify_config()['files'][migration]
