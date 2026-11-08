"""Repeated dispatch/drain/restart contracts plus real OS signal delivery."""
import contextlib
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from scrapper.operations.workers.roles import ROLES, run_role
from scrapper.operations.workers.lifecycle import WorkerLifecycle, run_bounded, role_lifecycle


class RoleTests(unittest.TestCase):
    def test_exact_startup_and_post_batch_cadence_for_all_roles(self):
        for name, role in ROLES.items():
            with self.subTest(name=name):
                lifecycle = WorkerLifecycle()
                lifecycle.wait = Mock(return_value=False)
                dispatch = Mock(return_value=0)
                result = run_role(name, db_path=Path('/tmp/fixture.db'), dispatch=dispatch,
                    lifecycle=lifecycle, max_cycles=3)
                expected = ([role.startup] if role.startup else []) + [role.command]*3
                self.assertEqual([c.args[0] for c in dispatch.call_args_list],
                    [[*command, '--db', '/tmp/fixture.db'] for command in expected])
                self.assertEqual(result['cycles'], 3)
                self.assertEqual(lifecycle.wait.call_args_list, [unittest.mock.call(role.delay, state='WAITING')]*2)

    def test_drain_before_startup_is_inert(self):
        lifecycle=WorkerLifecycle();lifecycle.set();dispatch=Mock()
        result=run_role('document-download-worker',db_path='fixture',dispatch=dispatch,lifecycle=lifecycle)
        dispatch.assert_not_called();self.assertEqual(result['cycles'],0)

    def test_drain_during_startup_skips_first_claim(self):
        lifecycle=WorkerLifecycle();calls=[]
        def dispatch(argv): calls.append(argv);lifecycle.set();return 0
        run_role('document-download-worker',db_path='fixture',dispatch=dispatch,lifecycle=lifecycle)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'queue-accepted-document-downloads')

    def test_active_drain_uses_same_lifecycle_and_finishes_owned_work(self):
        lifecycle=WorkerLifecycle();calls=[]
        def worker(*,lifecycle):
            calls.append(lifecycle);lifecycle.claimed(42);lifecycle.set();lifecycle.finished();return 0
        def dispatch(argv):return run_bounded(worker)
        lifecycle.wait=Mock()
        result=run_role('credit-rating-worker-care',db_path='fixture',dispatch=dispatch,lifecycle=lifecycle)
        self.assertEqual(calls,[lifecycle]);self.assertEqual(result['cycles'],1)
        self.assertEqual(lifecycle.completed,1);self.assertIsNone(lifecycle.active_job)
        lifecycle.wait.assert_not_called()

    def test_idle_wait_drain_does_not_start_next_batch(self):
        lifecycle=WorkerLifecycle();dispatch=Mock(return_value=0)
        def wait(seconds,**kwargs):lifecycle.set();return True
        lifecycle.wait=wait
        result=run_role('rating-recovery-download',db_path='fixture',dispatch=dispatch,lifecycle=lifecycle)
        dispatch.assert_called_once();self.assertEqual(result['status'],'drained')

    def test_shell_compatible_failures_keep_delay_and_next_batch(self):
        for failure in (2,RuntimeError('offline fault')):
            lifecycle=WorkerLifecycle();lifecycle.wait=Mock(return_value=False)
            dispatch=Mock(side_effect=[failure,0])
            with contextlib.redirect_stderr(io.StringIO()):
                result=run_role('credit-rating-worker-other',db_path='fixture',dispatch=dispatch,
                    lifecycle=lifecycle,max_cycles=2)
            self.assertEqual(result['error_batches'],1);self.assertEqual(dispatch.call_count,2)
            lifecycle.wait.assert_called_once_with(10,state='WAITING')

    def test_startup_failure_still_runs_bounded_batch_and_restart_repeats_enqueue(self):
        dispatch=Mock(side_effect=[2,0,0,0])
        with contextlib.redirect_stderr(io.StringIO()):
            for _ in range(2):run_role('document-download-worker',db_path='fixture',dispatch=dispatch,
                lifecycle=WorkerLifecycle(),max_cycles=1)
        self.assertEqual([c.args[0][0] for c in dispatch.call_args_list],
            ['queue-accepted-document-downloads','run-document-download-worker']*2)

    def test_cli_rejects_scope_and_cadence_overrides_and_unknown_role(self):
        from scrapper.cli import _parser
        with contextlib.redirect_stderr(io.StringIO()):
            for flags in [['--role','all'],['--role','document-download-worker','--all-scopes'],
                          ['--role','credit-rating-worker-care','--throttle-seconds','0']]:
                with self.assertRaises(SystemExit):_parser().parse_args(['run-acquisition-role',*flags])
        dispatch=Mock()
        with self.assertRaises(ValueError):run_role('all',db_path='fixture',dispatch=dispatch)
        dispatch.assert_not_called()

    def test_cli_identity_refusal_precedes_role_startup(self):
        from scrapper import cli
        with patch.object(cli,'assert_configured_writable_targets',side_effect=RuntimeError('forbidden target')),patch(
            'scrapper.operations.workers.roles.run_role') as run:
            with self.assertRaises(RuntimeError):cli.main(['run-acquisition-role','--role','credit-rating-worker-care'])
            run.assert_not_called()

    def test_context_and_health_probe_are_restored_after_failed_batch(self):
        lifecycle=WorkerLifecycle();original=Mock();lifecycle.health_probe=original
        def broken(*,lifecycle):lifecycle.health_probe=Mock();raise ValueError('fault')
        with role_lifecycle(lifecycle):
            with self.assertRaises(ValueError):run_bounded(broken)
            self.assertIs(lifecycle.health_probe,original)
        def worker(*,lifecycle):return lifecycle
        fresh=run_bounded(worker)
        self.assertIsNot(fresh,lifecycle)
        with self.assertRaises(RuntimeError):
            def unfinished(argv):lifecycle.claimed(1);return 2
            run_role('credit-rating-worker-care',db_path='fixture',dispatch=unfinished,lifecycle=lifecycle,max_cycles=1)
        self.assertIs(lifecycle.health_probe,original)

    def test_real_cli_reopens_connections_and_retains_report_output_each_batch(self):
        from scrapper import cli
        with tempfile.TemporaryDirectory() as directory:
            report=Path(directory)/'queue.json';connections=[];lifecycles=[]
            def connect(*args):
                conn=Mock();connections.append(conn);return conn
            def worker(conn,**kwargs):lifecycles.append(kwargs['lifecycle']);return {'processed_job_count':0}
            lifecycle=WorkerLifecycle();lifecycle.wait=Mock(return_value=False)
            with patch.object(cli,'connect',side_effect=connect),patch.object(cli,'init_db'),patch.object(
                cli,'run_document_download_worker',side_effect=worker),patch.object(cli,'write_download_queue_report',
                return_value=({'bytes':{}},report)),patch.object(cli,'assert_configured_writable_targets'),patch(
                'scrapper.config.DOCUMENT_DOWNLOAD_EXPORTS',Path(directory)),contextlib.redirect_stdout(io.StringIO()) as stream:
                run_role('rating-recovery-download',db_path='fixture',lifecycle=lifecycle,max_cycles=2)
            self.assertEqual(len(connections),2)
            for conn in connections:conn.close.assert_called_once()
            self.assertEqual(lifecycles,[lifecycle]*2)
            self.assertEqual(len(stream.getvalue().splitlines()),2)

    def test_wait_health_uses_fresh_readonly_connection_and_real_schema(self):
        from scrapper.operations.workers.roles import idle_health
        from scrapper.tests.database_fixtures import prepare_database
        from scrapper.db.database import connect as real_connect
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'fixture.db';prepare_database(db)
            lifecycle=WorkerLifecycle();lifecycle.beat('WAITING')
            before=db.read_bytes();connections=[]
            def connection(*args,**kwargs):
                self.assertTrue(kwargs['read_only'])
                conn=real_connect(*args,**kwargs);connections.append(conn);return conn
            with patch('scrapper.db.database.connect',side_effect=connection),patch(
                'scrapper.documents.store.assert_document_store_ready',return_value=True):
                for _ in range(2):
                    result=idle_health(db,'rating',lifecycle,lifecycle.snapshot())
                    self.assertTrue(result['healthy']);self.assertTrue(result['waiting'])
            self.assertEqual(before,db.read_bytes());self.assertEqual(len(connections),2)
            for conn in connections:
                with self.assertRaises(Exception):conn.execute('SELECT 1')

    def test_os_sigterm_and_sigint_drain_idle_and_active_role(self):
        # Child runs the production role loop/lifecycle. Pipe barrier makes
        # signal timing deterministic; no source/DB writes or HTTP needed.
        script = """
import os,signal,sys
from scrapper.operations.workers.roles import run_role
from scrapper.operations.workers.lifecycle import run_bounded
active=sys.argv[1]=='active'
def dispatch(argv):
    def worker(*,lifecycle):
        if active:lifecycle.claimed(1)
        print('READY',flush=True)
        if active:
            sys.stdin.readline()
            assert lifecycle.is_set()
            lifecycle.finished()
            print('FINISHED',flush=True)
        return 0
    return run_bounded(worker)
result=run_role('credit-rating-worker-care',db_path='/tmp/nonexistent-signal-fixture',dispatch=dispatch)
assert result['cycles']==1 and result['status']=='drained'
print('DRAINED',flush=True)
"""
        for state in ('idle','active'):
            for sig in (signal.SIGTERM,signal.SIGINT):
                with self.subTest(state=state,signal=sig):
                    proc=subprocess.Popen([sys.executable,'-B','-c',script,state],stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    try:
                        self.assertEqual(proc.stdout.readline().strip(),'READY')
                        os.kill(proc.pid,sig)
                        stdout,stderr=proc.communicate('\n',timeout=5)
                        self.assertEqual(proc.returncode,0,stderr)
                        self.assertIn('DRAINED',stdout)
                        if state=='active':self.assertIn('FINISHED',stdout)
                    finally:
                        if proc.poll() is None:proc.kill();proc.communicate()
