from datetime import datetime, timezone
import json
import sys

import pytest

import automatic_acquisition as dispatcher
import phase4_documents
import scraper_storage_growth as storage
from acquisition_failures import runtime_entrypoint
from paired_downloads import DownloadCompanion


def test_slow_inventory_publishes_new_measurement_and_keeps_growth_budget(tmp_path, monkeypatch):
    root = tmp_path / 'store'
    root.mkdir()
    (root / 'report.pdf').write_bytes(b'pdf')
    ledger = tmp_path / 'ledger.json'
    capacity = dict(root=str(root), volume_uuid='volume', physical_identity_sha256='identity',
                    free_bytes=1000, required_free_bytes=100, measured_at_utc='old')
    config = dict(activated_at_utc='2026-01-01T00:00:00+00:00', external_growth_limit_bytes=100)
    storage.account(root, ledger, config, capacity, bootstrap=True)
    (root / 'report.pdf').write_bytes(b'pdf appended')
    scanned = []
    inventory = storage.inventory
    def slow_inventory(path):
        scanned.append(True)
        return inventory(path)
    def fresh_preflight():
        assert scanned, 'Measurement must follow the potentially slow scan'
        return capacity | dict(measured_at_utc='fresh', free_bytes=900)
    monkeypatch.setattr(storage, 'inventory', slow_inventory)
    monkeypatch.setattr(phase4_documents, 'storage_preflight', fresh_preflight)
    growth, fresh = storage.account_and_refresh(root, ledger, config, capacity, reserve_bytes=20)
    assert fresh['measured_at_utc'] == 'fresh'
    assert fresh['free_bytes'] == 900
    assert growth['charged_bytes'] == len(b'pdf appended')
    assert growth['reserved_bytes'] == 20
    assert json.loads(ledger.read_text())['charged_bytes'] == len(b'pdf appended')


@pytest.mark.parametrize('change,match', [
    ({'volume_uuid': 'another'}, 'identity changed'),
    ({'physical_identity_sha256': 'another'}, 'identity changed'),
    ({'root': '/another'}, 'identity changed'),
    ({'free_bytes': 105}, 'Reserve cannot cover'),
])
def test_changed_device_or_reduced_capacity_blocks_after_inventory(tmp_path, monkeypatch, change, match):
    capacity = dict(root=str(tmp_path), volume_uuid='volume', physical_identity_sha256='identity',
                    free_bytes=1000, required_free_bytes=100)
    monkeypatch.setattr(storage, 'account', lambda *a, **kw: {})
    monkeypatch.setattr(phase4_documents, 'storage_preflight', lambda: capacity | change)
    with pytest.raises(RuntimeError, match=match):
        storage.account_and_refresh(tmp_path, tmp_path / 'ledger', {}, capacity, reserve_bytes=20)


def test_capacity_deferral_does_not_stop_financial_lane_or_replay_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, 'STATE', tmp_path)
    monkeypatch.setattr(dispatcher, 'verify_config', lambda: None)
    def operation(_, out):
        with DownloadCompanion([sys.executable, '-c', 'raise SystemExit(75)'],
                               log_path=out/'child.log', result_path=out/'result.json'):
            pass
    at = datetime.now(timezone.utc)
    result = dispatcher.run_slot('financial', 'FINANCIAL', at, execute=operation)
    assert result['status'] == 'DEFERRED_ENVIRONMENT'
    assert not (tmp_path/'FINANCIAL.stopped.json').exists()
    assert dispatcher.run_slot('financial', 'FINANCIAL', at, execute=operation)['status'] == 'slot_already_consumed'


def test_child_capacity_failure_uses_environment_exit_protocol():
    def child():
        raise RuntimeError('Document-store capacity file is stale: measured 824s ago')
    assert runtime_entrypoint(child) == 75


def test_unknown_companion_failure_still_stops_financial_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, 'STATE', tmp_path)
    monkeypatch.setattr(dispatcher, 'verify_config', lambda: None)
    def operation(_, out):
        with DownloadCompanion([sys.executable, '-c', 'raise SystemExit(2)'],
                               log_path=out/'child.log', result_path=out/'result.json'):
            pass
    result = dispatcher.run_slot('financial', 'FINANCIAL', datetime.now(timezone.utc), execute=operation)
    assert result['status'] == 'STOP'
    assert (tmp_path/'FINANCIAL.stopped.json').exists()


def test_financial_entrypoint_preserves_companion_deferral(tmp_path, monkeypatch):
    import step3_financial_runtime as financial
    def operation():
        with DownloadCompanion([sys.executable, '-c', 'raise SystemExit(75)'],
                               log_path=tmp_path/'child.log', result_path=tmp_path/'result.json'):
            pass
    monkeypatch.setattr(financial, 'main', operation)
    assert financial.entrypoint() == 75


def test_financial_entrypoint_preserves_unknown_critical_failure(monkeypatch):
    import step3_financial_runtime as financial
    def operation():
        raise RuntimeError('Physical document identity changed')
    monkeypatch.setattr(financial, 'main', operation)
    assert financial.entrypoint() == 2
