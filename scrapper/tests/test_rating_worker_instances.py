"""Configured roles exercise the real queue, resolver, permits and legacy CLI."""
import unittest
from unittest.mock import patch
from scrapper.operations.workers.rating import RatingWorker, INSTANCES
from scrapper.tests import test_host_request_executors as contracts


class RatingInstanceTests(unittest.TestCase):
    def setUp(self):
        contracts.RatingWorkerIntegrationTests.setUp(self)
        # Worker partitions exercise the current default policy, not terms holds.
        self.fixture.doCleanups()
        from pathlib import Path
        migration = Path(__file__).parents[1] / 'db/migrations/076_retire_crisil_icra_terms_holds.sql'
        self.conn.executescript(migration.read_text())
    tearDown = contracts.RatingWorkerIntegrationTests.tearDown

    def run_worker(self, instance='care', **kwargs):
        from scrapper.tests.test_document_download_queue import FakeHealth
        with patch('scrapper.pipeline.credit_rating_downloads.assert_document_store_ready', FakeHealth):
            return RatingWorker(instance).run(self.conn, limit=1, throttle_seconds=4,
                snapshot_root=self.root/'bytes', document_store=self.root, **kwargs)

    test_wait_refunds_claim = contracts.RatingWorkerIntegrationTests.test_rating_wait_refunds_claim_without_resolution_attempt
    test_success_same_artifact = contracts.RatingWorkerIntegrationTests.test_rating_shared_success_keeps_logical_artifact
    test_no_browser_bypass = contracts.RatingWorkerIntegrationTests.test_enabled_rating_never_invokes_unpermitted_browser_fallback
    test_partial_resolution = contracts.RatingWorkerIntegrationTests.test_partial_rating_resolution_keeps_original_html_and_dispatched_count

    def test_other_instances_do_not_claim_care(self):
        before=[tuple(r) for r in self.conn.execute('SELECT * FROM credit_rating_download_queue')]
        for instance in ('other', 'india-ratings'):
            result=self.run_worker(instance)
            self.assertEqual(result['processed_job_count'],0)
            self.assertEqual(result['allowed_hosts'],sorted(INSTANCES[instance].hosts))
        self.assertEqual(before,[tuple(r) for r in self.conn.execute('SELECT * FROM credit_rating_download_queue')])

    def test_each_configured_host_claims_only_its_partition(self):
        from scrapper.pipeline.credit_rating_downloads import _claim_job
        from scrapper.db.repo import utc_now
        for name, config in INSTANCES.items():
            for host in config.hosts:
                with self.conn:
                    self.conn.execute("UPDATE credit_rating_download_queue SET target_host=?, status='PENDING', lease_owner=NULL, lease_expires_at_utc=NULL",(host,))
                for other, excluded in INSTANCES.items():
                    if other != name:
                        self.assertIsNone(_claim_job(self.conn,worker='excluded',now=utc_now(),allowed_hosts=excluded.hosts))
                job=_claim_job(self.conn,worker=name,now=utc_now(),allowed_hosts=config.hosts)
                self.assertEqual(job['target_host'],host)

    def test_unknown_and_host_override_fail_closed(self):
        for name in ('', 'all', None, 'crisil'):
            with self.assertRaises(ValueError): RatingWorker(name)
        for kwargs in ({'allowed_hosts':()}, {'shared_permits':False}):
            with self.assertRaises(ValueError): self.run_worker(**kwargs)

    def test_drain_before_claim(self):
        from scrapper.operations.workers.lifecycle import WorkerLifecycle
        lifecycle=WorkerLifecycle(); lifecycle.set()
        before=[tuple(r) for r in self.conn.execute('SELECT * FROM credit_rating_download_queue')]
        self.assertEqual(self.run_worker(lifecycle=lifecycle)['processed_job_count'],0)
        self.assertEqual(before,[tuple(r) for r in self.conn.execute('SELECT * FROM credit_rating_download_queue')])

    def test_legacy_and_instance_cli_selection(self):
        from scrapper.cli import _parser
        parser=_parser()
        args=parser.parse_args(['run-credit-rating-download-worker','--rating-instance','other'])
        self.assertEqual(args.rating_instance,'other'); self.assertEqual(args.host,[])
        args=parser.parse_args(['run-credit-rating-download-worker','--host','www.careratings.com','--host','www.acuite.in'])
        self.assertIsNone(args.rating_instance); self.assertEqual(len(args.host),2)
        with self.assertRaises(SystemExit): parser.parse_args(['run-credit-rating-download-worker','--rating-instance','care','--host','x'])
        with self.assertRaises(SystemExit): parser.parse_args(['run-credit-rating-download-worker','--rating-instance','all'])
