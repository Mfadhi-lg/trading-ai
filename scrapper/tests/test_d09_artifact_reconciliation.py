from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from scrapper.db.artifact_reconciliation_repo import (
    ClaimedExpectation,
    claim_due_expectations,
    ensure_artifact_expectation,
    ensure_result_artifact_expectations,
    register_artifact_event,
    release_claims,
)
from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.db.document_download_repo import persist_download_result
from scrapper.cli import main
from scrapper.documents.availability import ArtifactCheckResult, RetryPolicy
from scrapper.documents.downloader import DownloadPolicy, DownloadResult
from scrapper.exports.artifact_coverage import build_artifact_coverage_report
from scrapper.pipeline.extract_document import extract_document
from scrapper.pipeline.ingest_official_transcript import ingest_official_transcript
from scrapper.pipeline.normalize_recording import normalize_recording
from scrapper.pipeline.reconcile_artifacts import (
    PersistedArtifactAdapter,
    _same_event,
    reconcile_artifacts,
)
from scrapper.pipeline.transcribe_recording import transcribe_recording
from scrapper.tests.test_d05_official_transcript import NoOcr
from scrapper.tests.test_d07_machine_transcription import (
    FixtureMediaTool,
    FixtureTranscriber,
)


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
GOLDEN = ROOT / "golden" / "d09_reconciliation_expected.json"
T0 = "2026-08-05T00:00:00+00:00"
T2 = "2026-08-05T02:00:00+00:00"
T6 = "2026-08-05T06:00:00+00:00"
T12 = "2026-08-05T12:00:00+00:00"
RECORDING_URL = "https://fixture.example/q1-fy26-recording.wav"


class ProgressingAdapter:
    def __init__(self, case: "ArtifactReconciliationD09Tests") -> None:
        self.case = case
        self.persisted = PersistedArtifactAdapter()
        self.recording_ingested = False
        self.official_ingested = False

    def check(self, conn: sqlite3.Connection, expectation: Any, checked_at: str) -> ArtifactCheckResult:
        if checked_at == T6 and expectation.artifact_type == "recording":
            if not self.recording_ingested:
                self.case._ingest_recording_fallback(conn)
                self.recording_ingested = True
        if checked_at == T12 and expectation.artifact_type == "transcript":
            if not self.official_ingested:
                self.case._ingest_official(conn)
                self.official_ingested = True
        return self.persisted.check(conn, expectation, checked_at)


class StatusAdapter:
    def __init__(self, statuses: dict[str, tuple[str, bool]]) -> None:
        self.statuses = statuses

    def check(self, conn: sqlite3.Connection, expectation: Any, checked_at: str) -> ArtifactCheckResult:
        status, retryable = self.statuses.get(
            expectation.event_key, ("expected_not_yet_published", True)
        )
        return ArtifactCheckResult(
            status,
            "fixture_status_source",
            requested_url=f"https://fixture.example/{expectation.event_key}",
            evidence={"explicit_fixture_status": status},
            retryable=retryable,
        )


