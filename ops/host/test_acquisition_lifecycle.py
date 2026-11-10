"""Isolated operator/queue safety tests. No production subprocess is invoked."""
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import subprocess
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acquisition_lifecycle as lifecycle
from install_lifecycle_gate import prepare_source


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    activation = tmp_path / 'activation'
    activation.mkdir()
    for name in ('.scheduler-admission.lock', '.nse-owner.lock'):
        (activation / name).touch()
    monkeypatch.setattr(lifecycle, 'STATE', state)
    monkeypatch.setattr(lifecycle, 'PAUSE', state / 'lifecycle-paused.json')
    monkeypatch.setattr(lifecycle, 'PREVIOUS', state / 'previous.json')
    monkeypatch.setattr(lifecycle, 'ACTIVATION', activation)
    # Synthetic prerequisites: never read the developer's credentials or rely
    # on a macOS desktop/Chrome installation in an isolated checkout or CI.
    monkeypatch.setattr(lifecycle, 'ROOT', tmp_path)
    (tmp_path / '.screener_session').write_text('synthetic-test-session')
    original_stat = lifecycle.os.stat
    def fixture_stat(path, *args, **kwargs):
        if str(path) == '/dev/console':
            return SimpleNamespace(st_uid=lifecycle.os.getuid())
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(lifecycle.os, 'stat', fixture_stat)
    original_is_file = Path.is_file
    def fixture_is_file(path):
        if str(path) == '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':
            return True
        return original_is_file(path)
    monkeypatch.setattr(Path, 'is_file', fixture_is_file)
    import nse_results
    monkeypatch.setattr(nse_results, 'check_configuration', lambda: {'oci_index_digest': 'fixture-index'})
    monkeypatch.setattr(lifecycle, 'config', lambda: {'manifest': 'reviewed'})
    monkeypatch.setattr(lifecycle, 'loaded', lambda: True)
    monkeypatch.setattr(lifecycle, 'quiescent', lambda: {'database': {'leases': {'status': 'PASS'}}})
    monkeypatch.setattr(lifecycle, 'external_handles', lambda: [])
    monkeypatch.setattr(lifecycle, 'schedule_status', lambda: {'document_window_expired': True})
    monkeypatch.setattr(lifecycle, 'missed_slots', lambda *_: [{'missed_slots': 3, 'replayed': False}])
    monkeypatch.setattr(lifecycle,'candles',SimpleNamespace(
        pause=lambda **_: {'status':'PAUSED','workers_drained':True},
        wake=lambda: {'status':'RUNNING'},status=lambda: {'status':'IDLE'}))
    calls = []
    monkeypatch.setattr(lifecycle, 'run', lambda args, **_: calls.append(args) or SimpleNamespace(stdout='{}'))
    import phase4_documents
    monkeypatch.setattr(phase4_documents, 'storage_preflight', lambda: {'free_bytes': 400 * 1024**3})
    return calls


def test_shutdown_wake_repeated_and_no_legacy_restore(isolated):
    legacy = lifecycle.STATE / '.scraper_shutdown_state'
    legacy.write_text('trading-ai-daemon-1\n@browser-rating-icra\n')
    first = lifecycle.shutdown(0)
    previous = lifecycle.PREVIOUS.read_bytes()
    assert first['safe_to_shut_down_mac'] and not first['safe_to_disconnect_drive']
    assert lifecycle.shutdown(0)['status'] == 'PASS'
    assert lifecycle.PREVIOUS.read_bytes() == previous
    wake = lifecycle.wake()
    assert wake['schedule']['document_window_expired']
    assert not wake['replayed'] and wake['missed_schedule_opportunities'][0]['missed_slots'] == 3
    assert lifecycle.wake()['already_awake']
    assert legacy.read_text().startswith('trading-ai-daemon-1')
    assert not any(c[:2] in (['docker', 'start'], ['docker', 'stop'], ['docker', 'compose']) for c in isolated)


def test_candles_drain_before_shutdown_quiescence(isolated,monkeypatch):
    events=[]
    monkeypatch.setattr(lifecycle.candles,'pause',lambda **_:events.append('candles_drained') or {'status':'PAUSED'})
    monkeypatch.setattr(lifecycle,'quiescent',lambda:events.append('shared_quiescent') or {})
    assert lifecycle.shutdown(1)['historical_candles']['status']=='PAUSED'
    assert events==['candles_drained','shared_quiescent']


