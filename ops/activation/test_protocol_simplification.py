"""Isolated regressions for removing inventory and checkout coupling."""
import hashlib
import json
from pathlib import Path

import pytest

import acquisition_release as releases
import automatic_acquisition as scheduler
import phase4_documents
import scraper_storage_growth as storage


def test_disabled_growth_never_scans_or_reads_ledger(tmp_path, monkeypatch):
    ledger = tmp_path/'ledger.json'
    ledger.write_text('deliberately unreadable as JSON')
    monkeypatch.setattr(storage, 'inventory', lambda *_: pytest.fail('Unexpected archive scan'))
    monkeypatch.setattr(phase4_documents, 'storage_preflight', lambda: pytest.fail('Duplicate preflight'))
    capacity = dict(root=str(tmp_path), free_bytes=1000, required_free_bytes=100)
    config = {'enforce_external_growth_limit': False}
    growth, fresh = storage.account_and_refresh(tmp_path, ledger, config, capacity, reserve_bytes=20)
    assert growth['inventory_deferred'] and not growth['budget_enforced']
    assert fresh is capacity
    assert storage.batch_account(tmp_path, ledger, config, capacity)['inventory_deferred']
    assert ledger.read_text() == 'deliberately unreadable as JSON'
    with pytest.raises(RuntimeError, match='Reserve cannot cover'):
        storage.batch_account(tmp_path, ledger, config, capacity, reserve_bytes=901)
    with pytest.raises(RuntimeError, match='verified document root'):
        storage.batch_account(tmp_path, ledger, config, capacity | {'root': '/wrong'})


def make_release(tmp_path):
    workspace = tmp_path/'workspace'
    for directory in ('scrapper', 'ops/activation', 'ops/host', 'docs', 'ops/docker', '.git'):
        (workspace/directory).mkdir(parents=True, exist_ok=True)
    for name in ('.scheduler-admission.lock', '.nse-owner.lock', '.monitor-readers.lock'):
        (workspace/'ops/activation'/name).touch()
    (workspace/'scrapper/runtime.py').write_text('original runtime')
    (workspace/'.screener_session').write_text('test credential')
    files = {'scrapper/runtime.py': hashlib.sha256(b'original runtime').hexdigest(),
             'ops/host/operator.py': 'historical operator digest',
             'scrapper/tests/test_runtime.py': 'historical test digest'}
    (workspace/'ops/activation/automatic-acquisition-manifest.json').write_text(json.dumps({'files': files}))
    release = releases.prepare_release(workspace, workspace/'artifacts/acquisition-releases/test-release')
    (release.parent/'active.json').write_text(json.dumps({'release_root': str(release)}))
    return workspace, release


def test_workspace_edits_do_not_change_deployed_code_or_locks(tmp_path, monkeypatch):
    workspace, release = make_release(tmp_path)
    (workspace/'scrapper/runtime.py').write_text('working edit')
    monkeypatch.setattr(scheduler, 'ROOT', workspace)
    scheduler.verify_config()
    assert releases.active_root(workspace) == releases.active_root(release) == release
    assert (release/'scrapper/runtime.py').read_text() == 'original runtime'
    for name in ('.scheduler-admission.lock', '.nse-owner.lock', '.monitor-readers.lock'):
        assert (release/'ops/activation'/name).samefile(workspace/'ops/activation'/name)
        assert not (release/'ops/activation'/name).is_symlink()
    assert (release/'artifacts').samefile(workspace/'artifacts')
    assert (release/'.screener_session').is_symlink()
    agent = scheduler.launchagent()
    assert agent['ProgramArguments'][1] == str(release/'ops/activation/automatic_acquisition.py')
    assert agent['WorkingDirectory'] == str(workspace)
    (release/'scrapper/runtime.py').write_text('deployment corruption')
    with pytest.raises(RuntimeError, match='source/config drift'):
        scheduler.verify_config()


def test_recovery_preserves_failure_but_marks_current_status(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, 'HERE', tmp_path)
    monkeypatch.setattr(scheduler, 'STATE', tmp_path)
    monkeypatch.setattr(scheduler, 'verify_config', lambda: {})
    monkeypatch.setattr(scheduler, 'due', lambda _: [])
    old = {'status': 'STOP', 'error': 'previous configuration drift'}
    (tmp_path/'scheduler-failure.json').write_text(json.dumps(old))
    assert scheduler.tick()['status'] == 'checked'
    recovered = json.loads((tmp_path/'scheduler-failure.json').read_text())
    assert recovered['status'] == 'RECOVERED'
    assert recovered['previous_failure'] == old


def test_workspace_and_release_expect_identical_agent(tmp_path, monkeypatch):
    workspace, release = make_release(tmp_path)
    monkeypatch.setattr(scheduler, 'ROOT', workspace)
    expected = scheduler.launchagent()
    monkeypatch.setattr(scheduler, 'ROOT', release)
    monkeypatch.setattr(scheduler, 'STATE', release/'artifacts/automatic-acquisition')
    assert scheduler.launchagent() == expected


def test_failed_activation_restores_pointer_and_installed_agent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import plistlib
    import subprocess
    workspace, release = make_release(tmp_path)
    pointer = release.parent/'active.json'
    pointer.unlink()
    home = tmp_path/'home'
    destination = home/'Library/LaunchAgents'/(scheduler.LABEL+'.plist')
    destination.parent.mkdir(parents=True)
    original = plistlib.dumps({'Label': scheduler.LABEL, 'original': True})
    destination.write_bytes(original)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(scheduler, 'ROOT', workspace)
    bootstraps = []
    def run(args, **kwargs):
        if args[1] == 'bootstrap':
            bootstraps.append(args)
            if len(bootstraps) == 1:
                raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        releases.activate_release(release)
    assert not pointer.exists()
    assert destination.read_bytes() == original
    assert len(bootstraps) == 2
