"""Alternate recovery ownership, local hooks, and offline provider outcomes."""
import ast
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from scrapper.pipeline import alternate_source_recovery as facade
from scrapper.documents.recovery import alternate_sources as owner
from scrapper.db.documents.recovery import alternate_sources as queries
from scrapper.db.document_recovery_repo import load_recovery_target
from scrapper.core.http import FetchResponse
from scrapper.tests import test_da06_recovery_attachment as fixture

NAMES = ('_exchange_observations', '_issuer_observations', '_process_claim', 'run_document_recovery_worker')
NOW = fixture.NOW

class AlternateSourceOwnershipTests(unittest.TestCase):
    setUp = fixture.RecoveryAttachmentTests.setUp
    tearDown = fixture.RecoveryAttachmentTests.tearDown
    _seed_blob = fixture.RecoveryAttachmentTests._seed_blob
    _seed_blocked_artifact = fixture.RecoveryAttachmentTests._seed_blocked_artifact

    def response(self, url, body=b'[]', status='ok', code=200, final=None):
        return FetchResponse(requested_url=url, final_url=final or url, status_code=code,
            content=body, content_type='application/json', encoding='utf-8', etag=None,
            last_modified=None, auth_mode='public', fetch_status=status)

    def run_worker(self, module, source, fetcher):
        return module.run_document_recovery_worker(self.conn, sources=(source,), limit=1,
            throttle_seconds=0, worker='fixture-worker', fetcher=fetcher, now=lambda: NOW,
            sleep=lambda _: None, discovery_snapshot_root=self.root/'alternate-discovery')

    def test_facade_arguments_and_hooks_are_local(self):
        for name in NAMES:
            fn = getattr(facade, name)
            sig = inspect.signature(fn)
            args = [object() for p in sig.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD]
            kwargs = {p.name: object() for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY}
            token = object()
            with patch.object(owner, name, return_value=token) as delegate:
                self.assertIs(fn(*args, **kwargs), token)
            self.assertEqual(delegate.call_args.args, tuple(args))
            self.assertEqual(delegate.call_args.kwargs, dict(kwargs, _hooks=facade))
        original = owner.claim_recovery_target
        with patch.object(facade, 'claim_recovery_target', return_value=None) as claim:
            result = self.run_worker(facade, 'nse', lambda *a: self.fail('unexpected network'))
        self.assertEqual(result['processed_target_source_count'], 0)
        self.assertTrue(claim.called)
        self.assertIs(owner.claim_recovery_target, original)

    def test_direct_worker_refuses_invalid_inputs_before_claim(self):
        before = list(self.conn.iterdump())
        for kwargs in ({'sources':()}, {'sources':('debt',)}, {'sources':('nse',), 'limit':0},
                       {'sources':('bse',), 'lease_seconds':0}, {'sources':('issuer-ir',), 'throttle_seconds':-1}):
            for module in (facade, owner):
                with self.assertRaises(ValueError):module.run_document_recovery_worker(self.conn, **kwargs)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_exchange_rejection_and_error_keep_discovery_and_attempt_evidence(self):
        # Distinct sources allow two terminal paths on the same logical artifact.
        company_id = load_recovery_target(self.conn, self.target_id).company_id
        with self.conn:
            self.conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value,is_current) VALUES (?,'BSE','500001',1)", (company_id,))
            self.conn.execute("UPDATE artifact_recovery_targets SET bse_codes_json='[\"500001\"]' WHERE recovery_target_id=?", (self.target_id,))
        for module, source, status, code, expected in ((owner,'nse','rejected',403,'INTERACTION_REQUIRED'), (facade,'bse','error',500,'RETRYABLE_FAILURE')):
            result = self.run_worker(module, source, lambda url, exchange: self.response(url, body=source.encode(), status=status, code=code))
            self.assertEqual(result['outcome_counts'], {source+':'+expected:1})
        rows = self.conn.execute('SELECT status,error_json FROM artifact_recovery_attempts ORDER BY recovery_attempt_id').fetchall()
        self.assertEqual([r['status'] for r in rows], ['INTERACTION_REQUIRED','RETRYABLE_FAILURE'])
        self.assertTrue(all(json.loads(r['error_json'])['message'] for r in rows))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM response_blobs').fetchone()[0],3)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM artifact_recovery_candidates').fetchone()[0],0)

    def test_blocked_redirect_refuses_before_discovery_storage(self):
        result = self.run_worker(owner, 'nse', lambda url, exchange: self.response(url, final='https://www.crisilratings.com/forbidden.pdf'))
        self.assertEqual(result['outcome_counts'], {'nse:INTERACTION_REQUIRED':1})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM response_blobs').fetchone()[0],1)

    def test_bse_debt_is_separate_observation_and_keeps_blob(self):
        from scrapper.tests.test_da06_recovery_adapters import target
        payload = {'Table':[{'SCRIP_CD':'500001','SLONGNAME':'Fixture Limited',
            'NEWSSUB':'Placement Memorandum for bond issue','SUBCATNAME':'Debt Listing',
            'ATTACHMENTNAME':'pm.pdf','DT_TM':'01/08/2026 13:30:00','NEWSID':'BSE-DEBT-1'}]}
        observations, evidence = owner._exchange_observations(conn=self.conn,target=target(),source='bse',
            discovered_at_utc=NOW,fetcher=lambda url, exchange:self.response(url,json.dumps(payload).encode()),
            sleep=lambda _:None,throttle_seconds=0,discovery_snapshot_root=self.root/'debt')
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0][0].source_type.value,'BSE_DEBT_DOCUMENT')
        self.assertGreater(observations[0][1],0)
        self.assertEqual(evidence['fetch_status_counts'],{'ok':1})

    def test_saved_issuer_pages_keep_no_network_and_skip_missing(self):
        target = load_recovery_target(self.conn,self.target_id)
        blob = self._seed_blob(b"<a href='/rating.pdf'>CRISIL Credit Rating Rationale</a>", 'text/html','html')
        with self.conn:
            for position, blob_id in enumerate((blob,self._seed_blob(b'missing','text/html','html'))):
                self.conn.execute("""INSERT INTO artifact_discovery_pages(company_id,discovered_from_source,discovery_page_url,blob_id,parser_name,parser_version,first_observed_at_utc) VALUES (?,'company_ir',?,?, 'fixture','1',?)""",(target.company_id,'https://issuer.example/ratings/'+str(position),blob_id,NOW))
        missing = queries.list_saved_issuer_pages(self.conn,company_id=target.company_id)[1]
        Path(missing['local_path']).unlink()
        before = list(self.conn.iterdump())
        with patch.object(owner,'fetch_exchange_recovery_surface',side_effect=AssertionError('network')):
            found,evidence = owner._issuer_observations(self.conn,target=target,discovered_at_utc=NOW)
        self.assertEqual(evidence,{'saved_page_count':2,'skipped_page_count':1,'network_request_count':0})
        self.assertEqual(len(found),1)
        self.assertEqual(found[0][1],blob)
        self.assertEqual(found[0][0].candidate_url,'https://issuer.example/rating.pdf')
        self.assertEqual(before,list(self.conn.iterdump()))
        original_path = owner.Path
        with patch.object(facade, 'Path', wraps=Path) as local_path:
            self.assertEqual(facade._issuer_observations(self.conn,target=target,discovered_at_utc=NOW),(found,evidence))
        self.assertEqual(local_path.call_count, 2)
        self.assertIs(owner.Path, original_path)

    def test_layers_and_transitive_import_help_are_inert(self):
        for module in (owner, queries):
            tree = ast.parse(Path(module.__file__).read_text())
            if module is owner:
                self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in {'execute','executemany','executescript'} for n in ast.walk(tree)))
            for n in ast.walk(tree):
                if isinstance(n,ast.ImportFrom):
                    self.assertFalse(set((n.module or '').split('.')) & {'pipeline','operations','view','commands','cli','workflows'})
        script = """import sys,socket,sqlite3,subprocess,threading
sys.dont_write_bytecode=True
def deny(*a,**k):raise AssertionError('forbidden side effect')
def audit(event,args):
 if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write during import/help')
 if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):raise AssertionError(event)
sys.addaudithook(audit)
socket.socket.connect=sqlite3.connect=subprocess.Popen=threading.Thread.start=deny
import scrapper.documents.recovery.alternate_sources
import scrapper.db.documents.recovery.alternate_sources
import scrapper.pipeline.alternate_source_recovery
import scrapper.cli
scrapper.cli.main(['run-document-recovery-worker','--help'])
"""
        result = subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