def test_candle_shutdown_blocker_never_claims_safe(isolated,monkeypatch):
    def blocked(**_):raise RuntimeError('Candle worker still writing')
    monkeypatch.setattr(lifecycle.candles,'pause',blocked)
    with pytest.raises(RuntimeError,match='Candle worker still writing'):lifecycle.shutdown(0)
    assert lifecycle.PAUSE.exists()
    assert not (lifecycle.STATE/'shutdown-verification.json').exists()


def test_candle_wake_failure_recloses_shared_admissions(isolated,monkeypatch):
    lifecycle.shutdown(0)
    def blocked():raise RuntimeError('Wrong candle Passport identity')
    monkeypatch.setattr(lifecycle.candles,'wake',blocked)
    with pytest.raises(RuntimeError,match='Wrong candle Passport identity'):lifecycle.wake()
    assert lifecycle.PAUSE.exists()


def test_already_running_candle_wake_does_not_require_its_writer_to_be_idle(isolated,monkeypatch):
    monkeypatch.setattr(lifecycle.candles,'status',lambda:{'status':'RUNNING','workers':{'acquisition':{'pid':123}}})
    def active():raise AssertionError('Already-awake owner must not drain itself')
    monkeypatch.setattr(lifecycle,'quiescent',active)
    assert lifecycle.wake()['already_awake']


@pytest.mark.parametrize('owner_lock', ['.scheduler-admission.lock', '.nse-owner.lock'])
def test_already_awake_without_candles_does_not_drain_active_owner(isolated, monkeypatch, owner_lock):
    monkeypatch.setattr(lifecycle.candles, 'status', lambda: {'status': 'NOT_CONFIGURED'})
    def unexpected_start():
        pytest.fail('An already-awake dispatcher must not start candle workers')
    monkeypatch.setattr(lifecycle.candles, 'wake', unexpected_start)
    with (lifecycle.ACTIVATION / owner_lock).open('rb') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = lifecycle.wake()
    assert result['status'] == 'PASS' and result['already_awake']
    assert not lifecycle.PAUSE.exists()
    assert not any(c[:2] == ['launchctl', 'bootstrap'] for c in isolated)


def test_wake_uses_sealed_local_image_without_repository_alias(isolated, monkeypatch):
    import nse_results
    digest = 'sha256:' + 'a' * 64
    monkeypatch.setattr(nse_results, 'check_configuration', lambda: {'oci_index_digest': digest})
    original_run = lifecycle.run

    def no_repository_alias(args, **kwargs):
        if args[:3] == ['docker', 'image', 'inspect']:
            assert args[-1] == digest
            assert not any('@sha256:' in arg for arg in args)
        return original_run(args, **kwargs)

    monkeypatch.setattr(lifecycle, 'run', no_repository_alias)
    lifecycle.shutdown(0)
    assert lifecycle.wake()['status'] == 'PASS'


@pytest.mark.parametrize('blocker', ['writable handle', 'Unexpired production acquisition lease', 'Nonempty WAL'])
def test_shutdown_blocker_retains_pause_and_never_claims_safe(isolated, monkeypatch, blocker):
    def blocked():
        raise RuntimeError(blocker)
    monkeypatch.setattr(lifecycle, 'quiescent', blocked)
    with pytest.raises(RuntimeError, match=blocker):
        lifecycle.shutdown(0)
    assert lifecycle.PAUSE.exists() and lifecycle.PREVIOUS.exists()
    assert not (lifecycle.STATE / 'shutdown-verification.json').exists()


def test_shutdown_active_work_finishes_without_kill(isolated):
    entered = threading.Event()
    finished = threading.Event()
    queue = lifecycle.STATE / 'queue.json'
    queue.write_text('{"unprocessed": 4, "committed": 1}')
    original = queue.read_bytes()
    def bounded():
        with (lifecycle.ACTIVATION / '.scheduler-admission.lock').open('rb') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            entered.set()
            while not lifecycle.PAUSE.exists():
                time.sleep(.01)
            time.sleep(.03)
            finished.set()
    thread = threading.Thread(target=bounded)
    thread.start()
    assert entered.wait(2)
    try:
        result = lifecycle.shutdown(2)
        assert finished.is_set() and result['status'] == 'PASS'
        assert queue.read_bytes() == original
    finally:
        thread.join(2)
    assert not any('kill' in ' '.join(c) or c[:2] == ['docker', 'stop'] for c in isolated)


