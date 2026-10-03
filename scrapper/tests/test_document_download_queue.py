from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.cli import _parser
from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import persist_discovery_plan
from scrapper.db.document_download_queue_repo import (
    claim_download_job,
    materialize_download_queue,
)
from scrapper.documents.download_scope import (
    ScopeCompany,
    _validate_p0,
    register_download_scope,
)
from scrapper.pipeline.credit_rating_downloads import queue_credit_rating_downloads
from scrapper.pipeline.run_document_download_worker import (
    _record_transport_host_failure,
    build_download_queue_report,
    run_document_download_worker,
)
from scrapper.documents.downloader import DownloadResult
from scrapper.pipeline.queue_document_downloads import queue_document_downloads
from scrapper.sources.documents.discovery import build_discovery_plan


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
MANIFEST = FIXTURES / "d01_discovery_manifest.json"
PDF_BYTES = (FIXTURES / "d02_sample.pdf").read_bytes()
PPTX_BYTES = (FIXTURES / "d03_presentation.pptx").read_bytes()
HTML_BYTES = (FIXTURES / "d02_html_error.pdf").read_bytes()
MP3_BYTES = (FIXTURES / "d02_sample.mp3").read_bytes()
NOW = "2026-08-14T10:00:00+00:00"


class FakeHealth:
    def __init__(self, root: Path) -> None:
        self.root = root

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "total_bytes": 1_000_000,
            "free_bytes": 900_000,
            "required_free_bytes": 1,
        }


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        status_code: int = 200,
        content_type: str = "application/pdf",
    ) -> None:
        self.body = body
        self.url = url
        self.status_code = status_code
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }
        self.history: tuple[Any, ...] = ()

    def iter_content(self, chunk_size: int) -> Any:
        yield self.body

    def close(self) -> None:
        pass


