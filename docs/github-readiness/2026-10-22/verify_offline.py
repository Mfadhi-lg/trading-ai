"""Reuse selected existing tests in isolated storage, deny network/production DB.
This runs fixtures only; it neither initializes nor opens a production database.
"""
import io
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT))
MODULES=[
 'test_screener_acceptance_repo', 'test_screener_observation_repo',
 'test_screener_statements', 'test_screener_period_coverage',
 'test_company_quarter_features', 'test_financial_feature_ownership',
 'test_reporting_periods', 'test_document_persistence_ownership',
 'test_d02_document_store_guard', 'test_document_download_queue',
 'test_document_download_ownership', 'test_document_evidence_interface',
 'test_document_reconciliation_ownership', 'test_document_readiness_ownership',
]
if os.environ.get('AUDIT_MODULES'):
    MODULES=os.environ['AUDIT_MODULES'].split(',')
with tempfile.TemporaryDirectory(prefix='trading-ai-github-audit-') as scratch:
    base=Path(scratch).resolve()
    for key,suffix in [('SCRAPER_STORE_PATH','store'),('SCRAPER_DB_PATH','store/fixture.db'),('SCRAPER_DOCUMENT_STORE_PATH','documents'),('SCREENER_SESSION_FILE','absent-test-session')]:os.environ[key]=str(base/suffix)
    os.environ['TRADING_AI_ENV']='test'
    os.environ['DOCUMENT_REQUIRE_SENTINEL']='0'
    os.environ['DOCUMENT_STORE_REQUIRE_CAPACITY_FILE']='0'
    sys.dont_write_bytecode=True
    def guard(event,args):
        if event in {'socket.connect','socket.getaddrinfo','socket.bind'}:
            raise RuntimeError('audit denies network access')
        if event=='sqlite3.connect':
            name=str(args[0])
            if name==':memory:':return
            if name.startswith('file:'):name=name[5:].split('?')[0]
            path=Path(name).resolve()
            if not str(path).startswith('/private/tmp/') and not str(path).startswith('/tmp/') and not str(path).startswith('/private/var/folders/'):
                raise RuntimeError('audit denies non-fixture SQLite access')
    sys.addaudithook(guard)
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        suite=unittest.defaultTestLoader.loadTestsFromNames(['scrapper.tests.'+n for n in MODULES])
        r=unittest.TextTestRunner(stream=stream,verbosity=1).run(suite)
    label=os.environ.get('AUDIT_RUN_LABEL','offline-tests')
    (OUT/(label+'.log')).write_text(stream.getvalue())
    result=dict(modules=MODULES,python=sys.version,tests=r.testsRun,failures=len(r.failures),errors=len(r.errors),skips=len(r.skipped),successful=r.wasSuccessful(),network='denied by audit hook in test runner',sqlite='memory/temporary fixtures only',production_database_opened=False,limits='subprocess import-inertness probes use existing tests; existing B0-derived fixture inputs read in place and not copied to report')
    (OUT/(label+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    sys.exit(not r.wasSuccessful())