def test_active_timeout_fails_with_admissions_closed(isolated):
    with (lifecycle.ACTIVATION / '.scheduler-admission.lock').open('rb') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='not drained'):
            lifecycle.shutdown(0)
    assert lifecycle.PAUSE.exists()


@pytest.mark.parametrize('reason', ['External drive identity changed', 'Document sentinel missing', 'Physical reserve cannot cover canary'])
def test_wake_bad_storage_never_launches(isolated, monkeypatch, reason):
    lifecycle.shutdown(0)
    import phase4_documents
    def blocked():
        raise RuntimeError(reason)
    monkeypatch.setattr(phase4_documents, 'storage_preflight', blocked)
    isolated.clear()
    with pytest.raises(RuntimeError, match=reason):
        lifecycle.wake()
    assert isolated == [] and lifecycle.PAUSE.exists()


def test_wake_duplicate_owner_refused(isolated):
    lifecycle.shutdown(0)
    with (lifecycle.ACTIVATION / '.scheduler-admission.lock').open('rb') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='not drained'):
            lifecycle.wake()
    assert lifecycle.PAUSE.exists()
    assert not any(c[:2] == ['launchctl', 'bootstrap'] for c in isolated)


def test_wake_changed_configuration_refused(isolated, monkeypatch):
    lifecycle.shutdown(0)
    monkeypatch.setattr(lifecycle, 'config', lambda: {'manifest': 'changed'})
    with pytest.raises(RuntimeError, match='changed'):
        lifecycle.wake()
    assert lifecycle.PAUSE.exists()


def test_wake_receipt_failure_recloses_admissions(isolated, monkeypatch):
    lifecycle.shutdown(0)
    original_atomic = lifecycle.atomic
    def fail_receipt(path, value):
        if path.name == 'wake-verification.json':
            raise OSError('fixture destination unavailable')
        return original_atomic(path, value)
    monkeypatch.setattr(lifecycle, 'atomic', fail_receipt)
    with pytest.raises(OSError, match='fixture destination unavailable'):
        lifecycle.wake()
    assert lifecycle.PAUSE.exists()


def test_wake_only_restores_authorized_label(isolated, monkeypatch):
    lifecycle.shutdown(0)
    loaded = iter([False, True])
    monkeypatch.setattr(lifecycle, 'loaded', lambda: next(loaded))
    assert lifecycle.wake()['status'] == 'PASS'
    boots = [c for c in isolated if c[:2] == ['launchctl', 'bootstrap']]
    assert len(boots) == 1 and boots[0][-1] == str(lifecycle.PLIST)


def test_unloaded_dispatcher_without_state_cannot_be_started(isolated, monkeypatch):
    monkeypatch.setattr(lifecycle, 'loaded', lambda: False)
    with pytest.raises(RuntimeError, match='No current lifecycle authorization'):
        lifecycle.wake()
    assert not any(c[:2] == ['launchctl', 'bootstrap'] for c in isolated)


def test_dispatcher_gate_preserves_receipts_and_ignores_due_jobs(tmp_path, monkeypatch):
    source = Path(lifecycle.dispatcher.__file__).read_text()
    if 'lifecycle-paused.json' not in source:
        source = prepare_source(source)
    module = SimpleNamespace(__file__=str(Path(lifecycle.dispatcher.__file__)))
    namespace = vars(module)
    exec(compile(source, module.__file__, 'exec'), namespace)
    namespace['STATE'] = tmp_path
    namespace['HERE'] = tmp_path
    (tmp_path / 'lifecycle-paused.json').write_text('{}')
    receipt = tmp_path / 'prior-receipt.json'
    receipt.write_text('{"committed": true}')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert namespace['tick']()['status'] == 'lifecycle_paused'
    assert namespace['run_slot']('job', 'NSE', datetime.now(timezone.utc))['status'] == 'lifecycle_paused'
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name != '.scheduler-admission.lock'} == before


