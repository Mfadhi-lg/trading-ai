"""Acceptance history, atomicity and facade contracts for C12.3."""
import json
import sqlite3
import unittest
from unittest.mock import patch
from scrapper.validation import screener as facade
from scrapper.db.screener import acceptance

class AcceptanceRepoTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:'); self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
        CREATE TABLE parse_artifacts(parse_id INTEGER PRIMARY KEY,parse_status TEXT,validation_status TEXT,blob_id INTEGER);
        CREATE TABLE validation_results(parse_id INTEGER,rule_code TEXT,status TEXT,severity TEXT);
        CREATE TABLE quote_observations(parse_id INTEGER,reporting_scope TEXT);
        CREATE TABLE acceptance_events(company_id INTEGER,source TEXT,reporting_scope TEXT,resolved_source_period TEXT,parse_id INTEGER,action TEXT,accepted_at_utc TEXT,accepted_by TEXT,acceptance_mode TEXT,reason_json TEXT);
        CREATE TABLE accepted_page_versions(company_id INTEGER,source TEXT,reporting_scope TEXT,resolved_source_period TEXT,parse_id INTEGER,accepted_at_utc TEXT,accepted_by TEXT,acceptance_mode TEXT,UNIQUE(company_id,source,reporting_scope,resolved_source_period));
        CREATE TABLE financial_observations(parse_id INTEGER,period_end TEXT,section_code TEXT,reporting_scope TEXT);
        CREATE TABLE response_blobs(blob_id INTEGER,first_observed_at_utc TEXT);
        CREATE TABLE screener_company_collection_state(company_id INTEGER,reporting_scope TEXT,latest_accepted_result_period TEXT,latest_accepted_parse_id INTEGER,updated_at_utc TEXT);
        INSERT INTO parse_artifacts VALUES (1,'ok','passed',1),(2,'ok','passed_with_warnings',2);
        INSERT INTO quote_observations VALUES (1,'standalone'),(2,'standalone');
        INSERT INTO response_blobs VALUES (1,'first'),(2,'later');
        INSERT INTO financial_observations VALUES (1,'2026-06-30','quarters','standalone'),(2,'2026-06-30','quarters','standalone');
        INSERT INTO screener_company_collection_state(company_id,reporting_scope) VALUES (10,'standalone');
        """)
    def accept(self,parse_id=1,scope='standalone',period='2026-06',source='screener'):
        with patch.object(facade,'_utc_now',return_value='clock'):
            facade.accept_parse(self.conn,10,parse_id,source,scope,period)
    def events(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM acceptance_events')]
    def test_revision_and_replay_preserve_history_clock_and_watermark(self):
        self.accept();self.accept();self.accept(2);self.accept(2)
        events=self.events();self.assertEqual([r['action'] for r in events],['accepted','superseded','accepted'])
        self.assertEqual([r['parse_id'] for r in events],[1,1,2])
        self.assertEqual([r['accepted_at_utc'] for r in events],['clock']*3)
        self.assertEqual(json.loads(events[2]['reason_json']),{'previous_parse_id':1})
        self.assertEqual(tuple(self.conn.execute('SELECT latest_accepted_result_period,latest_accepted_parse_id,updated_at_utc FROM screener_company_collection_state').fetchone()),('2026-06',2,'clock'))
        self.assertFalse(self.conn.in_transaction)
    def test_rejection_is_committed_with_ordered_codes_and_existing_version_retained(self):
        self.accept();self.conn.executescript("INSERT INTO validation_results VALUES (2,'z_rule','failed','error'),(2,'a_rule','failed','manual_review');")
        with self.assertRaisesRegex(ValueError,r'blocking_rules=\[a_rule, z_rule\]'):self.accept(2)
        self.assertEqual(self.conn.execute('SELECT parse_id FROM accepted_page_versions').fetchone()[0],1)
        event=self.events()[-1];self.assertEqual(event['action'],'rejected')
        self.assertEqual(json.loads(event['reason_json'])['blocking_rule_codes'],['a_rule','z_rule'])
        self.assertFalse(self.conn.in_transaction)
    def test_scope_and_missing_parse_refusals_do_not_create_history(self):
        with self.assertRaisesRegex(ValueError,'requires reporting_scope=standalone'):self.accept(scope='consolidated')
        with self.assertRaisesRegex(ValueError,'parse_status=missing'):self.accept(99)
        self.assertEqual(self.events(),[])
    def test_warning_is_allowed_but_quote_scope_mismatch_is_rejected(self):
        self.conn.executescript("INSERT INTO validation_results VALUES (2,'warning','failed','warning');")
        self.accept(2)
        self.conn.executescript("UPDATE quote_observations SET reporting_scope='consolidated' WHERE parse_id=1;")
        with self.assertRaisesRegex(ValueError,'actual_scope=consolidated'):self.accept()
        self.assertEqual([r['action'] for r in self.events()],['accepted','rejected'])
    def test_version_update_fault_rolls_back_both_history_events(self):
        self.accept();before=self.events()
        self.conn.executescript("CREATE TRIGGER inject BEFORE UPDATE ON accepted_page_versions BEGIN SELECT RAISE(ABORT,'injected'); END;")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):self.accept(2)
        self.assertEqual(self.events(),before)
        self.assertEqual(self.conn.execute('SELECT parse_id FROM accepted_page_versions').fetchone()[0],1)
        self.assertFalse(self.conn.in_transaction)
    def test_facade_sync_hook_and_non_screener_behavior_remain_compatible(self):
        with patch.object(facade,'sync_screener_quarter_state') as sync:
            self.accept();self.accept();sync.assert_called_with(self.conn,10,'standalone');self.assertEqual(sync.call_count,2)
            self.accept(source='other');self.assertEqual(sync.call_count,2)
    def test_owner_import_is_inert(self):
        import subprocess,sys
        script="import sys, socket, sqlite3, subprocess, threading\nsys.dont_write_bytecode = True\ndef deny(*args, **kwargs): raise AssertionError('forbidden import side effect')\ndef audit(event, args):\n    if event == 'open' and ((isinstance(args[1],str) and any(c in args[1] for c in 'wax+')) or (args[2] & 3)):\n        raise AssertionError('write during import')\n    if event in ('os.mkdir','os.remove','os.rename','subprocess.Popen','socket.__new__','socket.connect','sqlite3.connect'):\n        raise AssertionError(event)\nsys.addaudithook(audit)\nsocket.socket.connect = sqlite3.connect = subprocess.Popen = threading.Thread.start = deny\nimport scrapper.db.screener.acceptance\n"
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
