"""Offline permit races, expiry, restart, policy and queue isolation contracts."""
from dataclasses import replace
import importlib
import subprocess
import sys
import ast
import multiprocessing
import os
import signal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from scrapper.core.host_policy import policy_for, OperatorAuthorization
from scrapper.db.host_permits import acquire, finish, renew, PermitLost, retry_after_seconds


def race(path, mode, gate, results):
    conn = sqlite3.connect(path, timeout=10)
    gate.wait()
    result = acquire(conn, 'https://connect.acuite.in/a', mode=mode,
                     owner=mode, now=100, authorization=OperatorAuthorization('test-run','fixture explicit approval',('connect.acuite.in',),10000) if mode == 'browser' else None)
    results.put(result.reason)
    conn.close()

def crash_after_grant(path, results, wait):
    conn=sqlite3.connect(path,timeout=10)
    p=acquire(conn,'careratings.com',mode='generic',owner='crashed-process',now=100,lease_seconds=5).permit
    results.put(p)
    wait.wait(30)

class PermitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'permits.db'
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        self.conn.executescript("""CREATE TABLE credit_rating_host_policy(
            host TEXT PRIMARY KEY,automation_status TEXT,max_concurrency INTEGER,min_delay_seconds REAL);
            CREATE TABLE untouched_queue(id INTEGER,attempt_count INTEGER,status TEXT);
            INSERT INTO untouched_queue VALUES(1,3,'PENDING');""")
        self.conn.executescript((Path(__file__).parents[1]/'db/migrations/071_shared_host_permits.sql').read_text())
    def connect(self):
        return sqlite3.connect(self.path, timeout=10)
    def get(self, host='careratings.com', now=100, **kw):
        return acquire(self.conn, host, mode=kw.pop('mode','generic'), owner=kw.pop('owner','a'), now=now, **kw)
    def test_aliases_normalization_and_unrelated_hosts(self):
        for a,b in [('www.careratings.com','careratings.com'),('connect.acuite.in','www.acuite.in'),('bcrisp.in','www.brickworkratings.com'),('crisilratings.com','www.crisil.com')]:
            self.assertEqual(policy_for(a).group, policy_for(b).group)
        self.assertEqual(policy_for('HTTPS://WWW.CARERATINGS.COM.:443/a').host,'www.careratings.com')
        self.assertNotEqual(policy_for('a.example.com').group,policy_for('b.example.com').group)
        self.assertNotEqual(policy_for('evilcareratings.com').group,policy_for('careratings.com').group)
    def test_terms_and_authorization_are_separate(self):
        self.conn.executemany("INSERT INTO credit_rating_host_policy VALUES(?,'BLOCKED_TERMS',0,4)", [('icra.in',), ('crisil.com',), ('crisilratings.com',)])
        self.conn.commit()
        for host in ['icra.in','x.icra.in','crisilratings.com','www.crisil.com']:
            self.assertEqual(self.get(host).reason,'PUBLISHER_POLICY_BLOCKED')
            self.assertEqual(self.get(host,mode='browser',authorization=OperatorAuthorization('test-run','fixture explicit approval',('connect.acuite.in','www.careratings.com','icra.in','x.icra.in','crisilratings.com','www.crisil.com'),10000)).reason,'PUBLISHER_POLICY_BLOCKED')
        self.assertEqual(self.get(mode='browser').reason,'OPERATOR_AUTHORIZATION_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM acquisition_host_permits').fetchone()[0],0)
    def test_finite_operator_scope_is_distinct_and_persisted(self):
        self.conn.execute("INSERT INTO credit_rating_host_policy VALUES('icra.in','BLOCKED_TERMS',0,4)")
        self.conn.commit()
        auth=OperatorAuthorization('finite-test','fixture approval: icra only',('icra.in',),110,('icra.in',))
        p=self.get('icra.in',mode='browser',authorization=auth).permit
        self.assertEqual(p.expires_at,110)
        self.assertEqual(self.get('icra.in',now=104).reason,'PUBLISHER_POLICY_BLOCKED')
        self.assertEqual(self.get('www.icra.in',mode='browser',authorization=auth).reason,'OPERATOR_AUTHORIZATION_REQUIRED')
        self.assertEqual(self.get('icra.in',now=110,mode='browser',authorization=auth).reason,'OPERATOR_AUTHORIZATION_REQUIRED')
        with self.assertRaises(ValueError):self.get('icra.in',authorization=auth)
        self.assertEqual(renew(self.conn,p,now=105,lease_seconds=100).expires_at,110)
        with self.assertRaises(PermitLost):renew(self.conn,p,now=110)
        receipt=self.conn.execute('SELECT authorization_json FROM acquisition_host_permits').fetchone()[0]
        self.assertIn('fixture approval: icra only',receipt)
        self.assertEqual(self.get('icra.in',mode='browser',authorization=True).reason,'OPERATOR_AUTHORIZATION_REQUIRED')

    def test_database_restriction_applies_to_aliases(self):
        self.conn.execute("INSERT INTO credit_rating_host_policy VALUES('connect.acuite.in','BLOCKED_TERMS',0,4)");self.conn.commit()
        self.assertEqual(self.get('www.acuite.in').reason,'PUBLISHER_POLICY_BLOCKED')
    def test_database_minimum_cannot_be_relaxed(self):
        self.conn.execute("INSERT INTO credit_rating_host_policy VALUES('careratings.com','UNSPECIFIED',8,10)");self.conn.commit()
        p=self.get().permit
        self.assertEqual(self.get(now=105).reason,'HOST_DEFERRED')
        finish(self.conn,p,now=101)
        self.assertEqual(self.get(now=105).retry_at,110)
    def test_restart_pacing_and_expiry(self):
        p=self.get(lease_seconds=2).permit
        other=self.connect()
        try:
            self.assertEqual(acquire(other,'www.careratings.com',mode='rating',owner='b',now=102).reason,'HOST_DEFERRED')
            q=acquire(other,'www.careratings.com',mode='rating',owner='b',now=104).permit
            self.assertIsNotNone(q)
            with self.assertRaises(PermitLost):finish(self.conn,p,now=105)
            self.assertEqual(other.execute("SELECT status FROM acquisition_host_permits WHERE permit_token=?",(q.token,)).fetchone()[0],'ACTIVE')
        finally:other.close()
    def test_process_race_across_all_executor_modes(self):
        ctx=multiprocessing.get_context('spawn');gate=ctx.Event();results=ctx.Queue()
        ps=[ctx.Process(target=race,args=(str(self.path),m,gate,results)) for m in ['generic','rating','browser','screener']]
        for p in ps:p.start()
        gate.set()
        answers=[results.get(timeout=15) for _ in ps]
        for p in ps:p.join(15);self.assertEqual(p.exitcode,0)
        self.assertEqual(answers.count('GRANTED'),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0],1)
    def test_crash_before_request_recovers_and_preserves_history(self):
        p=self.get(lease_seconds=5).permit
        self.assertEqual(self.get(now=104).reason,'HOST_BUSY')
        q=self.get(now=105).permit
        self.assertNotEqual(p.token,q.token)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acquisition_host_permits').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT * FROM untouched_queue').fetchall(),[(1,3,'PENDING')])
    def test_killed_process_reservation_survives_until_expiry(self):
        ctx=multiprocessing.get_context('spawn');results=ctx.Queue();wait=ctx.Event()
        process=ctx.Process(target=crash_after_grant,args=(str(self.path),results,wait))
        process.start()
        try:
            old=results.get(timeout=15)
            os.kill(process.pid,signal.SIGKILL);process.join(10)
            self.assertEqual(process.exitcode,-signal.SIGKILL)
            self.assertEqual(self.get(now=104).reason,'HOST_BUSY')
            new=self.get(now=105).permit
            with self.assertRaises(PermitLost):finish(self.conn,old,now=106,outcome='blocked',cooldown_seconds=100)
            finish(self.conn,new,now=106)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM acquisition_host_permits').fetchone()[0],2)
            self.assertFalse(self.conn.in_transaction)
        finally:
            if process.is_alive():process.terminate();process.join(10)

    def test_renew_owner_fence_and_no_deadline_shrink(self):
        p=self.get(lease_seconds=5).permit
        with self.assertRaises(PermitLost):renew(self.conn,replace(p,owner='other'),now=101)
        p=renew(self.conn,p,now=101,lease_seconds=1)
        self.assertEqual(p.expires_at,105)
        p=renew(self.conn,p,now=102,lease_seconds=10)
        self.assertEqual(p.expires_at,112)
        self.assertEqual(self.get(now=106).reason,'HOST_BUSY')
        finish(self.conn,p,now=107)
        with self.assertRaises(PermitLost):renew(self.conn,p,now=108)
    def test_monotonic_cooldown_retry_after_and_unrelated_progress(self):
        p=self.get().permit
        finish(self.conn,p,now=101,outcome='blocked',retry_after='120')
        self.assertEqual(self.get(now=104).retry_at,221)
        self.assertEqual(self.get('indiaratings.co.in',now=104).reason,'GRANTED')
        self.assertEqual(self.get(now=221).reason,'GRANTED')
    def test_legacy_partition_cooldowns_are_imported_without_writes(self):
        self.conn.executescript("""CREATE TABLE artifact_download_host_health(host TEXT,blocked_until_utc TEXT);
            CREATE TABLE credit_rating_host_health(host TEXT,blocked_until_utc TEXT);
            CREATE TABLE browser_rating_recovery_hosts(run_id TEXT,target_host TEXT,blocked_until_utc TEXT);
            INSERT INTO artifact_download_host_health VALUES('www.careratings.com','1970-01-01T00:03:20+00:00');
            INSERT INTO credit_rating_host_health VALUES('careratings.com','1970-01-01T00:05:00+00:00');
            INSERT INTO browser_rating_recovery_hosts VALUES('other-run','www.careratings.com','1970-01-01T00:06:40+00:00');""")
        before={n:self.conn.execute('SELECT * FROM '+n).fetchall() for n in ['artifact_download_host_health','credit_rating_host_health','browser_rating_recovery_hosts']}
        self.assertEqual(self.get(now=100).retry_at,400)
        self.assertEqual(self.get('indiaratings.co.in',now=100).reason,'GRANTED')
        after={n:self.conn.execute('SELECT * FROM '+n).fetchall() for n in before}
        self.assertEqual(before,after)
        self.assertEqual(self.get(now=400).reason,'GRANTED')

    def test_retry_after_parser(self):
        self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 00:02:00 GMT',100),20)
        for raw in ['nonsense','-1','nan','inf']:
            self.assertIsNone(retry_after_seconds(raw,100))
        self.assertEqual(retry_after_seconds('0',100),0)
    def test_three_failures_open_persisted_circuit(self):
        for now in [100,104,108]:
            p=self.get(now=now).permit;finish(self.conn,p,now=now+1,outcome='transport_failure')
        self.assertEqual(self.get(now=112).retry_at,3709)
        other=self.connect()
        try:self.assertEqual(acquire(other,'www.careratings.com',mode='browser',owner='x',now=120,authorization=OperatorAuthorization('test-run','fixture explicit approval',('connect.acuite.in','www.careratings.com','icra.in','x.icra.in','crisilratings.com','www.crisil.com'),10000)).retry_at,3709)
        finally:other.close()
    def test_backward_clock_does_not_erase_timing(self):
        p=self.get().permit;finish(self.conn,p,now=101,outcome='blocked',cooldown_seconds=100)
        self.assertEqual(self.get(now=90).retry_at,201)
        self.assertEqual(self.conn.execute('SELECT observed_at FROM acquisition_host_state').fetchone()[0],101)
    def test_policy_change_blocks_renewal(self):
        p=self.get().permit
        self.conn.execute("INSERT INTO credit_rating_host_policy VALUES('careratings.com','BLOCKED_TERMS',0,4)");self.conn.commit()
        with self.assertRaises(PermitLost):renew(self.conn,p,now=101)
        finish(self.conn,p,now=102,outcome='released')
    def test_transaction_rollback_and_no_outer_transaction(self):
        before=list(self.conn.iterdump())
        self.conn.execute("CREATE TRIGGER fail_grant BEFORE INSERT ON acquisition_host_permits BEGIN SELECT RAISE(ABORT,'injected'); END;")
        with self.assertRaises(sqlite3.IntegrityError):self.get()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM acquisition_host_state').fetchone()[0],0)
        self.conn.execute('BEGIN')
        with self.assertRaises(ValueError):self.get()
        self.conn.rollback()
    def test_invalid_inputs_and_missing_schema_fail_closed(self):
        for target in ['','https://user:pass@example.com/a','ftp://example.com','https://exa mple.com','https://example.com:bad']:
            with self.assertRaises(ValueError):self.get(target)
        for now in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):self.get(now=now)
        with self.assertRaises(ValueError):self.get(lease_seconds=0)
        with self.assertRaises(ValueError):self.get(mode='manual-bypass')
        conn=sqlite3.connect(':memory:')
        try:
            with self.assertRaises(sqlite3.OperationalError):acquire(conn,'example.com',mode='generic',owner='a',now=1)
        finally:conn.close()
    def test_imports_inert_and_db_dependency_direction(self):
        code = """from unittest.mock import patch
with patch('sqlite3.connect',side_effect=AssertionError('connect')),patch('socket.socket',side_effect=AssertionError('network')),patch('threading.Thread.start',side_effect=AssertionError('thread')),patch('subprocess.Popen',side_effect=AssertionError('process')),patch('pathlib.Path.mkdir',side_effect=AssertionError('directory')):
    import scrapper.core.host_policy
    import scrapper.db.host_permits
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        text=(Path(__file__).parents[1]/'db/host_permits.py').read_text()
        imports=[n.module for n in ast.walk(ast.parse(text)) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any(n and any(x in n.split('.') for x in ('operations','commands','view')) for n in imports))
