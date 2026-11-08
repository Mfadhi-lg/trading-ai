"""Run existing fixture-independent regressions from the export only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

OUT = Path(__file__).resolve().parent
EXPORT = OUT.parents[2] / 'github-source-candidate-20261023'
MODULES = [
    'scrapper.tests.' + n for n in (
        'test_screener_acceptance_repo', 'test_screener_observation_repo',
        'test_screener_statements',
        'test_reporting_periods', 'test_document_persistence_ownership',
        'test_d02_document_store_guard', 'test_document_download_ownership',
        'test_document_reconciliation_ownership', 'test_document_readiness_ownership',
        'test_migration_manifest', 'test_database_migration_controls',
        'test_runtime_paths', 'test_shared_host_permits',
    )
] + ['scripts.audit.test_migration_lineage']

GUARD = '''
import os, sys
from pathlib import Path
base=Path(os.environ['EXPORT_TEST_SCRATCH']).resolve()
def guard(event,args):
    if event in {'socket.connect','socket.getaddrinfo','socket.bind'}:
        raise RuntimeError('export test denies network')
    if event=='sqlite3.connect':
        name=str(args[0])
        if name==':memory:':return
        if name.startswith('file:'): name=name[5:].split('?')[0]
        path=Path(name).resolve()
        if base != path and base not in path.parents:
            raise RuntimeError('export test denies non-fixture SQLite')
    if event=='subprocess.Popen':
        exe=Path(str(args[0])).name
        if not exe.startswith('python'):
            raise RuntimeError('export test denies non-Python subprocess')
sys.addaudithook(guard)
'''

RUN = '''
import json,sys,unittest
modules=json.loads(sys.argv[1])
suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
r=unittest.TextTestRunner(verbosity=2).run(suite)
print('EXPORT_TEST_RESULT='+json.dumps(dict(tests=r.testsRun,failures=len(r.failures),errors=len(r.errors),skips=len(r.skipped),successful=r.wasSuccessful())))
sys.exit(not r.wasSuccessful())
'''

def main():
    with tempfile.TemporaryDirectory(prefix='export-regression-') as scratch:
        base=Path(scratch)
        (base/'sitecustomize.py').write_text(GUARD)
        env={k:v for k,v in os.environ.items() if not k.startswith(('SCRAPER_', 'SCREENER_', 'DOCUMENT_', 'TRADING_AI_'))}
        env.update(EXPORT_TEST_SCRATCH=str(base), TMPDIR=str(base),
                   PYTHONPATH=os.pathsep.join((str(base),str(EXPORT))),
                   PYTHONDONTWRITEBYTECODE='1', TRADING_AI_ENV='test',
                   SCRAPER_STORE_PATH=str(base/'store'),
                   SCRAPER_DB_PATH=str(base/'store/fixture.db'),
                   SCRAPER_DOCUMENT_STORE_PATH=str(base/'documents'),
                   SCREENER_SESSION_FILE=str(base/'absent-session'),
                   DOCUMENT_REQUIRE_SENTINEL='0', DOCUMENT_STORE_REQUIRE_CAPACITY_FILE='0')
        result=subprocess.run([sys.executable,'-B','-c',RUN,json.dumps(MODULES)],
                              cwd=EXPORT,env=env,capture_output=True,text=True)
        log=result.stdout+result.stderr
        (OUT/'offline-tests.log').write_text(log)
        summary={'returncode':result.returncode,'modules':MODULES,'python':sys.version,
                 'source':'isolated export; original repository absent from PYTHONPATH',
                 'network':'denied in runner and Python subprocesses via sitecustomize',
                 'sqlite':'memory or dedicated temporary fixture root only',
                 'production_files_modified':False}
        for line in log.splitlines():
            if line.startswith('EXPORT_TEST_RESULT='):
                summary.update(json.loads(line.split('=',1)[1]))
        (OUT/'offline-tests.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary,indent=2))
        sys.exit(result.returncode)

if __name__=='__main__':
    main()
