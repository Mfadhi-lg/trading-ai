"""C23.2 explicit-scope, fairness and real fenced-queue contracts."""
import contextlib
import io
import sqlite3
import unittest
from unittest.mock import patch, Mock
from scrapper.operations.workers.generic_scopes import ScopeClaims, validate_scopes
from scrapper.tests import test_document_download_queue as fixtures
from scrapper.tests.test_document_download_queue import NOW, FakeHealth
from scrapper.db.document_download_queue_repo import claim_download_job, materialize_download_queue
from scrapper.pipeline.run_document_download_worker import run_document_download_worker
from scrapper.operations.workers.lifecycle import WorkerLifecycle
from scrapper.cli import _parser

A = "accepted-screener-concall"
R = "da06-rating-recovery"


class TurnsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE artifact_download_scopes(scope_id TEXT)")
        self.conn.executemany("INSERT INTO artifact_download_scopes VALUES(?)", [(A,), (R,)])
    def tearDown(self): self.conn.close()
    def test_weighted_fairness_and_host_filters(self):
        seen = []
        def claim(conn, **kw):
            seen.append(kw)
            return {"scope_id": kw["scope_id"]}
        picker = ScopeClaims(self.conn, (A, R), claim)
        for _ in range(40):
            picker(self.conn, scope_id=None, artifact_types=(), excluded_hosts=("busy.example",))
        self.assertEqual([x["scope_id"] for x in seen], [A,A,A,R] * 10)
        self.assertTrue(all(x["excluded_hosts"] == ("busy.example",) for x in seen))
        self.assertEqual(picker.counts, {A:30, R:10})
    def test_empty_or_blocked_lane_does_not_stall_other(self):
        seen = []
        def claim(conn, **kw):
            seen.append(kw["scope_id"])
            return {"scope_id":R} if kw["scope_id"] == R else None
        picker = ScopeClaims(self.conn, (A,R), claim)
        self.assertEqual([picker(self.conn)["scope_id"] for _ in range(8)], [R]*8)
        self.assertEqual(picker.counts, {A:0,R:8})
    def test_type_filter_cannot_expand_empty_intersection(self):
        seen=[]
        def claim(conn, **kw): seen.append(kw); return {"scope_id":kw["scope_id"]}
        picker=ScopeClaims(self.conn,(A,R),claim)
        self.assertIsNone(picker(self.conn,artifact_types=("recording",)))
        self.assertEqual(seen,[])
        self.assertEqual(picker(self.conn,artifact_types=("credit_rating",))["scope_id"],R)
        self.assertEqual(seen[0]["artifact_types"],("credit_rating",))
    def test_stop_during_empty_probe_does_not_claim_other_scope(self):
        stopped = [False]
        def claim(conn, **kwargs): stopped[0] = True; return None
        picker=ScopeClaims(self.conn,(A,R),claim,stopped=lambda:stopped[0])
        self.assertIsNone(picker(self.conn))
        self.assertEqual(picker.counts,{A:0,R:0})
    def test_invalid_allowlists_fail_before_claim(self):
        for scopes in [(), (None,), (A,A), ("pilot",), (A,"browser")]:
            with self.assertRaises(ValueError): validate_scopes(scopes)
        self.conn.execute("DELETE FROM artifact_download_scopes WHERE scope_id=?",(R,))
        with self.assertRaises(ValueError): ScopeClaims(self.conn,(A,R),lambda *_:self.fail())
    def test_cli_dispatch_and_reports_are_allowlist_scoped(self):
        import json, tempfile
        from pathlib import Path
        from scrapper import cli
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"report.json"
            stream=io.StringIO()
            with patch.object(cli,"connect",return_value=Mock()) as connect,patch.object(cli,"init_db"),patch.object(cli,"run_document_download_worker",return_value={"processed_job_count":0,"outcome_counts":{}}) as worker,patch.object(cli,"write_download_queue_report",return_value=({"bytes":{"saved":0}},output)) as report,contextlib.redirect_stdout(stream),contextlib.redirect_stderr(io.StringIO()):
                status=cli.main(["run-document-download-worker","--eligible-scope",A,"--eligible-scope",R,"--output",str(output)])
            self.assertEqual(status,0)
            self.assertEqual(worker.call_args.kwargs["eligible_scopes"],(A,R))
            self.assertEqual([call.kwargs["scope_id"] for call in report.call_args_list],[A,R])
            self.assertEqual(set(json.loads(output.read_text())),{A,R})
            self.assertEqual(set(json.loads(stream.getvalue())["byte_report"]),{A,R})
            connect.return_value.close.assert_called_once()
        with patch.object(cli,"connect") as connect,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["run-document-download-worker","--eligible-scope",A,"--eligible-scope",A]),2)
            connect.assert_not_called()
    def test_cli_requires_explicit_exclusive_selection(self):
        parser=_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            for flags in [[],["--scope-id",A,"--eligible-scope",R],["--all-scopes","--eligible-scope",A],["--eligible-scope","pilot"]]:
                with self.assertRaises(SystemExit): parser.parse_args(["run-document-download-worker",*flags])
        args=parser.parse_args(["run-document-download-worker","--eligible-scope",A,"--eligible-scope",R])
        self.assertEqual(args.eligible_scopes,[A,R])