class FakeSession:
    def __init__(self, responses: dict[str, list[FakeResponse]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.max_redirects = 30

    def get(self, url: str, **_: Any) -> FakeResponse:
        self.calls.append(url)
        responses = self.responses[url]
        if not responses:
            raise AssertionError(f"Unexpected repeated request: {url}")
        return responses.pop(0)

    def close(self) -> None:
        pass


class DocumentDownloadQueueDA05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.conn = connect(self.db_path)
        init_db(self.conn, apply_migrations=True)
        persist_discovery_plan(
            self.conn,
            build_discovery_plan(MANIFEST, requested_symbol="LAURUSLABS"),
            observed_at_utc=NOW,
            snapshot_root=self.root / "discovery",
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def register(
        self,
        scope_id: str,
        artifact_types: tuple[str, ...],
        artifact_ids: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        return register_download_scope(
            self.conn,
            scope_id=scope_id,
            companies=("LAURUSLABS",),
            artifact_types=artifact_types,
            artifact_ids=artifact_ids,
            enforce_pilot_composition=False,
            require_explicit_artifacts=False,
            created_at_utc=NOW,
        )

    def replace_credit_rating_url(self, url: str) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_sources SET document_url=?
                     WHERE artifact_id IN (
                       SELECT artifact_id FROM document_artifacts
                        WHERE artifact_type='credit_rating'
                     )""",
                (url,),
            )

    def test_production_p0_composition_is_enforced_before_queueing(self) -> None:
        with self.assertRaisesRegex(ValueError, "30 to 50"):
            register_download_scope(
                self.conn,
                scope_id="too-small",
                companies=("LAURUSLABS",),
                created_at_utc=NOW,
            )

    def test_acquisition_p0_needs_no_focus_labels_or_archetypes(self) -> None:
        companies = tuple(
            ScopeCompany(symbol=f"C{company_id}", company_id=company_id)
            for company_id in range(1, 41)
        )
        _validate_p0(
            companies,
            ("transcript", "presentation", "credit_rating"),
            (1, 2, 3),
            enforce_pilot_composition=True,
        )

    def test_scope_company_can_be_selected_by_durable_company_id(self) -> None:
        company_id = int(
            self.conn.execute(
                "SELECT company_id FROM document_artifacts ORDER BY artifact_id LIMIT 1"
            ).fetchone()[0]
        )
        artifact_id = int(
            self.conn.execute(
                "SELECT artifact_id FROM document_artifacts ORDER BY artifact_id LIMIT 1"
            ).fetchone()[0]
        )
        artifact_type = str(
            self.conn.execute(
                "SELECT artifact_type FROM document_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()[0]
        )
        scope = register_download_scope(
            self.conn,
            scope_id="p0-company-id",
            companies=({"company_id": company_id},),
            artifact_types=(artifact_type,),
            artifact_ids=(artifact_id,),
            enforce_pilot_composition=False,
            created_at_utc=NOW,
        )
        self.assertEqual(scope["company_count"], 1)
        row = self.conn.execute(
            """SELECT company_id, outcome_label, archetype
                 FROM artifact_download_scope_companies WHERE scope_id=?""",
            ("p0-company-id",),
        ).fetchone()
        self.assertEqual(int(row["company_id"]), company_id)
        self.assertIsNone(row["outcome_label"])
        self.assertIsNone(row["archetype"])

    def test_database_startup_does_not_touch_unapproved_external_store(self) -> None:
        store = self.root / "isolated-data"
        documents = self.root / "missing-sentinel-documents"
        documents.mkdir()
        environment = dict(os.environ)
        environment.update(
            {
                "SCRAPER_STORE_PATH": str(store),
                "SCRAPER_DB_PATH": str(store / "scraper.db"),
                "SCRAPER_DOCUMENT_STORE_PATH": str(documents),
                "DOCUMENT_REQUIRE_SENTINEL": "1",
            }
        )
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from scrapper.db.database import connect; c=connect(); c.close()",
            ],
            check=True,
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
        )
        self.assertEqual(list(documents.iterdir()), [])

    def test_scope_and_queue_materialization_are_idempotent(self) -> None:
        first_scope = self.register("p0-idempotent", ("transcript", "presentation"))
        second_scope = self.register("p0-idempotent", ("transcript", "presentation"))
        self.assertTrue(first_scope["created"])
        self.assertFalse(second_scope["created"])

        first = materialize_download_queue(
            self.conn, scope_id="p0-idempotent", created_at_utc=NOW
        )
        second = materialize_download_queue(
            self.conn, scope_id="p0-idempotent", created_at_utc=NOW
        )
        self.assertGreater(first["created_job_count"], 0)
        self.assertEqual(second["created_job_count"], 0)
        self.assertEqual(first["total_job_count"], second["total_job_count"])
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM artifact_download_queue AS queue
                   JOIN document_artifacts AS artifact USING (artifact_id)
                   WHERE queue.scope_id=? AND artifact.artifact_type='recording'""",
                ("p0-idempotent",),
            ).fetchone()[0],
            0,
        )

    def test_explicit_artifact_selection_prevents_historical_scope_expansion(self) -> None:
        artifacts = list(
            self.conn.execute(
                """SELECT artifact_id, artifact_type FROM document_artifacts
                    ORDER BY artifact_id"""
            )
        )
        self.assertGreaterEqual(len(artifacts), 2)
        selected_id = int(artifacts[0]["artifact_id"])
        self.register(
            "p0-explicit-artifact",
            (str(artifacts[0]["artifact_type"]),),
            artifact_ids=(selected_id,),
        )
        materialize_download_queue(
            self.conn, scope_id="p0-explicit-artifact", created_at_utc=NOW
        )
        self.assertEqual(
            {
                int(row["artifact_id"])
                for row in self.conn.execute(
                    """SELECT artifact_id FROM artifact_download_queue
                        WHERE scope_id=?""",
                    ("p0-explicit-artifact",),
                )
            },
            {selected_id},
        )

    def test_queue_dry_run_leaves_no_scope_or_jobs(self) -> None:
        manifest = self.root / "p0.json"
        manifest.write_text(
            json.dumps(
                {
                    "scope_id": "p0-dry-run",
                    "scope_kind": "P0",
                    "artifact_types": ["transcript"],
                    "artifact_ids": [
                        int(
                            self.conn.execute(
                                """SELECT artifact_id FROM document_artifacts
                                    WHERE artifact_type='transcript' LIMIT 1"""
                            ).fetchone()[0]
                        )
                    ],
                    "companies": [{"symbol": "LAURUSLABS"}],
                }
            ),
            encoding="utf-8",
        )
        result = queue_document_downloads(
            manifest_path=manifest,
            db_path=self.db_path,
            dry_run=True,
            enforce_pilot_composition=False,
        )
        self.assertTrue(result["queue"]["dry_run"])
        self.assertGreater(result["preview"]["target_count"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_download_scopes WHERE scope_id='p0-dry-run'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_download_queue WHERE scope_id='p0-dry-run'"
            ).fetchone()[0],
            0,
        )

    def test_expired_lease_is_reclaimed_without_an_extra_job(self) -> None:
        self.register("p0-lease", ("credit_rating",))
        materialize_download_queue(self.conn, scope_id="p0-lease", created_at_utc=NOW)
        first = claim_download_job(
            self.conn,
            worker="worker-a",
            now_utc=NOW,
            lease_seconds=60,
            scope_id="p0-lease",
        )
        self.assertIsNotNone(first)
        reclaimed = claim_download_job(
            self.conn,
            worker="worker-b",
            now_utc="2026-08-14T10:01:01+00:00",
            lease_seconds=60,
            scope_id="p0-lease",
        )
        self.assertEqual(reclaimed["download_job_id"], first["download_job_id"])
        self.assertEqual(reclaimed["attempt_count"], 2)
        self.assertEqual(reclaimed["lease_owner"], "worker-b")

    def test_claim_can_be_restricted_to_explicit_artifact_types(self) -> None:
        self.register(
            "p0-type-filter", ("transcript", "presentation", "credit_rating")
        )
        materialize_download_queue(
            self.conn, scope_id="p0-type-filter", created_at_utc=NOW
        )
        claimed = claim_download_job(
            self.conn,
            worker="transcript-worker",
            now_utc=NOW,
            scope_id="p0-type-filter",
            artifact_types=("transcript",),
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["artifact_type"], "transcript")

    def test_claim_prioritizes_the_latest_reporting_period(self) -> None:
        artifacts = self.conn.execute(
            """SELECT artifact_id, artifact_type
                 FROM document_artifacts
                WHERE artifact_type IN ('transcript', 'presentation')
                ORDER BY artifact_id
                LIMIT 2"""
        ).fetchall()
        self.assertEqual(len(artifacts), 2)
        older_id = int(artifacts[0]["artifact_id"])
        latest_id = int(artifacts[1]["artifact_id"])
        with self.conn:
            self.conn.execute(
                "UPDATE document_artifacts SET period_end='2025-12-31' WHERE artifact_id=?",
                (older_id,),
            )
            self.conn.execute(
                "UPDATE document_artifacts SET period_end='2026-06-30' WHERE artifact_id=?",
                (latest_id,),
            )
        self.register(
            "p0-latest-period",
            tuple({str(row["artifact_type"]) for row in artifacts}),
            (older_id, latest_id),
        )
        materialize_download_queue(
            self.conn, scope_id="p0-latest-period", created_at_utc=NOW
        )

        claimed = claim_download_job(
            self.conn,
            worker="latest-first-worker",
            now_utc=NOW,
            scope_id="p0-latest-period",
        )

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["artifact_id"], latest_id)

    def test_resolved_quarter_precedes_a_recently_observed_unresolved_item(self) -> None:
        artifacts = self.conn.execute(
            """SELECT artifact_id, artifact_type
                 FROM document_artifacts
                WHERE artifact_type IN ('transcript', 'presentation')
                ORDER BY artifact_id
                LIMIT 2"""
        ).fetchall()
        resolved_id = int(artifacts[0]["artifact_id"])
        unresolved_id = int(artifacts[1]["artifact_id"])
        with self.conn:
            self.conn.execute(
                "UPDATE document_artifacts SET period_end='2026-06-30' WHERE artifact_id=?",
                (resolved_id,),
            )
            self.conn.execute(
                """UPDATE document_artifacts
                      SET period_end=NULL, fiscal_year=NULL, fiscal_quarter=NULL,
                          event_date=NULL, published_at_utc=NULL,
                          first_observed_at_utc='2099-12-31T00:00:00+00:00'
                    WHERE artifact_id=?""",
                (unresolved_id,),
            )
        self.register(
            "p0-resolved-period-first",
            tuple({str(row["artifact_type"]) for row in artifacts}),
            (resolved_id, unresolved_id),
        )
        materialize_download_queue(
            self.conn, scope_id="p0-resolved-period-first", created_at_utc=NOW
        )

        claimed = claim_download_job(
            self.conn,
            worker="resolved-period-worker",
            now_utc=NOW,
            scope_id="p0-resolved-period-first",
        )

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["artifact_id"], resolved_id)

    def test_claim_does_not_lease_same_host_concurrently(self) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_sources
                      SET document_url='https://same-host.example/document/'
                                       || artifact_source_id
                    WHERE artifact_id IN (
                      SELECT artifact_id FROM document_artifacts
                       WHERE artifact_type IN ('transcript','presentation')
                    )"""
            )
        self.register("p0-host-lease", ("transcript", "presentation"))
        materialize_download_queue(
            self.conn, scope_id="p0-host-lease", created_at_utc=NOW
        )
        first = claim_download_job(
            self.conn,
            worker="worker-a",
            now_utc=NOW,
            lease_seconds=600,
            scope_id="p0-host-lease",
        )
        self.assertIsNotNone(first)
        second = claim_download_job(
            self.conn,
            worker="worker-b",
            now_utc=NOW,
            lease_seconds=600,
            scope_id="p0-host-lease",
        )
        self.assertIsNone(second)

    def test_three_transport_failures_open_persistent_host_circuit(self) -> None:
        result = DownloadResult(
            requested_url="https://dead-host.example/document.pdf",
            final_url=None,
            http_status=None,
            response_headers={},
            redirects=(),
            declared_media_type=None,
            detected_media_type=None,
            body=None,
            byte_size=None,
            result_status="network_error",
            elapsed_ms=100,
            error_code="ConnectionError",
        )
        self.assertIsNone(
            _record_transport_host_failure(
                self.conn,
                host="dead-host.example",
                result=result,
                scope_id=None,
                now_utc=NOW,
            )
        )
        self.assertIsNone(
            _record_transport_host_failure(
                self.conn,
                host="dead-host.example",
                result=result,
                scope_id=None,
                now_utc=NOW,
            )
        )
        blocked_until = _record_transport_host_failure(
            self.conn,
            host="dead-host.example",
            result=result,
            scope_id=None,
            now_utc=NOW,
        )
        self.assertIsNotNone(blocked_until)
        health = self.conn.execute(
            "SELECT * FROM artifact_download_host_health WHERE host='dead-host.example'"
        ).fetchone()
        self.assertEqual(int(health["consecutive_blocks"]), 3)
        self.assertEqual(health["last_result_status"], "network_error")

    def test_crisil_generic_queue_is_policy_skipped_with_zero_http_calls(self) -> None:
        self.replace_credit_rating_url(
            "https://www.crisil.com/en/home/our-analysis/ratings/issuer.pdf"
        )
        self.register("p0-crisil-policy", ("credit_rating",))
        queued = materialize_download_queue(
            self.conn, scope_id="p0-crisil-policy", created_at_utc=NOW
        )
        session = FakeSession({})

        self.assertEqual(queued["policy_blocked_target_count"], 1)
        self.assertEqual(queued["status_counts"], {"SKIPPED_POLICY": 1})
        self.assertIsNone(
            claim_download_job(
                self.conn,
                worker="worker-crisil",
                now_utc=NOW,
                scope_id="p0-crisil-policy",
            )
        )
        result = run_document_download_worker(
            self.conn,
            scope_id="p0-crisil-policy",
            limit=1,
            throttle_seconds=0,
            document_store=self.root,
            session_factory=lambda: session,
            health_check=FakeHealth,
            worker="worker-crisil",
        )
        self.assertEqual(result["processed_job_count"], 0)
        self.assertEqual(session.calls, [])

    def test_genuine_rating_html_is_preserved_and_rendered_to_pdf(self) -> None:
        url = "https://www.indiaratings.co.in/pressrelease/123"
        self.replace_credit_rating_url(url)
        artifact_id = int(
            self.conn.execute(
                """SELECT artifact_id FROM document_artifacts
                    WHERE artifact_type='credit_rating' LIMIT 1"""
            ).fetchone()[0]
        )
        register_download_scope(
            self.conn,
            scope_id="html-rating",
            scope_kind="P1",
            companies=("LAURUSLABS",),
            artifact_types=("credit_rating",),
            artifact_ids=(artifact_id,),
            enforce_pilot_composition=False,
            created_at_utc=NOW,
        )
        materialize_download_queue(
            self.conn, scope_id="html-rating", created_at_utc=NOW
        )
        paragraph = (
            "Credit rating action assigns a stable outlook to the bank loan facility. "
            "The rating rationale discusses key rating drivers, liquidity and rating sensitivity. "
        )
        html = (
            "<html><head><title>Rating rationale</title></head><body>"
            "<h1>Laurus Labs Limited</h1><p>" + paragraph * 12 + "</p></body></html>"
        ).encode()
        session = FakeSession(
            {url: [FakeResponse(html, url=url, content_type="text/html")]}
        )

        result = run_document_download_worker(
            self.conn,
            scope_id="html-rating",
            limit=1,
            throttle_seconds=0,
            snapshot_root=self.root / "content",
            document_store=self.root,
            session_factory=lambda: session,
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="worker-html-rating",
        )

        self.assertEqual(result["outcome_counts"], {"validated": 1})
        self.assertEqual(
            self.conn.execute(
                """SELECT status FROM artifact_download_queue
                    WHERE scope_id='html-rating'"""
            ).fetchone()[0],
            "SUCCEEDED",
        )
        contents = self.conn.execute(
            """SELECT media_type,is_official,content_origin
                 FROM artifact_contents WHERE artifact_id=? ORDER BY content_id""",
            (artifact_id,),
        ).fetchall()
        self.assertEqual(
            [(row["media_type"], row["is_official"], row["content_origin"]) for row in contents],
            [
                ("text/html", 1, "publisher_original"),
                ("application/pdf", 0, "generated_derivative"),
            ],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT relationship_type FROM artifact_content_relationships"
            ).fetchone()[0],
            "rendered_pdf",
        )

    def test_crisilratings_generic_queue_is_policy_skipped_with_zero_http_calls(
        self,
    ) -> None:
        self.replace_credit_rating_url(
            "https://media.crisilratings.com/ratings/issuer.pdf"
        )
        self.register("p0-crisilratings-policy", ("credit_rating",))
        queued = materialize_download_queue(
            self.conn,
            scope_id="p0-crisilratings-policy",
            created_at_utc=NOW,
        )
        session = FakeSession({})

        self.assertEqual(queued["policy_blocked_target_count"], 1)
        self.assertEqual(queued["status_counts"], {"SKIPPED_POLICY": 1})
        self.assertIsNone(
            claim_download_job(
                self.conn,
                worker="worker-crisilratings",
                now_utc=NOW,
                scope_id="p0-crisilratings-policy",
            )
        )
        result = run_document_download_worker(
            self.conn,
            scope_id="p0-crisilratings-policy",
            limit=1,
            throttle_seconds=0,
            document_store=self.root,
            session_factory=lambda: session,
            health_check=FakeHealth,
            worker="worker-crisilratings",
        )
        self.assertEqual(result["processed_job_count"], 0)
        self.assertEqual(session.calls, [])

    def test_icra_generic_queue_is_policy_skipped_with_zero_http_calls(self) -> None:
        self.replace_credit_rating_url(
            "https://www.icra.in/Rationale/ShowRationaleReport?id=42"
        )
        self.register("p0-icra-policy", ("credit_rating",))
        queued = materialize_download_queue(
            self.conn, scope_id="p0-icra-policy", created_at_utc=NOW
        )
        session = FakeSession({})

        self.assertEqual(queued["policy_blocked_target_count"], 1)
        self.assertEqual(queued["status_counts"], {"SKIPPED_POLICY": 1})
        self.assertIsNone(
            claim_download_job(
                self.conn,
                worker="worker-icra",
                now_utc=NOW,
                scope_id="p0-icra-policy",
            )
        )
        result = run_document_download_worker(
            self.conn,
            scope_id="p0-icra-policy",
            limit=1,
            throttle_seconds=0,
            document_store=self.root,
            session_factory=lambda: session,
            health_check=FakeHealth,
            worker="worker-icra",
        )
        self.assertEqual(result["processed_job_count"], 0)
        self.assertEqual(session.calls, [])

    def test_claim_migrates_an_existing_blocked_row_to_policy_skip(self) -> None:
        self.replace_credit_rating_url("https://ratings.crisil.com/report.pdf")
        self.register("p0-policy-claim", ("credit_rating",))
        materialize_download_queue(
            self.conn, scope_id="p0-policy-claim", created_at_utc=NOW
        )
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_download_queue
                      SET status='PENDING', last_result_status=NULL, last_error=NULL
                    WHERE scope_id='p0-policy-claim'"""
            )

        claimed = claim_download_job(
            self.conn,
            worker="worker-policy-claim",
            now_utc=NOW,
            scope_id="p0-policy-claim",
        )

        self.assertIsNone(claimed)
        row = self.conn.execute(
            """SELECT status, attempt_count, last_result_status
                 FROM artifact_download_queue WHERE scope_id='p0-policy-claim'"""
        ).fetchone()
        self.assertEqual(row["status"], "SKIPPED_POLICY")
        self.assertEqual(row["attempt_count"], 0)
        self.assertEqual(
            row["last_result_status"], "PUBLISHER_TERMS_BLOCK_AUTOMATION"
        )

    def test_migration_closes_existing_generic_blocked_rows(self) -> None:
        self.replace_credit_rating_url("https://www.icra.in/report.pdf")
        self.register("p0-policy-migration", ("credit_rating",))
        materialize_download_queue(
            self.conn, scope_id="p0-policy-migration", created_at_utc=NOW
        )
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_download_queue
                      SET status='RETRYABLE_FAILURE', next_attempt_at_utc=?,
                          lease_owner='old-worker', lease_expires_at_utc=?
                    WHERE scope_id='p0-policy-migration'""",
                (NOW, NOW),
            )
            self.conn.execute(
                """DELETE FROM schema_migrations
                    WHERE version='043_generic_download_host_policy.sql'"""
            )

        init_db(self.conn, apply_migrations=True)

        row = self.conn.execute(
            """SELECT status, next_attempt_at_utc, lease_owner,
                      lease_expires_at_utc, last_result_status
                 FROM artifact_download_queue
                WHERE scope_id='p0-policy-migration'"""
        ).fetchone()
        self.assertEqual(row["status"], "SKIPPED_POLICY")
        self.assertIsNone(row["next_attempt_at_utc"])
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at_utc"])
        self.assertEqual(
            row["last_result_status"], "PUBLISHER_TERMS_BLOCK_AUTOMATION"
        )

    def test_migration_044_closes_existing_crisilratings_rows(self) -> None:
        with self.conn:
            self.conn.execute(
                """DELETE FROM schema_migrations
                    WHERE version='044_crisilratings_and_recording_safety.sql'"""
            )
            self.conn.execute(
                """DELETE FROM credit_rating_host_policy
                    WHERE host IN ('crisilratings.com', 'www.crisilratings.com')"""
            )
        self.replace_credit_rating_url(
            "https://www.crisilratings.com/ratings/old-job.pdf"
        )
        self.register("p0-policy-migration-044", ("credit_rating",))
        queued = materialize_download_queue(
            self.conn,
            scope_id="p0-policy-migration-044",
            created_at_utc=NOW,
        )
        self.assertEqual(queued["status_counts"], {"PENDING": 1})
        specialized = queue_credit_rating_downloads(self.conn, created_at_utc=NOW)
        self.assertEqual(specialized["PENDING"], 1)

        init_db(self.conn, apply_migrations=True)

        row = self.conn.execute(
            """SELECT status, next_attempt_at_utc, lease_owner,
                      lease_expires_at_utc, last_result_status
                 FROM artifact_download_queue
                WHERE scope_id='p0-policy-migration-044'"""
        ).fetchone()
        self.assertEqual(row["status"], "SKIPPED_POLICY")
        self.assertIsNone(row["next_attempt_at_utc"])
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_expires_at_utc"])
        self.assertEqual(
            row["last_result_status"], "PUBLISHER_TERMS_BLOCK_AUTOMATION"
        )
        specialized_row = self.conn.execute(
            """SELECT status, next_attempt_at_utc, lease_owner,
                      lease_expires_at_utc, last_result_status
                 FROM credit_rating_download_queue
                WHERE target_host='www.crisilratings.com'"""
        ).fetchone()
        self.assertEqual(specialized_row["status"], "SKIPPED_POLICY")
        self.assertIsNone(specialized_row["next_attempt_at_utc"])
        self.assertIsNone(specialized_row["lease_owner"])
        self.assertIsNone(specialized_row["lease_expires_at_utc"])
        self.assertEqual(
            specialized_row["last_result_status"],
            "PUBLISHER_TERMS_BLOCK_AUTOMATION",
        )

    def test_worker_requires_explicit_scope_or_all_scopes(self) -> None:
        parser = _parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["run-document-download-worker"])
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "run-document-download-worker",
                        "--scope-id",
                        "scope-a",
                        "--all-scopes",
                    ]
                )

        selected = parser.parse_args(
            ["run-document-download-worker", "--scope-id", "scope-a"]
        )
        all_scopes = parser.parse_args(
            ["run-document-download-worker", "--all-scopes"]
        )
        self.assertEqual(selected.scope_id, "scope-a")
        self.assertFalse(selected.all_scopes)
        self.assertIsNone(all_scopes.scope_id)
        self.assertTrue(all_scopes.all_scopes)

    def test_success_skips_same_artifact_urls_and_report_counts_unique_bytes(self) -> None:
        self.register("p0-success", ("presentation",))
        queued = materialize_download_queue(
            self.conn, scope_id="p0-success", created_at_utc=NOW
        )
        self.assertGreaterEqual(queued["total_job_count"], 2)
        urls = [
            row["requested_url"]
            for row in self.conn.execute(
                """SELECT requested_url FROM artifact_download_queue
                    WHERE scope_id=? ORDER BY download_job_id""",
                ("p0-success",),
            )
        ]
        responses = {
            url: [FakeResponse(PDF_BYTES, url=url)]
            for url in urls
        }
        session = FakeSession(responses)
        result = run_document_download_worker(
            self.conn,
            scope_id="p0-success",
            limit=10,
            throttle_seconds=0,
            snapshot_root=self.root / "content",
            document_store=self.root,
            session_factory=lambda: session,
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="worker-success",
        )
        self.assertEqual(result["processed_job_count"], 1)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            result["queue"]["status_counts"],
            {
                "SKIPPED_ALREADY_HAVE_CONTENT": len(urls) - 1,
                "SUCCEEDED": 1,
            },
        )
        report = build_download_queue_report(self.conn, scope_id="p0-success")
        self.assertEqual(report["bytes"]["attempt_count"], 1)
        self.assertEqual(report["bytes"]["response_bytes"], len(PDF_BYTES))
        self.assertEqual(report["bytes"]["unique_content_bytes"], len(PDF_BYTES))

        resumed = run_document_download_worker(
            self.conn,
            scope_id="p0-success",
            limit=10,
            throttle_seconds=0,
            document_store=self.root,
            session_factory=lambda: session,
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="worker-resumed",
        )
        self.assertEqual(resumed["processed_job_count"], 0)
        self.assertEqual(len(session.calls), 1)

    def test_host_block_defers_same_host_and_continues_another_host(self) -> None:
        self.register("p0-hosts", ("transcript", "presentation", "credit_rating"))
        materialize_download_queue(self.conn, scope_id="p0-hosts", created_at_utc=NOW)
        first_host = str(
            self.conn.execute(
                """SELECT target_host
                     FROM artifact_download_queue
                    WHERE scope_id=?
                    GROUP BY target_host
                   HAVING COUNT(*) > 1
                    ORDER BY MIN(download_job_id)
                    LIMIT 1""",
                ("p0-hosts",),
            ).fetchone()[0]
        )
        with self.conn:
            self.conn.execute(
                """UPDATE document_artifacts SET period_end='2099-12-31'
                    WHERE artifact_id IN (
                      SELECT artifact_id FROM artifact_download_queue
                       WHERE scope_id=? AND target_host=?
                    )""",
                ("p0-hosts", first_host),
            )
        rows = list(
            self.conn.execute(
                """SELECT queue.download_job_id, queue.requested_url,
                          queue.target_host
                     FROM artifact_download_queue AS queue
                     JOIN document_artifacts AS artifact USING (artifact_id)
                    WHERE queue.scope_id=?
                    ORDER BY queue.priority,
                             CASE
                               WHEN artifact.period_end IS NOT NULL
                                 OR (
                                   artifact.fiscal_year IS NOT NULL
                                   AND artifact.fiscal_quarter IS NOT NULL
                                 )
                               THEN 0 ELSE 1
                             END,
                             artifact.period_end DESC,
                             artifact.fiscal_year DESC,
                             artifact.fiscal_quarter DESC,
                             COALESCE(
                               artifact.event_date,
                               substr(artifact.published_at_utc, 1, 10),
                               substr(artifact.first_observed_at_utc, 1, 10),
                               ''
                             ) DESC,
                             queue.attempt_count,
                             queue.download_job_id""",
                ("p0-hosts",),
            )
        )
        first_job_id = int(rows[0]["download_job_id"])
        self.assertEqual(rows[0]["target_host"], first_host)
        same_host_urls = [
            row["requested_url"] for row in rows if row["target_host"] == first_host
        ]
        other = next(row for row in rows if row["target_host"] != first_host)
        responses: dict[str, list[FakeResponse]] = {
            same_host_urls[0]: [
                FakeResponse(
                    HTML_BYTES,
                    url=same_host_urls[0],
                    status_code=403,
                    content_type="text/html",
                )
            ],
            other["requested_url"]: [FakeResponse(PDF_BYTES, url=other["requested_url"])],
        }
        session = FakeSession(responses)
        result = run_document_download_worker(
            self.conn,
            scope_id="p0-hosts",
            limit=2,
            throttle_seconds=0,
            snapshot_root=self.root / "content",
            document_store=self.root,
            session_factory=lambda: session,
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="worker-hosts",
        )
        self.assertEqual(result["blocked_hosts"], {first_host: "rejected_http_status"})
        self.assertEqual(session.calls, [same_host_urls[0], other["requested_url"]])
        deferred = list(
            self.conn.execute(
                """SELECT status,attempt_count,last_result_status
                     FROM artifact_download_queue
                    WHERE scope_id=? AND target_host=?
                      AND download_job_id<>?""",
                ("p0-hosts", first_host, first_job_id),
            )
        )
        self.assertTrue(deferred)
        self.assertTrue(all(row["status"] == "PENDING" for row in deferred))
        self.assertTrue(all(row["attempt_count"] == 0 for row in deferred))
        self.assertTrue(all(row["last_result_status"] is None for row in deferred))
        health = self.conn.execute(
            "SELECT * FROM artifact_download_host_health WHERE host=?",
            (first_host,),
        ).fetchone()
        self.assertIsNotNone(health)
        self.assertGreater(health["blocked_until_utc"], NOW)
        claimed = claim_download_job(
            self.conn,
            worker="worker-after-restart",
            now_utc=NOW,
            scope_id="p0-hosts",
        )
        self.assertTrue(claimed is None or claimed["target_host"] != first_host)

    def test_worker_rotates_hosts_before_returning_to_same_publisher(self) -> None:
        artifact_ids = tuple(
            int(row["artifact_id"])
            for row in self.conn.execute(
                """SELECT artifact_id FROM document_artifacts
                    WHERE artifact_type IN ('transcript','presentation','credit_rating')
                    ORDER BY artifact_id LIMIT 3"""
            )
        )
        self.register(
            "p0-host-fair",
            ("transcript", "presentation", "credit_rating"),
            artifact_ids=artifact_ids,
        )
        materialize_download_queue(
            self.conn, scope_id="p0-host-fair", created_at_utc=NOW
        )
        rows = list(
            self.conn.execute(
                """SELECT download_job_id FROM artifact_download_queue
                    WHERE scope_id=? ORDER BY download_job_id LIMIT 3""",
                ("p0-host-fair",),
            )
        )
        self.assertEqual(len(rows), 3)
        kept_job_ids = tuple(int(row["download_job_id"]) for row in rows)
        with self.conn:
            self.conn.execute(
                """DELETE FROM artifact_download_queue
                    WHERE scope_id=? AND download_job_id NOT IN (?,?,?)""",
                ("p0-host-fair", *kept_job_ids),
            )
        urls = (
            "https://stale.example/one.pdf",
            "https://stale.example/two.pdf",
            "https://healthy.example/three.pdf",
        )
        with self.conn:
            for row, url in zip(rows, urls):
                self.conn.execute(
                    """UPDATE artifact_download_queue
                          SET requested_url=?, target_host=?
                        WHERE download_job_id=?""",
                    (url, url.split("/")[2], int(row["download_job_id"])),
                )
            self.conn.execute(
                """UPDATE document_artifacts
                      SET period_end='2026-06-30', fiscal_year=NULL,
                          fiscal_quarter=NULL, event_date=NULL,
                          published_at_utc=NULL,
                          first_observed_at_utc=?
                    WHERE artifact_id IN (
                      SELECT artifact_id FROM artifact_download_queue
                       WHERE scope_id=?
                    )""",
                (NOW, "p0-host-fair"),
            )
        session = FakeSession(
            {
                urls[0]: [FakeResponse(b"missing", url=urls[0], status_code=404)],
                urls[1]: [FakeResponse(b"missing", url=urls[1], status_code=404)],
                urls[2]: [FakeResponse(PDF_BYTES, url=urls[2])],
            }
        )

        run_document_download_worker(
            self.conn,
            scope_id="p0-host-fair",
            limit=3,
            throttle_seconds=0,
            snapshot_root=self.root / "content",
            document_store=self.root,
            session_factory=lambda: session,
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="worker-host-fair",
        )

        self.assertEqual(session.calls, [urls[0], urls[2], urls[1]])

    def test_retryable_and_terminal_http_failures_are_distinct(self) -> None:
        self.register("p0-failures", ("credit_rating",))
        materialize_download_queue(self.conn, scope_id="p0-failures", created_at_utc=NOW)
        url = self.conn.execute(
            "SELECT requested_url FROM artifact_download_queue WHERE scope_id=?",
            ("p0-failures",),
        ).fetchone()[0]
        session = FakeSession(
            {
                url: [
                    FakeResponse(PDF_BYTES, url=url, status_code=500),
                    FakeResponse(PDF_BYTES, url=url, status_code=404),
                ]
            }
        )
        common = {
            "scope_id": "p0-failures",
            "limit": 1,
            "throttle_seconds": 0,
            "snapshot_root": self.root / "content",
            "document_store": self.root,
            "session_factory": lambda: session,
            "monotonic": lambda: 0.0,
            "health_check": FakeHealth,
            "worker": "worker-failures",
        }
        first = run_document_download_worker(
            self.conn, now=lambda: NOW, **common
        )
        self.assertEqual(first["queue"]["status_counts"], {"RETRYABLE_FAILURE": 1})
        row = self.conn.execute(
            "SELECT next_attempt_at_utc FROM artifact_download_queue WHERE scope_id=?",
            ("p0-failures",),
        ).fetchone()
        self.assertIsNotNone(row["next_attempt_at_utc"])

        second = run_document_download_worker(
            self.conn,
            now=lambda: "2026-08-15T10:00:00+00:00",
            **common,
        )
        self.assertEqual(second["queue"]["status_counts"], {"TERMINAL_FAILURE": 1})
        self.assertEqual(len(session.calls), 2)

    def test_p0_rejects_a_valid_powerpoint_without_registering_content(self) -> None:
        self.register("p0-pdf-only", ("presentation",))
        materialize_download_queue(self.conn, scope_id="p0-pdf-only", created_at_utc=NOW)
        url = self.conn.execute(
            """SELECT requested_url FROM artifact_download_queue
                WHERE scope_id=? ORDER BY download_job_id LIMIT 1""",
            ("p0-pdf-only",),
        ).fetchone()[0]
        session = FakeSession(
            {
                url: [
                    FakeResponse(
                        PPTX_BYTES,
                        url=url,
                        content_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"
                        ),
                    )
                ]
            }
        )
        result = run_document_download_worker(
            self.conn,
            scope_id="p0-pdf-only",
            limit=1,
            throttle_seconds=0,
            snapshot_root=self.root / "content",
            document_store=self.root,
            session_factory=lambda: session,
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="worker-pdf-only",
        )
        self.assertEqual(result["outcome_counts"], {"rejected_artifact_type": 1})
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM artifact_contents").fetchone()[0],
            0,
        )

    def test_recording_worker_registers_only_atomically_streamed_external_bytes(self) -> None:
        artifact_id = int(
            self.conn.execute(
                "SELECT artifact_id FROM document_artifacts WHERE artifact_type='recording'"
            ).fetchone()[0]
        )
        register_download_scope(
            self.conn,
            scope_id="recording-fallback-fixture",
            scope_kind="custom",
            companies=("LAURUSLABS",),
            artifact_types=("recording",),
            artifact_ids=(artifact_id,),
            enforce_pilot_composition=False,
            recording_fallback_only=True,
            created_at_utc=NOW,
        )
        materialize_download_queue(
            self.conn, scope_id="recording-fallback-fixture", created_at_utc=NOW
        )
        url = str(
            self.conn.execute(
                "SELECT requested_url FROM artifact_download_queue WHERE scope_id=?",
                ("recording-fallback-fixture",),
            ).fetchone()[0]
        )
        result = run_document_download_worker(
            self.conn,
            scope_id="recording-fallback-fixture",
            artifact_types=("recording",),
            limit=1,
            throttle_seconds=0,
            snapshot_root=self.root / "external-content",
            document_store=self.root,
            session_factory=lambda: FakeSession(
                {
                    url: [
                        FakeResponse(
                            MP3_BYTES,
                            url=url,
                            content_type="audio/mpeg",
                        )
                    ]
                }
            ),
            now=lambda: NOW,
            monotonic=lambda: 0.0,
            health_check=FakeHealth,
            worker="recording-worker",
        )
        self.assertEqual(result["outcome_counts"], {"validated": 1})
        row = self.conn.execute(
            """SELECT content.byte_size,content.content_sha256,blob.local_path
                 FROM artifact_contents content JOIN response_blobs blob USING(blob_id)
                WHERE content.artifact_id=?""",
            (artifact_id,),
        ).fetchone()
        self.assertEqual(int(row["byte_size"]), len(MP3_BYTES))
        self.assertEqual(row["content_sha256"], hashlib.sha256(MP3_BYTES).hexdigest())
        self.assertTrue(Path(row["local_path"]).is_file())
        self.assertEqual(list((self.root / "external-content").rglob("*.tmp")), [])

    def test_recording_scope_requires_explicit_fallback_policy(self) -> None:
        artifact_id = int(
            self.conn.execute(
                "SELECT artifact_id FROM document_artifacts WHERE artifact_type='recording'"
            ).fetchone()[0]
        )
        with self.assertRaisesRegex(ValueError, "recording_fallback_only"):
            register_download_scope(
                self.conn,
                scope_id="unsafe-recording-scope",
                scope_kind="custom",
                companies=("LAURUSLABS",),
                artifact_types=("recording",),
                artifact_ids=(artifact_id,),
                enforce_pilot_composition=False,
                created_at_utc=NOW,
            )


if __name__ == "__main__":
    unittest.main()
