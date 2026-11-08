import json
import sqlite3
import subprocess
import sys
import unittest
from dataclasses import asdict
from scrapper.tests import test_d09_artifact_reconciliation as fixture
from scrapper.research.features.documents import document_evidence
from scrapper.documents.readiness import evidence as owner
from scrapper.documents.availability import AVAILABILITY_STATUSES


class DocumentEvidenceTests(unittest.TestCase):
    setUp = fixture.ArtifactReconciliationD09Tests.setUp
    tearDown = fixture.ArtifactReconciliationD09Tests.tearDown
    _seed_event_and_artifacts = fixture.ArtifactReconciliationD09Tests._seed_event_and_artifacts
    _blob = fixture.ArtifactReconciliationD09Tests._blob
    _accept_market_cap = fixture.ArtifactReconciliationD09Tests._accept_market_cap

    def check(self, conn, *, status, at, token, aid=None, evidence=None):
        with conn:
            conn.execute("""INSERT INTO artifact_check_events
                (expectation_id,checked_at_utc,source,requested_url,result_status,
                 evidence_json,discovered_artifact_id,error_json,retryable,claim_token)
                VALUES (?,?,'fixture_source','https://fixture.test/doc',?,?,?,?,1,?)""",
                (self.transcript_expectation_id,at,status,json.dumps(evidence or {}),aid,
                 '{"technical_failure":null}',token))

    def read(self, conn, cutoff=fixture.T12):
        return document_evidence(conn,expectation_id=self.transcript_expectation_id,as_of_utc=cutoff)

    def test_historical_cutoff_does_not_backdate_latest_readiness(self):
        with fixture.connect(self.db_path) as conn:
            self.check(conn,status='unknown',at=fixture.T2,token='early')
            data={'content_id':7,'sha256':'a'*64,'origin':'official','unit':None,'value':None}
            self.check(conn,status='available',at=fixture.T12,token='late',aid=self.transcript_artifact_id,evidence=data)
            conn.execute("UPDATE artifact_expectations SET availability_status='available'")
            early=self.read(conn,fixture.T6);late=self.read(conn)
            self.assertEqual(early.result.result_status,'unknown')
            self.assertIsNone(early.available_at_utc);self.assertIsNone(early.recorded_ready)
            self.assertEqual(late.result.evidence,data);self.assertEqual(late.available_at_utc,fixture.T12)
            self.assertTrue(late.recorded_ready)
            self.assertEqual(late.result.discovered_artifact_id,self.transcript_artifact_id)
            self.assertEqual(late.identity['period_end'],'2026-06-30')
            self.assertEqual(late.result.evidence,json.loads(late.evidence_json))
            self.assertEqual(self.read(conn,'2026-08-05T17:30:00+05:30'),late.__class__(**{**late.__dict__,'as_of_utc':'2026-08-05T17:30:00+05:30'}))

    def test_missing_check_is_null_and_future_or_unknown_expectations_are_rejected(self):
        with fixture.connect(self.db_path) as conn:
            result=self.read(conn)
            self.assertIsNone(result.result);self.assertIsNone(result.recorded_ready)
            self.assertIsNone(result.check_id)
            with self.assertRaises(KeyError):self.read(conn,'2026-08-04T00:00:00Z')
            with self.assertRaises(KeyError):document_evidence(conn,expectation_id=999999,as_of_utc=fixture.T12)

    def test_all_availability_states_keep_nulls_errors_and_retryability(self):
        with fixture.connect(self.db_path) as conn:
            for index,status in enumerate(AVAILABILITY_STATUSES):
                self.check(conn,status=status,at=fixture.T2,token='status-'+str(index),
                           aid=self.transcript_artifact_id if status=='available' else None,
                           evidence={'rating':None,'origin':'generated','zero':0})
                result=self.read(conn)
                self.assertEqual(result.result.result_status,status)
                self.assertEqual(result.result.error,{'technical_failure':None})
                self.assertTrue(result.result.retryable)
                self.assertEqual(result.result.evidence,{'rating':None,'origin':'generated','zero':0})
                self.assertEqual(result.recorded_ready,True if status=='available' else None)

    def test_readonly_connection_and_source_record_bytes_unchanged(self):
        with fixture.connect(self.db_path) as conn:
            self.check(conn,status='inaccessible',at=fixture.T2,token='readonly')
        conn=fixture.connect(self.db_path,read_only=True)
        try:
            before=tuple(conn.iterdump());changes=conn.total_changes
            self.assertIs(document_evidence,owner.document_evidence)
            self.assertEqual(asdict(self.read(conn)),asdict(self.read(conn)))
            self.assertEqual(tuple(conn.iterdump()),before);self.assertEqual(conn.total_changes,changes)
        finally:conn.close()

    def test_timestamp_validation_and_observation_order(self):
        with fixture.connect(self.db_path) as conn:
            for value in [True,0,-1,None,'1']:
                with self.assertRaises(ValueError):document_evidence(conn,expectation_id=value,as_of_utc=fixture.T12)
            with self.assertRaises(ValueError):self.read(conn,'2026-08-05')
            self.check(conn,status='unknown',at='2026-08-04T00:00:00Z',token='invalid')
            with self.assertRaises(ValueError):self.read(conn)

    def test_import_inert_and_research_layer_has_no_sql(self):
        import ast
        from pathlib import Path
        for name in ['research/features/documents.py','documents/readiness/evidence.py','db/documents/evidence.py']:
            tree=ast.parse((Path(__file__).parents[1]/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse(any(x in (node.module or '') for x in ('pipeline','cli','operations','view','sources')))
                if not name.startswith('db/') and isinstance(node,ast.Attribute):
                    self.assertNotIn(node.attr,('execute','executemany','executescript'))
        script="import sys,sqlite3,socket,subprocess,threading\nsys.dont_write_bytecode=True\ndef deny(*a,**k):raise AssertionError('side effect')\ndef audit(event,args):\n if event=='open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2]&3)):raise AssertionError('write')\n if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','sqlite3.connect'):raise AssertionError(event)\nsys.addaudithook(audit)\nsqlite3.connect=socket.socket.connect=subprocess.Popen=threading.Thread.start=deny\nimport scrapper.research.features.documents\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