class RealQueueTests(unittest.TestCase):
    setUp = fixtures.DocumentDownloadQueueDA05Tests.setUp
    tearDown = fixtures.DocumentDownloadQueueDA05Tests.tearDown
    register = fixtures.DocumentDownloadQueueDA05Tests.register
    def prepare(self):
        self.register(A,("transcript","presentation"))
        self.register(R,("credit_rating",))
        self.register("excluded-pilot",("presentation",))
        for scope in [A,R,"excluded-pilot"]:
            materialize_download_queue(self.conn,scope_id=scope,created_at_utc=NOW)
    def test_real_sql_weighted_fairness_under_saturated_backlog(self):
        self.prepare()
        self.conn.execute("DELETE FROM artifact_download_queue");self.conn.commit()
        for scope,kind in [(A,"presentation"),(R,"credit_rating")]:
            artifact=self.conn.execute("SELECT artifact_id FROM document_artifacts WHERE artifact_type=?",(kind,)).fetchone()[0]
            with self.conn:
                self.conn.executemany("INSERT INTO artifact_download_queue(artifact_id,requested_url,target_host,scope_id,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?)",
                    [(artifact,f"https://h{i}.{kind}.example/item",f"h{i}.{kind}.example",scope,NOW,NOW) for i in range(40)])
        picker=ScopeClaims(self.conn,(A,R),claim_download_job)
        jobs=[picker(self.conn,worker="common",now_utc=NOW) for _ in range(40)]
        self.assertEqual([job["scope_id"] for job in jobs],[A,A,A,R]*10)
        self.assertEqual(picker.counts,{A:30,R:10})
        self.assertEqual(len({job["download_job_id"] for job in jobs}),40)
        self.assertTrue(all(job["attempt_count"]==1 for job in jobs))
    def test_actual_claims_never_touch_excluded_scope(self):
        self.prepare()
        before=[tuple(r) for r in self.conn.execute("SELECT * FROM artifact_download_queue WHERE scope_id='excluded-pilot'")]
        picker=ScopeClaims(self.conn,(A,R),claim_download_job)
        jobs=[]
        for _ in range(12):
            job=picker(self.conn,worker="common",now_utc=NOW,excluded_hosts=())
            if job is None: break
            jobs.append(job)
        self.assertTrue(jobs)
        self.assertTrue(all(j["scope_id"] in (A,R) for j in jobs))
        self.assertTrue(all(j["claim_token"] and j["lease_owner"]=="common" for j in jobs))
        after=[tuple(r) for r in self.conn.execute("SELECT * FROM artifact_download_queue WHERE scope_id='excluded-pilot'")]
        self.assertEqual(before,after)
    def test_two_instances_keep_exclusive_fenced_ownership(self):
        self.prepare()
        one=ScopeClaims(self.conn,(A,R),claim_download_job)
        two=ScopeClaims(self.conn,(A,R),claim_download_job)
        first=one(self.conn,worker="one",now_utc=NOW)
        second=two(self.conn,worker="two",now_utc=NOW)
        self.assertIsNotNone(first)
        if second is not None:
            self.assertNotEqual(first["download_job_id"],second["download_job_id"])
            self.assertNotEqual(first["claim_token"],second["claim_token"])
    def test_common_workflow_applies_job_policy_and_shared_permits(self):
        self.prepare()
        calls=[]
        from scrapper.pipeline import run_document_download_worker as hooks
        def failed(url, **kwargs):
            calls.append(kwargs["session"])
            raise RuntimeError("offline injected transport failure")
        with patch.object(hooks,"download_url",failed):
            result=run_document_download_worker(self.conn,eligible_scopes=(A,),limit=2,
                throttle_seconds=0,now=lambda:NOW,document_store=self.root,
                snapshot_root=self.root/"bytes",health_check=FakeHealth)
        self.assertTrue(calls)
        self.assertEqual(result["eligible_scopes"],[A])
        self.assertEqual(result["scope_claim_counts"][A],result["processed_job_count"])
        self.assertTrue(all(type(session).__name__=="HostSession" for session in calls))
        self.assertTrue(all(row[0]!="LEASED" for row in self.conn.execute("SELECT status FROM artifact_download_queue WHERE scope_id=?",(A,))))
    def test_common_success_uses_real_permits_and_preserves_content_lineage(self):
        self.prepare()
        from scrapper.operations.workers.host_requests import HostSession
        from scrapper.pipeline import run_document_download_worker as hooks
        from datetime import datetime
        urls=[row[0] for row in self.conn.execute("SELECT requested_url FROM artifact_download_queue WHERE scope_id=?",(A,))]
        fake=fixtures.FakeSession({url:[fixtures.FakeResponse(fixtures.PDF_BYTES,url=url)] for url in urls})
        real_policy=hooks._scope_policy
        def adapter(*args,**kwargs):
            return HostSession(*args,**kwargs,offline_session=fake)
        with patch("scrapper.operations.workers.host_requests.HostSession",side_effect=adapter),patch.object(hooks,"_scope_policy",wraps=real_policy) as policy:
            result=run_document_download_worker(self.conn,eligible_scopes=(A,),limit=1,
                throttle_seconds=0,now=lambda:NOW,document_store=self.root,
                snapshot_root=self.root/"bytes",health_check=FakeHealth)
        self.assertEqual(result["outcome_counts"],{"validated":1})
        self.assertIn(((self.conn,A),{}),[(call.args,call.kwargs) for call in policy.call_args_list])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artifact_contents").fetchone()[0],1)
        self.assertTrue(fake.calls)
        self.assertTrue(all(row[0]=="RELEASED" for row in self.conn.execute("SELECT status FROM acquisition_host_permits")))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artifact_download_queue WHERE status='SUCCEEDED'").fetchone()[0],1)
        self.assertFalse(self.conn.in_transaction)
    def test_drain_before_claim_and_invalid_selection_are_inert(self):
        self.prepare()
        before=[tuple(r) for r in self.conn.execute("SELECT * FROM artifact_download_queue")]
        lifecycle=WorkerLifecycle();lifecycle.set()
        result=run_document_download_worker(self.conn,eligible_scopes=(A,R),lifecycle=lifecycle,
            document_store=self.root,health_check=FakeHealth)
        self.assertEqual(result["processed_job_count"],0)
        with self.assertRaises(ValueError):
            run_document_download_worker(self.conn,eligible_scopes=(A,),scope_id=R,
                document_store=self.root,health_check=lambda _:self.fail("health called"))
        self.assertEqual(before,[tuple(r) for r in self.conn.execute("SELECT * FROM artifact_download_queue")])
