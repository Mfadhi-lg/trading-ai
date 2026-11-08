from __future__ import annotations

import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.documents.credit_rating_resolver import (
    _route_request_with_policy,
    resolve_credit_rating,
)
from scrapper.pipeline.credit_rating_downloads import (
    _claim_job,
    enqueue_credit_ratings_for_accepted_parse,
    queue_credit_rating_downloads,
)
from scrapper.pipeline.accepted_document_downloads import (
    enqueue_documents_for_accepted_parse,
)
from scrapper.pipeline.promote_document_links import promote_document_links


ROOT = Path(__file__).resolve().parent
PDF_BYTES = (ROOT / "fixtures" / "documents" / "d02_sample.pdf").read_bytes()
OBSERVED = "2026-08-13T10:00:00+00:00"


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, content_type: str) -> None:
        self.body = body
        self.url = url
        self.status_code = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self.history: tuple[Any, ...] = ()

    def iter_content(self, chunk_size: int) -> Any:
        yield from (
            self.body[position : position + chunk_size]
            for position in range(0, len(self.body), chunk_size)
        )

    def close(self) -> None:
        pass


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.max_redirects = 30

    def get(self, url: str, **_: Any) -> FakeResponse:
        self.calls.append(("GET", url))
        return self.responses[url]

    def post(self, url: str, **_: Any) -> FakeResponse:
        self.calls.append(("POST", url))
        return self.responses[url]

    def close(self) -> None:
        pass


class FakePlaywrightRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakePlaywrightRoute:
    def __init__(self, url: str) -> None:
        self.request = FakePlaywrightRequest(url)
        self.aborted_with: str | None = None
        self.continued = False

    def abort(self, error_code: str) -> None:
        self.aborted_with = error_code

    def continue_(self) -> None:
        self.continued = True


class CreditRatingAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.conn = connect(self.db_path)
        init_db(self.conn, apply_migrations=True)
        # Exercise explicit restricted-policy behavior, independently of defaults.
        restricted_hosts = frozenset({'crisil.com', 'crisilratings.com', 'icra.in'})
        self.policy_patches = []
        for target in ('scrapper.core.access_policy.BLOCKED_TERMS_HOSTS',
                       'scrapper.db.documents.recovery.targets.BLOCKED_TERMS_HOSTS',
                       'scrapper.db.document_recovery_repo.BLOCKED_TERMS_HOSTS'):
            policy_patch = patch(target, restricted_hosts)
            policy_patch.start()
            self.policy_patches.append(policy_patch)
            self.addCleanup(policy_patch.stop)
        with self.conn:
            self.conn.execute("UPDATE credit_rating_host_policy SET automation_status='BLOCKED_TERMS', max_concurrency=0 WHERE host IN ('crisil.com','www.crisil.com','crisilratings.com','www.crisilratings.com','icra.in','www.icra.in')")
        blob_path = self.root / "company.html"
        body = b"<html><body>accepted company page</body></html>"
        blob_path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        with self.conn:
            company_id = self.conn.execute(
                "INSERT INTO companies(current_legal_name) VALUES ('Example Limited')"
            ).lastrowid
            self.conn.execute(
                """INSERT INTO security_identifiers
                   (company_id, identifier_type, identifier_value, is_current)
                   VALUES (?, 'NSE', 'EXAMPLE', 1)""",
                (company_id,),
            )
            run_id = self.conn.execute(
                """INSERT INTO scrape_runs
                   (started_at_utc, completed_at_utc, code_version, config_json, status)
                   VALUES (?, ?, 'test', '{}', 'ok')""",
                (OBSERVED, OBSERVED),
            ).lastrowid
            blob_id = self.conn.execute(
                """INSERT INTO response_blobs
                   (content_sha256, content_type, encoding, byte_size, local_path,
                    first_observed_at_utc, created_at_utc)
                   VALUES (?, 'text/html', 'utf-8', ?, ?, ?, ?)""",
                (digest, len(body), str(blob_path), OBSERVED, OBSERVED),
            ).lastrowid
            self.conn.execute(
                """INSERT INTO fetch_observations
                   (run_id, company_id, source, reporting_scope, requested_url,
                    final_url, fetched_at_utc, http_status, auth_mode, blob_id, fetch_status)
                   VALUES (?, ?, 'screener', 'consolidated', ?, ?, ?, 200, 'session', ?, 'ok')""",
                (run_id, company_id, "https://www.screener.in/company/EXAMPLE/", "https://www.screener.in/company/EXAMPLE/consolidated/", OBSERVED, blob_id),
            )
            parse_id = self.conn.execute(
                """INSERT INTO parse_artifacts
                   (blob_id, parser_name, parser_version, manifest_version, parsed_at_utc,
                    parse_status, validation_status)
                   VALUES (?, 'screener-company', '1', '1', ?, 'ok', 'passed')""",
                (blob_id, OBSERVED),
            ).lastrowid
            self.conn.execute(
                """INSERT INTO accepted_page_versions
                   (company_id, source, reporting_scope, resolved_source_period, parse_id,
                    accepted_at_utc, accepted_by, acceptance_mode)
                   VALUES (?, 'screener', 'consolidated', 'current', ?, ?, 'test', 'automatic')""",
                (company_id, parse_id, OBSERVED),
            )
            self.conn.execute(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'credit_rating', 'Jul 2026',
                    'Rating rationale', 0, ?, 'CARE Ratings', 'available', ?, ?, 'source')""",
                (parse_id, "https://www.careratings.com/report/123", OBSERVED, OBSERVED),
            )
        self.parse_id = int(parse_id)

    def tearDown(self) -> None:
        for policy_patch in getattr(self, "policy_patches", ()):
            policy_patch.stop()
        self.conn.close()
        self.temp.cleanup()

    def test_accepted_credit_link_promotes_and_queues_idempotently(self) -> None:
        first = promote_document_links(self.conn)
        second = promote_document_links(self.conn)
        self.assertEqual(first["status_counts"]["PROMOTED"], 1)
        self.assertEqual(second["status_counts"]["PROMOTED"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM document_artifacts").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artifact_sources").fetchone()[0], 1)
        queued = queue_credit_rating_downloads(self.conn)
        self.assertEqual(queued["PENDING"], 1)
        self.assertEqual(queue_credit_rating_downloads(self.conn)["total_job_count"], 1)

    def test_promotion_carries_only_an_explicit_full_source_date(self) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE document_links SET period_label_raw='21 May 2025'
                    WHERE parse_id=? AND document_type='credit_rating'""",
                (self.parse_id,),
            )
        first = promote_document_links(self.conn)
        second = promote_document_links(self.conn)
        artifact = self.conn.execute(
            """SELECT event_date FROM document_artifacts
                WHERE artifact_type='credit_rating'"""
        ).fetchone()
        self.assertEqual(artifact["event_date"], "2025-05-21")
        self.assertEqual(first["status_counts"]["PROMOTED"], 1)
        self.assertEqual(second["status_counts"]["PROMOTED"], 1)

        with self.conn:
            self.conn.execute(
                "UPDATE document_artifacts SET event_date='2025-05-22'"
            )
        conflicted = promote_document_links(self.conn)
        self.assertEqual(
            conflicted["status_counts"]["event_date_conflict_preserved"], 1
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT event_date FROM document_artifacts"
            ).fetchone()[0],
            "2025-05-22",
        )

    def test_current_concall_group_is_bound_to_accepted_result_period(self) -> None:
        with self.conn:
            self.conn.executemany(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher,
                    availability_status, first_observed_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'concall_ppt', ?, 'PPT', ?, ?,
                           NULL, 'available', ?, ?, 'source')""",
                (
                    (
                        self.parse_id,
                        "Aug 2026",
                        2,
                        "https://example.com/q1-fy27.pptx",
                        OBSERVED,
                        OBSERVED,
                    ),
                    (
                        self.parse_id,
                        "May 2026",
                        3,
                        "https://example.com/q4-fy26.pptx",
                        OBSERVED,
                        OBSERVED,
                    ),
                ),
            )

        enqueue_documents_for_accepted_parse(
            self.conn,
            parse_id=self.parse_id,
            resolved_result_period="2026-06",
        )
        rows = self.conn.execute(
            """SELECT source.document_url, artifact.period_end
                 FROM document_artifacts AS artifact
                 JOIN artifact_sources AS source USING (artifact_id)
                WHERE artifact.artifact_type='presentation'
                ORDER BY source.document_url"""
        ).fetchall()
        self.assertEqual(
            [(row["document_url"], row["period_end"]) for row in rows],
            [
                ("https://example.com/q1-fy27.pptx", "2026-06-30"),
                ("https://example.com/q4-fy26.pptx", "2026-03-31"),
            ],
        )

    def test_accepted_parse_enqueues_only_its_links_and_applies_host_policy(self) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'credit_rating', 'Jun 2026',
                    'Rating rationale', 1, ?, 'CRISIL', 'available', ?, ?, 'source')""",
                (
                    self.parse_id,
                    "https://www.crisil.com/rating-report/456",
                    OBSERVED,
                    OBSERVED,
                ),
            )

        result = enqueue_credit_ratings_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )

        self.assertEqual(result["promotion"]["input_count"], 2)
        self.assertEqual(len(result["promotion"]["artifact_ids"]), 2)
        statuses = {
            row["target_host"]: row["status"]
            for row in self.conn.execute(
                "SELECT target_host, status FROM credit_rating_download_queue"
            )
        }
        self.assertEqual(statuses["www.careratings.com"], "PENDING")
        self.assertEqual(statuses["www.crisil.com"], "SKIPPED_POLICY")

        second = enqueue_credit_ratings_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )
        self.assertEqual(second["queue"]["total_job_count"], 2)

    def test_blocked_link_builds_its_recovery_target_during_the_same_enqueue(
        self,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'credit_rating', 'Jun 2026',
                    'Rating rationale', 1, ?, 'CRISIL', 'available', ?, ?, 'source')""",
                (
                    self.parse_id,
                    "https://www.crisil.com/rating-report/456",
                    OBSERVED,
                    OBSERVED,
                ),
            )

        result = enqueue_credit_ratings_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )

        self.assertEqual(result["recovery"]["recovery_target_count"], 1)
        self.assertEqual(result["recovery"]["crisil_count"], 1)
        targets = self.conn.execute(
            """SELECT blocked_source_host, status FROM artifact_recovery_targets"""
        ).fetchall()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["blocked_source_host"], "www.crisil.com")
        self.assertEqual(targets[0]["status"], "PENDING")

        # The unblocked CARE link stays an ordinary download, never a target.
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM artifact_recovery_targets
                    WHERE blocked_source_host='www.careratings.com'"""
            ).fetchone()[0],
            0,
        )

    def test_crisilratings_subdomain_is_policy_skipped_by_specialized_queue(
        self,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'credit_rating', 'Jun 2026',
                    'Rating rationale', 1, ?, 'CRISIL Ratings', 'available',
                    ?, ?, 'source')""",
                (
                    self.parse_id,
                    "https://media.crisilratings.com/ratings/report.pdf",
                    OBSERVED,
                    OBSERVED,
                ),
            )

        result = enqueue_credit_ratings_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )

        self.assertEqual(result["queue"]["total_job_count"], 2)
        row = self.conn.execute(
            """SELECT status, attempt_count, last_result_status
                 FROM credit_rating_download_queue
                WHERE target_host='media.crisilratings.com'"""
        ).fetchone()
        self.assertEqual(row["status"], "SKIPPED_POLICY")
        self.assertEqual(row["attempt_count"], 0)
        self.assertEqual(
            row["last_result_status"], "PUBLISHER_TERMS_BLOCK_AUTOMATION"
        )

    def test_accepted_parse_promotes_recording_but_queues_only_ppt_and_transcript(
        self,
    ) -> None:
        links = (
            (
                "concall_ppt",
                "Earnings presentation",
                "https://example.com/q1-presentation.pptx",
            ),
            (
                "concall_transcript",
                "Earnings call transcript",
                "https://example.com/q1-transcript.pdf",
            ),
            (
                "concall_recording",
                "Earnings call recording",
                "https://example.com/q1-recording.mp3",
            ),
            (
                "concall_notes",
                "AI Summary",
                "https://www.screener.in/concalls/summary/123/",
            ),
        )
        with self.conn:
            self.conn.executemany(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', ?, 'Jun 2026', ?, ?, ?, NULL,
                           'available', ?, ?, 'source')""",
                [
                    (self.parse_id, document_type, title, position, url, OBSERVED, OBSERVED)
                    for position, (document_type, title, url) in enumerate(links, start=1)
                ],
            )

        first = enqueue_documents_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )
        self.assertEqual(first["concall"]["queue"]["created_job_count"], 2)
        self.assertEqual(
            {
                row["artifact_type"]
                for row in self.conn.execute(
                    """SELECT artifact.artifact_type
                         FROM artifact_download_queue AS queue
                         JOIN document_artifacts AS artifact USING (artifact_id)
                        WHERE queue.scope_id=?""",
                    (first["concall"]["scope_id"],),
                )
            },
            {"presentation", "transcript"},
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM document_link_promotions AS promotion
                     JOIN document_links AS link USING (document_link_id)
                    WHERE link.document_type='concall_recording'
                      AND promotion.promotion_status='PROMOTED'"""
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM artifact_download_scope_artifacts AS member
                     JOIN document_artifacts AS artifact USING (artifact_id)
                    WHERE member.scope_id=? AND artifact.artifact_type='recording'""",
                (first["concall"]["scope_id"],),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM document_link_promotions AS promotion
                     JOIN document_links AS link USING (document_link_id)
                    WHERE link.document_type='concall_notes'"""
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM credit_rating_download_queue"
            ).fetchone()[0],
            1,
        )

        second = enqueue_documents_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )
        self.assertEqual(second["concall"]["queue"]["created_job_count"], 0)
        self.assertEqual(second["concall"]["queue"]["total_job_count"], 2)

    def test_claim_enforces_reviewed_per_host_concurrency(self) -> None:
        promote_document_links(self.conn)
        queue_credit_rating_downloads(self.conn)
        artifact_id = int(
            self.conn.execute(
                "SELECT artifact_id FROM credit_rating_download_queue"
            ).fetchone()[0]
        )
        with self.conn:
            self.conn.execute(
                """INSERT INTO credit_rating_download_queue
                   (artifact_id, requested_url, target_host, priority, status,
                    created_at_utc, updated_at_utc)
                   VALUES (?, ?, 'www.careratings.com', 100, 'PENDING', ?, ?)""",
                (
                    artifact_id,
                    "https://www.careratings.com/report/124",
                    OBSERVED,
                    OBSERVED,
                ),
            )

        first = _claim_job(self.conn, worker="test", now=OBSERVED)
        second = _claim_job(self.conn, worker="test-duplicate", now=OBSERVED)

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_migration_closes_legacy_automatic_recording_jobs(self) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'concall_transcript', 'Jun 2026',
                           'Earnings call transcript', 9, ?, NULL, 'available',
                           ?, ?, 'source')""",
                (
                    self.parse_id,
                    "https://example.com/q1-transcript.pdf",
                    OBSERVED,
                    OBSERVED,
                ),
            )
            self.conn.execute(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher, availability_status,
                    first_observed_at_utc, available_at_utc, availability_basis)
                   VALUES (?, 'consolidated', 'concall_recording', 'Jun 2026',
                           'Earnings call recording', 10, ?, NULL, 'available',
                           ?, ?, 'source')""",
                (
                    self.parse_id,
                    "https://example.com/q1-recording.mp3",
                    OBSERVED,
                    OBSERVED,
                ),
            )
        result = enqueue_documents_for_accepted_parse(
            self.conn, parse_id=self.parse_id
        )
        recording_id = int(
            result["concall"]["promotions"]["concall_recording"]["artifact_ids"][0]
        )
        scope_id = result["concall"]["scope_id"]
        with self.conn:
            self.conn.execute(
                """INSERT INTO artifact_download_scope_artifacts
                   (scope_id, artifact_id) VALUES (?, ?)""",
                (scope_id, recording_id),
            )
            self.conn.execute(
                """INSERT INTO artifact_download_queue
                   (artifact_id, requested_url, target_host, priority, scope_id,
                    status, created_at_utc, updated_at_utc)
                   VALUES (?, ?, 'example.com', 100, ?, 'PENDING', ?, ?)""",
                (
                    recording_id,
                    "https://example.com/q1-recording.mp3",
                    scope_id,
                    OBSERVED,
                    OBSERVED,
                ),
            )
            self.conn.execute(
                """DELETE FROM schema_migrations
                    WHERE version='044_crisilratings_and_recording_safety.sql'"""
            )

        init_db(self.conn, apply_migrations=True)

        row = self.conn.execute(
            """SELECT status, last_result_status FROM artifact_download_queue
                WHERE artifact_id=? AND scope_id=?""",
            (recording_id, scope_id),
        ).fetchone()
        self.assertEqual(row["status"], "SKIPPED_POLICY")
        self.assertEqual(
            row["last_result_status"],
            "AUTOMATIC_RECORDING_DOWNLOAD_DISABLED",
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM artifact_download_scope_artifacts
                    WHERE scope_id=? AND artifact_id=?""",
                (scope_id, recording_id),
            ).fetchone()[0],
            0,
        )

    def test_landing_page_resolves_temporary_object_pdf(self) -> None:
        landing = "https://www.careratings.com/report/123"
        report = "https://ratings-bucket.s3.amazonaws.com/report-123.pdf"
        html = f'<html><body><a class="download" href="{report}">Download rating report</a></body></html>'.encode()
        session = FakeSession(
            {
                landing: FakeResponse(html, url=landing, content_type="text/html"),
                report: FakeResponse(PDF_BYTES, url=report, content_type="application/pdf"),
            }
        )
        resolved = resolve_credit_rating(landing, session=session)
        self.assertEqual(resolved.status, "TEMPORARY_OBJECT_REPORT_VALIDATED")
        self.assertEqual(resolved.final_result.requested_url, landing)
        self.assertEqual(resolved.final_result.detected_media_type, "application/pdf")
        self.assertEqual(session.calls, [("GET", landing), ("GET", report)])

    def test_substantial_html_rating_report_is_first_class_content(self) -> None:
        url = "https://www.indiaratings.co.in/pressrelease/123"
        paragraph = (
            "Credit rating action assigns a stable outlook to the bank loan facility. "
            "The rating rationale discusses key rating drivers, liquidity and rating sensitivity. "
        )
        html = ("<html><body><h1>Example Limited</h1><p>" + paragraph * 12 + "</p></body></html>").encode()
        resolved = resolve_credit_rating(
            url, session=FakeSession({url: FakeResponse(html, url=url, content_type="text/html")})
        )
        self.assertEqual(resolved.status, "HTML_REPORT_VALIDATED")
        self.assertEqual(resolved.final_result.detected_media_type, "text/html")

    def test_crisil_resolver_policy_regression_makes_zero_http_calls(self) -> None:
        url = "https://www.crisil.com/en/home/ratings/report.pdf"
        session = FakeSession({})

        resolved = resolve_credit_rating(url, session=session)

        self.assertEqual(resolved.status, "SKIPPED_POLICY")
        self.assertEqual(
            resolved.error,
            "Automated acquisition disabled by reviewed publisher terms",
        )
        self.assertEqual(session.calls, [])

    def test_crisilratings_resolver_policy_regression_makes_zero_http_calls(
        self,
    ) -> None:
        url = "https://www.crisilratings.com/en/home/ratings/report.pdf"
        session = FakeSession({})

        resolved = resolve_credit_rating(url, session=session)

        self.assertEqual(resolved.status, "SKIPPED_POLICY")
        self.assertEqual(
            resolved.error,
            "Automated acquisition disabled by reviewed publisher terms",
        )
        self.assertEqual(session.calls, [])

    def test_playwright_route_aborts_blocked_hosts_before_network(self) -> None:
        blocked = FakePlaywrightRoute(
            "https://assets.crisilratings.com/ratings/report.pdf"
        )
        allowed = FakePlaywrightRoute(
            "https://www.careratings.com/ratings/report.pdf"
        )

        _route_request_with_policy(blocked)
        _route_request_with_policy(allowed)

        self.assertEqual(blocked.aborted_with, "blockedbyclient")
        self.assertFalse(blocked.continued)
        self.assertIsNone(allowed.aborted_with)
        self.assertTrue(allowed.continued)

    def test_icra_resolver_policy_regression_makes_zero_http_calls(self) -> None:
        url = "https://www.icra.in/Rationale/ShowRationaleReport?id=42"
        session = FakeSession({})

        resolved = resolve_credit_rating(url, session=session)

        self.assertEqual(resolved.status, "SKIPPED_POLICY")
        self.assertEqual(
            resolved.error,
            "Automated acquisition disabled by reviewed publisher terms",
        )
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