def test_status_read_only_receipts_and_paused_state(isolated, monkeypatch):
    monkeypatch.setattr(lifecycle, 'receipts', lambda: {'last_successful_batch': {'job': 'document-download'}})
    monkeypatch.setattr(lifecycle, 'containers', lambda: [])
    before = {str(p): p.read_bytes() for p in lifecycle.STATE.rglob('*') if p.is_file()}
    result = lifecycle.status()
    assert result['status'] == 'PASS'
    assert result['receipts']['last_successful_batch']['job'] == 'document-download'
    assert {str(p): p.read_bytes() for p in lifecycle.STATE.rglob('*') if p.is_file()} == before
    assert not any(c[:2] in (['launchctl', 'bootstrap'], ['docker', 'start'], ['docker', 'stop']) for c in isolated)


@pytest.mark.parametrize('alias,target', [('status', 'status'), ('wake', 'wake'), ('shutdown', 'shutdown'),
    ('start', 'wake'), ('resume', 'wake'), ('unpause', 'wake'), ('stop', 'shutdown'), ('pause', 'shutdown')])
def test_shell_shortcuts_target_current_lifecycle_only(tmp_path, alias, target):
    interpreter = tmp_path / 'python3'
    interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    interpreter.chmod(0o755)
    result = subprocess.run(['/bin/bash', str(lifecycle.ROOT / 'ops/host/scraper_lifecycle.sh'), alias],
        env={'PATH': str(tmp_path) + ':/usr/bin:/bin'}, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ['-B', 'ops/host/run_acquisition_lifecycle.py', target]


def test_shell_legacy_continuous_command_refused():
    result = subprocess.run(['/bin/bash', str(lifecycle.ROOT / 'ops/host/scraper_lifecycle.sh'), 'exchange-scheduler'],
        capture_output=True, text=True)
    assert result.returncode == 2 and 'Refused' in result.stderr


def test_immutable_database_census_keeps_queue_and_sidecars(tmp_path, monkeypatch):
    import sqlite3
    import acquisition_db_status as reader
    path = tmp_path / 'fixture.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE filing_queue (status TEXT, lease_until_utc TEXT)')
    conn.executemany('INSERT INTO filing_queue VALUES (?,?)', [('PENDING', None),
        ('RUNNING', '2099-01-01T00:00:00+00:00'), ('DONE', None)])
    conn.commit()
    conn.close()
    before = path.read_bytes()
    monkeypatch.setattr(reader, 'assert_safe_empty_wal', lambda _: {'files': {}})
    monkeypatch.setattr(reader, 'snapshot', lambda _: {})
    result = reader.census(path)
    assert result['leases']['status'] == 'BLOCKED'
    assert result['queues']['filing_queue'] == [{'state': 'DONE', 'n': 1},
        {'state': 'PENDING', 'n': 1}, {'state': 'RUNNING', 'n': 1}]
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_duplicate_dispatcher_plist_is_refused(tmp_path, monkeypatch):
    import plistlib
    installed = tmp_path / 'com.trading-ai.automatic-acquisition.plist'
    installed.write_bytes(plistlib.dumps(lifecycle.dispatcher.launchagent()))
    duplicate = tmp_path / 'other-label.plist'
    duplicate.write_bytes(plistlib.dumps({'Label': 'other-label', 'ProgramArguments': [
        '/some/python', '/some/automatic_acquisition.py', '--tick']}))
    monkeypatch.setattr(lifecycle, 'PLIST', installed)
    monkeypatch.setattr(lifecycle.dispatcher, 'verify_config', lambda: None)
    with pytest.raises(RuntimeError, match='Duplicate dispatcher'):
        lifecycle.config()


def test_loaded_definition_mismatch_is_refused(monkeypatch):
    monkeypatch.setattr(lifecycle, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='program = /wrong/worker'))
    with pytest.raises(RuntimeError, match='Loaded dispatcher definition differs'):
        lifecycle.loaded()


@pytest.mark.parametrize('source', ['/host_mnt/Volumes/My Passport/passport/trading-ai', '/Volumes/My Passport', '/Volumes'])
def test_external_mount_alias_and_parent_consumers_are_visible(monkeypatch, source):
    row = {'Name': '/other-project-writer', 'Config': {'Labels': {}},
        'State': {'Status': 'running'}, 'Mounts': [{'Type': 'bind', 'Source': source, 'RW': True}]}
    def fake_run(args):
        return SimpleNamespace(stdout='abc' if args[1] == 'ps' else json.dumps([row]))
    monkeypatch.setattr(lifecycle, 'run', fake_run)
    assert lifecycle.containers()[0]['name'] == 'other-project-writer'
