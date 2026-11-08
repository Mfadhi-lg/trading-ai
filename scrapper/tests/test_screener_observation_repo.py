"""Offline persistence contracts for source/blob/parsed observation extraction."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.db import repo
from scrapper.core.http import FetchResponse

class ScreenerObservationRepoTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
        CREATE TABLE response_blobs(blob_id INTEGER PRIMARY KEY, content_sha256 TEXT UNIQUE,
          content_type TEXT, encoding TEXT, byte_size INTEGER, local_path TEXT,
          first_observed_at_utc TEXT, created_at_utc TEXT);
        CREATE TABLE parse_artifacts(parse_id INTEGER PRIMARY KEY, blob_id INTEGER,
          parser_name TEXT, parser_version TEXT, manifest_version TEXT, parsed_at_utc TEXT,
          parse_status TEXT, validation_status TEXT);
        CREATE TABLE source_sections(section_id INTEGER PRIMARY KEY, parse_id INTEGER,
          section_code TEXT, heading_raw TEXT, position INTEGER, parse_status TEXT);
        """)

    def test_blob_dedup_preserves_first_observation_and_bytes_without_commit(self):
        response = FetchResponse('url','url',200,b'<html>raw</html>','text/html','utf-8',None,None,'public','ok')
        with tempfile.TemporaryDirectory() as directory, patch.object(repo,'utc_now',return_value='clock'):
            first = repo.store_blob(self.conn,response,'abc','standalone','first',Path(directory))
            second = repo.store_blob(self.conn,response,'abc','standalone','later',Path(directory))
            self.assertEqual(first,second)
            row = self.conn.execute('SELECT * FROM response_blobs').fetchone()
            self.assertEqual(row['first_observed_at_utc'],'first')
            self.assertEqual(row['created_at_utc'],'clock')
            self.assertEqual(Path(row['local_path']).read_bytes(),response.content)
            self.assertTrue(self.conn.in_transaction)
            self.conn.rollback()
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM response_blobs').fetchone()[0],0)
            self.assertTrue(Path(row['local_path']).exists())

    def test_parse_cache_preserves_first_clock_and_status_and_versions(self):
        with patch.object(repo,'utc_now',return_value='clock'):
            first = repo.get_or_create_parse_artifact(self.conn,1,'parser','v1','m1')
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("UPDATE parse_artifacts SET parse_status='ok'")
        self.conn.commit()
        with patch.object(repo,'utc_now',side_effect=AssertionError('cached parse needs no clock')):
            self.assertEqual(repo.get_or_create_parse_artifact(self.conn,1,'parser','v1','m1'),(first[0],'ok'))
        with patch.object(repo,'utc_now',return_value='new-clock'):
            revised = repo.get_or_create_parse_artifact(self.conn,1,'parser','v2','m1')
        self.assertNotEqual(first[0],revised[0])
        self.assertEqual(self.conn.execute('SELECT parsed_at_utc FROM parse_artifacts WHERE parse_id=?',(first[0],)).fetchone()[0],'clock')

    def test_partial_company_parse_rolls_back_sections_and_records_failed_status(self):
        parse_id,_ = repo.get_or_create_parse_artifact(self.conn,1,'p','v','m')
        class Partial:
            profile_heading = 'profile'
        with self.assertRaises(AttributeError):
            repo.store_parsed_company(self.conn,parse_id,Partial(),'first')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_sections').fetchone()[0],0)
        row = self.conn.execute('SELECT parse_status,validation_status FROM parse_artifacts').fetchone()
        self.assertEqual(tuple(row),('failed','failed'))
        self.assertFalse(self.conn.in_transaction)
        with self.assertRaisesRegex(ValueError,'Only a pending'):
            repo.store_parsed_company(self.conn,parse_id,Partial(),'later')

    def test_section_hook_remains_patchable_through_facade(self):
        parse_id,_ = repo.get_or_create_parse_artifact(self.conn,1,'p','v','m')
        with patch.object(repo,'_insert_section',side_effect=RuntimeError('injected failure')) as hook:
            with self.assertRaisesRegex(RuntimeError,'injected failure'):
                repo.store_parsed_company(self.conn,parse_id,object(),'first')
        hook.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT parse_status FROM parse_artifacts').fetchone()[0],'failed')

    def test_observation_owner_import_is_inert(self):
        # Exercise actual transitive imports with the existing forbidden-action guard.
        import subprocess, sys
        script = """import sys, socket, sqlite3, subprocess, threading
sys.dont_write_bytecode = True
def deny(*args, **kwargs): raise AssertionError('forbidden import side effect')
def audit(event, args):
    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):
        raise AssertionError('write during import')
    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):
        raise AssertionError(event)
sys.addaudithook(audit)
socket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny
import scrapper.db.screener.observations
"""
        result = subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
