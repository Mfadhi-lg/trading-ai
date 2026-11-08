"""Fixture-only host tests; shell routing is syntax checked separately."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from run_offline import GUARD

OUT=Path(__file__).resolve().parent
EXPORT=Path(os.environ.get('EXPORT_TEST_SOURCE_ROOT',str(OUT.parents[2]/'github-source-candidate-20261023')))
# Pure-Python UI packages already cached locally, at the lock's exact versions.
# No download, installation or writes to the shared package cache.
cache=Path.home()/'.cache/uv/archive-v0'
# The historical fixture predates wake's credential/platform/image checks.
# Supply synthetic prerequisites through a test plugin, never real host state.
FIXTURES = '''
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture(autouse=True)
def export_host_prerequisites(request, tmp_path, monkeypatch):
    if request.module.__name__.split('.')[-1] != 'test_acquisition_lifecycle':
        return
    if 'isolated' not in request.fixturenames:
        return
    lifecycle = request.module.lifecycle
    root = tmp_path / 'synthetic-host-root'
    root.mkdir()
    (root / '.screener_session').write_text('synthetic offline fixture only')
    monkeypatch.setattr(lifecycle, 'ROOT', root)
    original_stat = os.stat
    def fixture_stat(path, *args, **kwargs):
        if str(path) == '/dev/console':
            return SimpleNamespace(st_uid=os.getuid())
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', fixture_stat)
    original_is_file = Path.is_file
    def fixture_is_file(path):
        if str(path) == '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':
            return True
        return original_is_file(path)
    monkeypatch.setattr(Path, 'is_file', fixture_is_file)
    import nse_results
    monkeypatch.setattr(nse_results, 'check_configuration',
        lambda: {'oci_index_digest': 'sha256:' + 'a' * 64})
'''
cached=[]
for distribution in ('rich-15.0.0.dist-info','markdown_it_py-4.2.0.dist-info',
                     'mdurl-0.1.2.dist-info','pygments-2.21.0.dist-info'):
    found=sorted(cache.glob('*/'+distribution))
    if not found:raise RuntimeError('Offline cached dependency missing: '+distribution)
    cached.append(str(found[0].parent))
with tempfile.TemporaryDirectory(prefix='host-export-regressions-') as scratch:
    base=Path(scratch)
    (base/'sitecustomize.py').write_text(GUARD)
    (base/'export_host_fixtures.py').write_text(FIXTURES)
    env={k:v for k,v in os.environ.items() if not k.startswith(('SCRAPER_','SCREENER_','DOCUMENT_','TRADING_AI_','PYTEST_'))}
    env.update(EXPORT_TEST_SCRATCH=str(base),TMPDIR=str(base),
        PYTHONPATH=os.pathsep.join((str(base),str(EXPORT),*cached)),PYTHONDONTWRITEBYTECODE='1',
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',TRADING_AI_ENV='test',
        SCRAPER_STORE_PATH=str(base/'store'),SCRAPER_DB_PATH=str(base/'store/test.db'),
        SCRAPER_DOCUMENT_STORE_PATH=str(base/'documents'),SCREENER_SESSION_FILE=str(base/'absent-session'))
    command=[sys.executable,'-B','-m','pytest','-p','no:cacheprovider','-p','export_host_fixtures','--basetemp',str(base/'pytest'),
        'ops/host/test_acquisition_lifecycle.py','ops/host/test_acquisition_observations.py',
        'ops/host/test_acquisition_terminal.py','-k',
        'not shell_shortcuts and not shell_legacy and not new_cli_routes','-q']
    r=subprocess.run(command,cwd=EXPORT,env=env,capture_output=True,text=True)
    (OUT/'host-tests.log').write_text(r.stdout+r.stderr)
    (OUT/'host-tests.json').write_text(json.dumps({'returncode':r.returncode,'command':command,
        'scope':'Three host test modules; shell subprocess tests explicitly deselected because runner denies non-Python subprocesses; bash syntax checked separately',
        'network':'denied','sqlite':'memory or dedicated temporary fixtures only','cached_pure_python_dependency_roots':cached,
        'prerequisites':'Synthetic temporary session marker; mocked console, Chrome presence and sealed image configuration. Existing fixtures mock all operational subprocesses. No real credentials or deployment acceptance.',
        'summary':(r.stdout+r.stderr).splitlines()[-1]},indent=2)+'\n')
    print((r.stdout+r.stderr)[-2400:])
    sys.exit(r.returncode)