class ArtifactReconciliationD09Tests(unittest.TestCase):
    def test_event_identity_requires_date_period_or_complete_fy_quarter(self) -> None:
        expectation = ClaimedExpectation(
            expectation_id=1,company_id=1,artifact_type="presentation",event_id=1,
            event_key="fixture",event_type="results",event_date=None,period_end=None,
            fiscal_year=2026,fiscal_quarter=None,expectation_basis="fixture",
            expected_after_utc=T0,availability_status="expected_not_yet_published",
            next_check_at_utc=T2,attempt_count=0,claim_token=None,schedule={},
        )
        artifact = {
            "event_date": None,"period_end": None,"fiscal_year": 2026,
            "fiscal_quarter": 1,
        }
        self.assertFalse(_same_event(expectation, artifact))
        paired = ClaimedExpectation(
            **{**expectation.__dict__, "fiscal_quarter": 1}
        )
        self.assertTrue(_same_event(paired, artifact))
        period = ClaimedExpectation(
            **{
                **expectation.__dict__,
                "event_date": "2026-07-21",
                "period_end": "2026-06-30",
            }
        )
        artifact.update({"event_date": "2026-07-20", "period_end": "2026-06-30"})
        self.assertTrue(_same_event(period, artifact))

    def test_epoch_sentinel_does_not_create_result_artifact_expectations(self) -> None:
        conn = connect(self.db_path)
        try:
            created = ensure_result_artifact_expectations(
                conn,
                company_id=self.company_id,
                filing_id=999,
                exchange="NSE",
                exchange_key="fixture-sentinel",
                result_period="1970-01",
                period_end="1970-01-01",
                broadcast_at_utc=T0,
                created_at_utc=T0,
                policy=self.policy,
            )
            self.assertEqual(created, ())
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM artifact_events WHERE period_end='1970-01-01'"
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_delayed_result_documents_reject_market_cap_below_580_crore(self) -> None:
        conn = connect(self.db_path)
        try:
            self._accept_market_cap(conn, "579.99")
            at_floor = ensure_result_artifact_expectations(
                conn,
                company_id=self.company_id,
                filing_id=1001,
                exchange="NSE",
                exchange_key="fixture-at-floor",
                result_period="2026-09",
                period_end="2026-09-30",
                broadcast_at_utc=T0,
                created_at_utc=T0,
                policy=self.policy,
            )
            self.assertEqual(len(at_floor), 2)
            self.assertEqual(
                {
                    row[0]
                    for row in conn.execute(
                        "SELECT artifact_type FROM artifact_expectations "
                        "WHERE expectation_id IN (?, ?)",
                        at_floor,
                    )
                },
                {"recording", "credit_rating"},
            )

            self._accept_market_cap(conn, "580.01")
            above_floor = ensure_result_artifact_expectations(
                conn,
                company_id=self.company_id,
                filing_id=1002,
                exchange="NSE",
                exchange_key="fixture-above-floor",
                result_period="2026-12",
                period_end="2026-12-31",
                broadcast_at_utc=T0,
                created_at_utc=T0,
                policy=self.policy,
            )
            self.assertEqual(len(above_floor), 4)
        finally:
            conn.close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.policy = RetryPolicy(offsets_hours=(2, 6, 12), claim_lease_seconds=60)
        conn = connect(self.db_path)
        try:
            init_db(conn, apply_migrations=True)
            self.company_id = int(
                conn.execute(
                    "INSERT INTO companies(current_legal_name, status) "
                    "VALUES ('Fixture Limited', 'active')"
                ).lastrowid
            )
            conn.execute(
                """INSERT INTO security_identifiers
                   (company_id, identifier_type, identifier_value, is_current)
                   VALUES (?, 'NSE', 'FIXTURE', 1)""",
                (self.company_id,),
            )
            conn.commit()
            self._seed_event_and_artifacts(conn)
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _blob(
        self, conn: sqlite3.Connection, body: bytes, media_type: str, name: str
    ) -> int:
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / "seed" / digest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        conn.execute(
            """INSERT OR IGNORE INTO response_blobs
               (content_sha256, content_type, encoding, byte_size, local_path,
                first_observed_at_utc, created_at_utc)
               VALUES (?, ?, NULL, ?, ?, ?, ?)""",
            (digest, media_type, len(body), str(path), T0, T0),
        )
        return int(
            conn.execute(
                "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
            ).fetchone()[0]
        )

    def _accept_market_cap(self, conn: sqlite3.Connection, value: str) -> None:
        blob_id = self._blob(conn, value.encode("ascii"), "text/html", f"cap-{value}")
        parse_id = int(
            conn.execute(
                """INSERT INTO parse_artifacts
                   (blob_id, parser_name, parser_version, manifest_version,
                    parsed_at_utc, parse_status, validation_status)
                   VALUES (?, 'fixture', '1', '1', ?, 'ok', 'passed')""",
                (blob_id, T0),
            ).lastrowid
        )
        conn.execute(
            """INSERT INTO company_metric_observations
               (parse_id, reporting_scope, metric_code, label_raw, position,
                display_value, value_decimal, unit_code, scale_multiplier,
                first_observed_at_utc, available_at_utc, availability_basis)
               VALUES (?, 'standalone', 'market_cap', 'Market Cap', 0,
                       ?, ?, 'INR_CRORE', '10000000', ?, ?, 'first_observed')""",
            (parse_id, value, value, T0, T0),
        )
        conn.execute(
            """INSERT INTO accepted_page_versions
               (company_id, source, reporting_scope, resolved_source_period,
                parse_id, accepted_at_utc, accepted_by, acceptance_mode)
               VALUES (?, 'screener', 'standalone', ?, ?, ?, 'fixture', 'fixture')""",
            (self.company_id, f"cap-{value}", parse_id, T0),
        )
        conn.commit()

    def _seed_event_and_artifacts(self, conn: sqlite3.Connection) -> None:
        event_id = register_artifact_event(
            conn,
            company_id=self.company_id,
            event_key="earnings_call:q1-fy2026",
            event_type="earnings_call",
            event_date="2026-07-20",
            period_end="2026-06-30",
            fiscal_year=2026,
            fiscal_quarter=1,
            evidenced_at_utc=T0,
            evidence_source="fixture_exchange_announcement",
            evidence={"announcement_id": "FIXTURE-1"},
        )
        # Recording is registered first so it is ingested before the transcript
        # check observes the machine fallback at the +6h reconciliation.
        self.recording_expectation_id = ensure_artifact_expectation(
            conn,
            event_id=event_id,
            artifact_type="recording",
            expectation_basis="announced_earnings_call",
            expected_after_utc=T0,
            created_at_utc=T0,
            policy=self.policy,
        )
        self.transcript_expectation_id = ensure_artifact_expectation(
            conn,
            event_id=event_id,
            artifact_type="transcript",
            expectation_basis="announced_earnings_call",
            expected_after_utc=T0,
            created_at_utc=T0,
            policy=self.policy,
        )
        with conn:
            self.recording_artifact_id = int(
                conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id, artifact_key, artifact_type, publisher,
                        title_raw, event_date, period_end, fiscal_year,
                        fiscal_quarter, first_observed_at_utc, acquisition_status,
                        transcript_preference_status)
                       VALUES (?, 'fixture-recording', 'recording', 'Fixture Limited',
                        'Q1 FY2026 Recording', '2026-07-20', '2026-06-30',
                        2026, 1, ?, 'discovered', 'recording_fallback_candidate')""",
                    (self.company_id, T0),
                ).lastrowid
            )
            self.transcript_artifact_id = int(
                conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id, artifact_key, artifact_type, publisher,
                        title_raw, event_date, period_end, fiscal_year,
                        fiscal_quarter, first_observed_at_utc, acquisition_status,
                        transcript_preference_status)
                       VALUES (?, 'fixture-transcript', 'transcript', 'Fixture Limited',
                        'Q1 FY2026 Transcript', '2026-07-20', '2026-06-30',
                        2026, 1, ?, 'discovered', 'official_candidate')""",
                    (self.company_id, T0),
                ).lastrowid
            )
            page_blob = self._blob(conn, b"<html>fixture IR</html>", "text/html", "ir.html")
            page_id = int(
                conn.execute(
                    """INSERT INTO artifact_discovery_pages
                       (company_id, discovered_from_source, discovery_page_url,
                        publisher_claim, blob_id, parser_name, parser_version,
                        first_observed_at_utc)
                       VALUES (?, 'company_ir', 'https://fixture.example/ir',
                        'Fixture Limited', ?, 'fixture', '1.0.0', ?)""",
                    (self.company_id, page_blob, T0),
                ).lastrowid
            )
            self.recording_source_id = int(
                conn.execute(
                    """INSERT INTO artifact_sources
                       (artifact_id, discovery_page_id, discovered_from_source,
                        discovery_page_url, document_url, publisher_claim,
                        source_position, source_title_raw,
                        classification_evidence_json, is_primary_source,
                        first_observed_at_utc, last_checked_at_utc,
                        availability_status)
                       VALUES (?, ?, 'company_ir', 'https://fixture.example/ir', ?,
                        'Fixture Limited', 0, 'Q1 FY2026 Recording', '{}', 1,
                        ?, ?, 'expected_not_yet_published')""",
                    (self.recording_artifact_id, page_id, RECORDING_URL, T0, T0),
                ).lastrowid
            )

    def _ingest_recording_fallback(self, conn: sqlite3.Connection) -> None:
        body = (FIXTURES / "d06_recording.wav").read_bytes()
        persisted = persist_download_result(
            conn,
            artifact_id=self.recording_artifact_id,
            artifact_source_ids=(self.recording_source_id,),
            result=DownloadResult(
                requested_url=RECORDING_URL,
                final_url=RECORDING_URL,
                http_status=200,
                response_headers={"content-type": "audio/wav"},
                redirects=(),
                declared_media_type="audio/wav",
                detected_media_type="audio/wav",
                body=body,
                byte_size=len(body),
                result_status="validated",
                elapsed_ms=5,
            ),
            policy=DownloadPolicy(),
            started_at_utc=T6,
            fetched_at_utc=T6,
            snapshot_root=self.root / "content",
        )
        self.assertIsNotNone(persisted.content_id)
        normalized = normalize_recording(
            artifact_id=self.recording_artifact_id,
            db_path=self.db_path,
            output=self.root / "recording.json",
            media_tool=FixtureMediaTool(),
            now_utc=lambda: T6,
            snapshot_root=self.root / "content",
        )
        self.assertEqual(normalized["normalization"]["normalization_status"], "accepted")
        machine = transcribe_recording(
            artifact_id=self.transcript_artifact_id,
            recording_artifact_id=self.recording_artifact_id,
            transcriber=FixtureTranscriber(),
            db_path=self.db_path,
            output=self.root / "machine.json",
            now_utc=lambda: T6,
            snapshot_root=self.root / "content",
        )
        self.assertEqual(machine["transcription"]["run_status"], "accepted")

    def _ingest_official(self, conn: sqlite3.Connection) -> None:
        body = (FIXTURES / "d05_official_transcript.pdf").read_bytes()
        with conn:
            blob_id = self._blob(conn, body, "application/pdf", "official.pdf")
            content_id = register_artifact_content(
                conn,
                artifact_id=self.transcript_artifact_id,
                blob_id=blob_id,
                media_type="application/pdf",
                is_official=True,
                created_at_utc=T12,
            )
        extracted = extract_document(
            artifact_id=self.transcript_artifact_id,
            content_id=content_id,
            db_path=self.db_path,
            output=self.root / "official-extraction.json",
            ocr_engine=NoOcr(),
            now_utc=lambda: T12,
        )
        official = ingest_official_transcript(
            artifact_id=self.transcript_artifact_id,
            extraction_id=int(extracted["extraction"]["extraction_id"]),
            db_path=self.db_path,
            output=self.root / "official.json",
            now_utc=lambda: T12,
        )
        self.assertEqual(official["transcript"]["validation_status"], "accepted")

    def _run(self, when: str, adapter: Any, name: str) -> dict[str, Any]:
        return reconcile_artifacts(
            due=True,
            limit=100,
            db_path=self.db_path,
            output=self.root / f"{name}.json",
            adapter=adapter,
            now_utc=lambda: when,
            worker_id=f"worker-{name}",
        )

    def test_fake_clock_progression_preserves_machine_and_promotes_official(self) -> None:
        adapter = ProgressingAdapter(self)
        at_2 = self._run(T2, adapter, "at-2")
        self.assertEqual(at_2["summary"]["status_counts"], {"expected_not_yet_published": 2})
        at_6 = self._run(T6, adapter, "at-6")
        self.assertEqual(
            at_6["summary"]["status_counts"],
            {"available": 1, "expected_not_yet_published": 1},
        )
        at_12 = self._run(T12, adapter, "at-12")
        self.assertEqual(at_12["summary"]["status_counts"], {"available": 1})
        duplicate = self._run(T12, adapter, "duplicate")
        self.assertEqual(duplicate["summary"]["claimed_count"], 0)

        conn = connect(self.db_path)
        try:
            transcripts = conn.execute(
                """SELECT transcript_id, origin FROM transcripts
                   WHERE artifact_id=? ORDER BY transcript_id""",
                (self.transcript_artifact_id,),
            ).fetchall()
            self.assertEqual([row["origin"] for row in transcripts], ["machine_generated", "official"])
            preferred = conn.execute(
                """SELECT transcript.origin, artifact.transcript_preference_status
                   FROM preferred_transcripts AS preferred
                   JOIN transcripts AS transcript USING (transcript_id)
                   JOIN document_artifacts AS artifact USING (artifact_id)
                   WHERE preferred.artifact_id=?""",
                (self.transcript_artifact_id,),
            ).fetchone()
            self.assertEqual(preferred["origin"], "official")
            self.assertEqual(preferred["transcript_preference_status"], "official_preferred")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifact_check_events").fetchone()[0], 5)
            report = build_artifact_coverage_report(
                conn, symbols=("FIXTURE",), generated_at_utc=T12
            )
        finally:
            conn.close()
        projection = {
            "coverage_status": report["coverage_status"],
            "queue_status": report["queue_status"],
            "summary": report["summary"],
            "period_coverage": report["period_coverage"],
            "expectations": [
                {
                    "artifact_type": row["artifact_type"],
                    "availability_status": row["availability_status"],
                    "attempt_count": row["attempt_count"],
                    "terminal_reason": row["terminal_reason"],
                    "check_statuses": [check["result_status"] for check in row["checks"]],
                }
                for row in report["expectations"]
            ],
        }
        self.assertEqual(projection, json.loads(GOLDEN.read_text(encoding="utf-8")))

    def test_due_documents_share_one_screener_source_refresh_per_event(self) -> None:
        conn = connect(self.db_path)
        try:
            event_id = int(
                conn.execute(
                    "SELECT event_id FROM artifact_events WHERE event_key=?",
                    ("earnings_call:q1-fy2026",),
                ).fetchone()[0]
            )
            presentation_id = ensure_artifact_expectation(
                conn,
                event_id=event_id,
                artifact_type="presentation",
                expectation_basis="announced_results",
                expected_after_utc=T0,
                created_at_utc=T0,
                policy=self.policy,
            )
            with conn:
                conn.execute(
                    """INSERT INTO screener_company_collection_state
                       (company_path_normalized, reporting_scope, company_id,
                        latest_discovered_result_period,
                        latest_accepted_result_period, updated_at_utc)
                       VALUES ('/company/FIXTURE/', 'standalone', ?,
                               '2026-06', '2026-06', ?)""",
                    (self.company_id, T0),
                )
        finally:
            conn.close()

        refreshed: list[Any] = []

        def source_refresher(target: Any) -> None:
            refreshed.append(target)

        report = reconcile_artifacts(
            due=True,
            limit=100,
            db_path=self.db_path,
            output=self.root / "source-refresh.json",
            adapter=PersistedArtifactAdapter(),
            source_refresher=source_refresher,
            now_utc=lambda: T2,
            worker_id="worker-source-refresh",
        )

        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0].result_period, "2026-06")
        self.assertEqual(
            refreshed[0].expectation_ids,
            tuple(sorted((self.transcript_expectation_id, presentation_id))),
        )
        self.assertEqual(report["summary"]["source_refresh_count"], 1)

    def test_migration_rebases_never_checked_overdue_result_documents(self) -> None:
        conn = connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """UPDATE artifact_expectations
                          SET expectation_basis=?,
                              availability_status='expected_not_yet_published',
                              expected_after_utc=?,
                              first_checked_at_utc=NULL,
                              next_check_at_utc=?, attempt_count=0,
                              terminal_reason=NULL
                        WHERE expectation_id=?""",
                    (
                        "accepted_exchange_result_filing_requires_primary_source_discovery",
                        T0,
                        T0,
                        self.transcript_expectation_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM schema_migrations WHERE version=?",
                    ("060_rebase_all_overdue_concall_refresh.sql",),
                )
            init_db(conn, apply_migrations=True)
            row = conn.execute(
                """SELECT attempt_count, next_check_at_utc, terminal_reason
                     FROM artifact_expectations WHERE expectation_id=?""",
                (self.transcript_expectation_id,),
            ).fetchone()
            self.assertEqual(row["attempt_count"], 0)
            self.assertIsNotNone(row["next_check_at_utc"])
            self.assertIsNone(row["terminal_reason"])
            # The normal startup backfill still presents the immutable filing
            # timestamp and must accept the explicitly rearmed retry anchor.
            event_id = int(
                conn.execute(
                    "SELECT event_id FROM artifact_expectations WHERE expectation_id=?",
                    (self.transcript_expectation_id,),
                ).fetchone()[0]
            )
            ensure_artifact_expectation(
                conn,
                event_id=event_id,
                artifact_type="transcript",
                expectation_basis=(
                    "accepted_exchange_result_filing_requires_primary_source_discovery"
                ),
                expected_after_utc=T0,
                created_at_utc=T0,
                policy=self.policy,
            )
            with conn:
                conn.execute(
                    """UPDATE artifact_expectations
                          SET availability_status='available', attempt_count=1,
                              terminal_reason='accepted_artifact_present'
                        WHERE expectation_id=?""",
                    (self.transcript_expectation_id,),
                )
            ensure_artifact_expectation(
                conn,
                event_id=event_id,
                artifact_type="transcript",
                expectation_basis=(
                    "accepted_exchange_result_filing_requires_primary_source_discovery"
                ),
                expected_after_utc=T0,
                created_at_utc=T0,
                policy=self.policy,
            )
        finally:
            conn.close()

    def test_rate_limit_stops_remaining_company_refreshes_in_batch(self) -> None:
        conn = connect(self.db_path)
        try:
            second_event = register_artifact_event(
                conn,
                company_id=self.company_id,
                event_key="results:q2-fy2026",
                event_type="results",
                period_end="2026-09-30",
                evidenced_at_utc=T0,
                evidence_source="fixture",
                evidence={},
            )
            for artifact_type in ("presentation", "transcript"):
                ensure_artifact_expectation(
                    conn,
                    event_id=second_event,
                    artifact_type=artifact_type,
                    expectation_basis="fixture",
                    expected_after_utc=T0,
                    created_at_utc=T0,
                    policy=self.policy,
                )
            with conn:
                conn.execute(
                    """INSERT INTO screener_company_collection_state
                       (company_path_normalized, reporting_scope, company_id,
                        latest_discovered_result_period,
                        latest_accepted_result_period, updated_at_utc)
                       VALUES ('/company/FIXTURE/', 'standalone', ?,
                               '2026-09', '2026-09', ?)""",
                    (self.company_id, T0),
                )
        finally:
            conn.close()

        calls: list[Any] = []

        def rate_limited(target: Any) -> None:
            calls.append(target)
            raise RuntimeError(
                "Response rejected before parsing: status=429 classification=rejected"
            )

        report = reconcile_artifacts(
            due=True,
            limit=100,
            db_path=self.db_path,
            output=self.root / "rate-limit.json",
            adapter=PersistedArtifactAdapter(),
            source_refresher=rate_limited,
            now_utc=lambda: T2,
            worker_id="worker-rate-limit",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(report["summary"]["source_refresh_count"], 2)
        self.assertEqual(report["summary"]["source_refresh_failure_count"], 2)
        self.assertEqual(
            [row["status"] for row in report["source_refreshes"]],
            ["failed", "deferred_host_rate_limit"],
        )

    def test_overlapping_workers_cannot_claim_same_due_expectation(self) -> None:
        first = connect(self.db_path)
        second = connect(self.db_path)
        try:
            claimed = claim_due_expectations(
                first, now_utc=T2, limit=1, worker_id="first"
            )
            overlap = claim_due_expectations(
                second, now_utc=T2, limit=100, worker_id="second"
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(len(overlap), 1)
            self.assertNotEqual(overlap[0].expectation_id, claimed[0].expectation_id)
            no_more = claim_due_expectations(
                second, now_utc=T2, limit=100, worker_id="second-again"
            )
            self.assertEqual(no_more, ())
            release_claims(first, claimed, released_at_utc=T2)
            release_claims(second, overlap, released_at_utc=T2)
        finally:
            first.close()
            second.close()

    def test_absence_states_remain_distinct_and_no_coverage_is_not_zero(self) -> None:
        states = {
            "not-offered": ("not_offered", False),
            "not-applicable": ("not_applicable", False),
            "no-rows": ("no_rows_reported", False),
            "uncovered": ("source_not_covered", False),
            "inaccessible": ("inaccessible", False),
            "failed": ("discovery_failed", True),
            "unknown": ("unknown", True),
        }
        conn = connect(self.db_path)
        try:
            for position, (event_key, _) in enumerate(states.items(), 1):
                event_id = register_artifact_event(
                    conn,
                    company_id=self.company_id,
                    event_key=event_key,
                    event_type="other",
                    event_date=f"2026-06-{position:02d}",
                    evidenced_at_utc=T0,
                    evidence_source="fixture",
                    evidence={"case": event_key},
                )
                ensure_artifact_expectation(
                    conn,
                    event_id=event_id,
                    artifact_type="presentation",
                    expectation_basis="fixture_absence_case",
                    expected_after_utc=T0,
                    created_at_utc=T0,
                    policy=self.policy,
                )
        finally:
            conn.close()
        report = self._run(T2, StatusAdapter(states), "states")
        self.assertTrue(
            {status for status, _ in states.values()}.issubset(
                report["summary"]["status_counts"]
            )
        )
        conn = connect(self.db_path)
        try:
            uncovered = conn.execute(
                """SELECT availability_status, terminal_reason, last_error_json
                   FROM artifact_expectations AS expectation
                   JOIN artifact_events AS event USING (event_id)
                   WHERE event.event_key='uncovered'"""
            ).fetchone()
            self.assertEqual(uncovered["availability_status"], "source_not_covered")
            self.assertEqual(uncovered["terminal_reason"], "source_not_covered")
            self.assertEqual(json.loads(uncovered["last_error_json"]), {})
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM document_artifacts WHERE company_id=?",
                    (self.company_id,),
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_retry_policy_is_bounded_and_matches_default_ladder(self) -> None:
        policy = RetryPolicy()
        self.assertEqual(policy.check_at(T0, 0), T2)
        self.assertEqual(policy.check_at(T0, 1), T6)
        self.assertEqual(policy.check_at(T0, 2), T12)
        self.assertEqual(policy.check_at(T0, 9), "2026-08-12T00:00:00+00:00")
        self.assertEqual(policy.check_at(T0, 14), "2026-09-16T00:00:00+00:00")
        self.assertIsNone(policy.check_at(T0, 15))

    def test_overdue_retry_is_spaced_from_actual_check(self) -> None:
        policy = RetryPolicy(offsets_hours=(2, 6, 12))
        next_check, reason = policy.next_after_result(
            expected_after_utc=T0,
            completed_attempt_count=1,
            status="expected_not_yet_published",
            retryable=True,
            checked_at_utc="2026-09-29T04:00:00+00:00",
        )
        self.assertEqual(next_check, "2026-09-29T08:00:00+00:00")
        self.assertIsNone(reason)

    def test_unknown_enters_review_and_failures_stop_when_schedule_exhausts(self) -> None:
        conn = connect(self.db_path)
        try:
            for position, key in enumerate(("bounded-unknown", "bounded-failed"), 1):
                event_id = register_artifact_event(
                    conn,
                    company_id=self.company_id,
                    event_key=key,
                    event_type="other",
                    event_date=f"2026-05-0{position}",
                    evidenced_at_utc=T0,
                    evidence_source="fixture",
                    evidence={"case": key},
                )
                ensure_artifact_expectation(
                    conn,
                    event_id=event_id,
                    artifact_type="presentation",
                    expectation_basis="bounded_fixture",
                    expected_after_utc=T0,
                    created_at_utc=T0,
                    policy=self.policy,
                )
        finally:
            conn.close()
        adapter = StatusAdapter(
            {
                "bounded-unknown": ("unknown", True),
                "bounded-failed": ("discovery_failed", True),
            }
        )
        self._run(T2, adapter, "bounded-2")
        self._run(T6, adapter, "bounded-6")
        self._run(T12, adapter, "bounded-12")
        conn = connect(self.db_path)
        try:
            states = {
                row["event_key"]: (row["next_check_at_utc"], row["terminal_reason"])
                for row in conn.execute(
                    """SELECT event.event_key, expectation.next_check_at_utc,
                              expectation.terminal_reason
                       FROM artifact_expectations AS expectation
                       JOIN artifact_events AS event USING (event_id)
                       WHERE event.event_key IN ('bounded-unknown', 'bounded-failed')"""
                )
            }
        finally:
            conn.close()
        self.assertEqual(states["bounded-unknown"], (None, "unknown_retry_limit_reached"))
        self.assertEqual(states["bounded-failed"], (None, "retry_schedule_exhausted"))

    def test_cli_dry_run_and_coverage_export_retry_state_without_claiming(self) -> None:
        dry_output = self.root / "dry-run.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "reconcile-artifacts", "--due", "--limit", "1", "--dry-run",
                    "--db", str(self.db_path),
                    "--output", str(dry_output),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["summary"]["due_count"], 1)
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifact_check_events").fetchone()[0], 0)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM artifact_expectations WHERE claim_token IS NOT NULL"
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

        coverage_output = self.root / "coverage.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "artifact-coverage", "--symbol", "FIXTURE", "--db",
                    str(self.db_path), "--output", str(coverage_output),
                ]
            )
        self.assertEqual(code, 0)
        cli_payload = json.loads(stdout.getvalue())
        self.assertEqual(cli_payload["queue_status"], "active")
        self.assertEqual(cli_payload["summary"]["pending_retry_count"], 2)
