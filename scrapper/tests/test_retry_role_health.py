"""Offline retry boundaries and real schema/dependency/progress health contracts."""
import sqlite3
import unittest
from unittest.mock import patch
from scrapper.core.attempts import claim_attempt, retry_remaining, retry_delay, BROWSER_MAX_INVOCATIONS
from scrapper.operations.workers.lifecycle import WorkerLifecycle
from scrapper.operations.workers.health import role_health, bind_role_health
from scrapper.db.database import apply_migrations
from scrapper.tests import test_claim_fencing as fencing
from scrapper.db.documents.recovery import browser_rating as ledger
from scrapper.commands.browser_rating_recovery import claim, finish

class RetryHealthTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close);apply_migrations(self.conn)
        self.clock=[0.];self.life=WorkerLifecycle(monotonic=lambda:self.clock[0])
        self.life.beat('READY')

    def health(self, role='generic', dependencies=None):
        return role_health(self.conn,role,self.life.snapshot(),now=self.clock[0],
            dependencies={'store':lambda:True} if dependencies is None else dependencies,
            progress_limit=60)

    def test_normalized_all_queue_counts_and_invalid_values(self):
        for n in range(1,5):
            for kind in ('generic','rating','browser'):
                self.assertEqual(claim_attempt({'attempt_count':n-(kind=='browser')},kind),n)
        for raw in (-1,True,'1',1.5):
            with self.assertRaises(ValueError):claim_attempt({'attempt_count':raw},'browser')
        with self.assertRaises(ValueError):claim_attempt({'attempt_count':0},'generic')
        with self.assertRaises(ValueError):claim_attempt({'attempt_count':1},'other')

    def test_browser_four_real_claim_invocations_terminal_boundary(self):
        fixture=fencing.ClaimFencingTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        conn=fixture.browser()
        for n in range(1,5):
            job=claim(conn,'run',[], 'owner')
            self.assertEqual(job['attempt_count'],n-1)
            self.assertEqual(claim_attempt(job,'browser'),n)
            retry=retry_remaining(claim_attempt(job,'browser'),BROWSER_MAX_INVOCATIONS)
            self.assertEqual(retry,n<4)
            finish(conn,job,'PENDING' if retry else 'FAILED')
        self.assertIsNone(claim(conn,'run',[],'owner'))
        self.assertEqual(conn.execute('SELECT attempt_count,status FROM browser_rating_recovery_jobs').fetchone()[:],(4,'FAILED'))

    def test_retry_policy_delays_match_prior_and_large_counts_are_bounded(self):
        self.assertEqual([retry_remaining(n,3) for n in range(1,5)],[True,True,False,False])
        for n in range(1,40):
            self.assertEqual(retry_delay(n),min(86400,900*2**(n-1)))
            self.assertEqual(retry_delay(n,offset=1,maximum_seconds=57600),min(86400,900*2**min(n,6)))
            self.assertTrue(retry_remaining(n,None))
        self.assertEqual(retry_delay(10**9),86400)

    def test_readonly_schema_probe_all_roles_no_mutation(self):
        before=list(self.conn.iterdump());self.conn.execute('PRAGMA query_only=ON')
        for role in ('generic','rating','browser'):
            self.assertTrue(self.health(role)['healthy'])
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertFalse(self.conn.in_transaction)

    def test_missing_schema_column_and_migration_are_unhealthy(self):
        self.conn.execute('ALTER TABLE artifact_download_queue RENAME COLUMN claim_token TO old_token')
        self.assertIn('schema:OperationalError',self.health()['errors'])
        self.conn.execute("DELETE FROM schema_migrations WHERE version='070_document_extraction_claims.sql'")
        self.assertIn('schema:RuntimeError',self.health()['errors'])

    def test_missing_and_throwing_dependencies_fail_closed(self):
        self.assertFalse(self.health(dependencies={})['healthy'])
        self.assertIn('dependency:store',self.health(dependencies={'store':lambda:False})['errors'])
        def fail():raise OSError('missing')
        self.assertIn('dependency:browser:OSError',self.health(dependencies={'browser':fail})['errors'])
        with self.assertRaises(RuntimeError):bind_role_health(self.life,self.conn,'generic',dependencies={})

    def test_idle_and_cooldown_are_healthy_without_completed_progress(self):
        for state in ('READY','IDLE','WAITING','COOLDOWN','THROTTLED'):
            self.clock[0]+=10000;self.life.beat(state)
            health=self.health();self.assertTrue(health['healthy']);self.assertTrue(health['waiting'])
            self.assertEqual(health['completed'],0)
        self.clock[0]+=31
        self.assertIn('heartbeat:stale',self.health()['errors'])

    def test_renewal_and_heartbeat_cannot_hide_stalled_work_or_idle_age(self):
        self.clock[0]=10000;self.life.claimed(1)
        self.assertTrue(self.health()['healthy'])
        self.clock[0]+=61;self.life.beat('WORKING')
        self.assertIn('progress:stalled',self.health()['errors'])
        self.life.finished();self.assertTrue(self.health()['healthy'])
        self.life.claimed(2);self.assertTrue(self.health()['healthy'])

    def test_observer_failure_stopped_and_drain_states(self):
        self.life.observer_error='OSError';self.assertFalse(self.health()['healthy'])
        self.life.observer_error=None;self.life.claimed(1);self.life.set()
        self.assertTrue(self.health()['healthy'])
        self.life.beat('STOPPED');self.assertFalse(self.health()['healthy'])

    def test_bound_snapshot_health_is_emitted_and_probe_failure_is_observable(self):
        seen=[];self.life.observer=seen.append
        bind_role_health(self.life,self.conn,'generic',dependencies={'store':lambda:True},progress_limit=60)
        self.assertTrue(seen[-1]['health']['healthy'])
        def fail(snapshot):raise OSError()
        self.life.health_probe=fail;self.life.beat('READY')
        self.assertEqual(seen[-1]['health']['errors'],['probe:OSError'])

    def test_imports_start_no_io_threads_or_processes(self):
        import subprocess,sys
        code='''from unittest.mock import patch
import socket,sqlite3,threading,subprocess,pathlib
with patch.object(sqlite3,'connect',side_effect=AssertionError),patch.object(socket,'socket',side_effect=AssertionError),patch.object(threading.Thread,'start',side_effect=AssertionError),patch.object(subprocess,'Popen',side_effect=AssertionError),patch.object(pathlib.Path,'mkdir',side_effect=AssertionError):
 import scrapper.core.attempts
 import scrapper.operations.workers.health
'''
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
