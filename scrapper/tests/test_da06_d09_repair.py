from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scrapper.db.artifact_reconciliation_repo import (
    ensure_artifact_expectation,
    register_artifact_event,
)
from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.documents.availability import RetryPolicy
from scrapper.pipeline.reconcile_promoted_artifacts import (
    reconcile_promoted_artifacts,
)


T0 = "2026-07-20T10:00:00+00:00"
TODAY = "2026-08-16T10:00:00+00:00"


class PromotedArtifactReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "d09-repair.db")
        init_db(self.conn, apply_migrations=True)
        self.company_id = int(
            self.conn.execute(
                "INSERT INTO companies(current_legal_name,status) VALUES ('Fixture Limited','active')"
            ).lastrowid
        )
        self.conn.execute(
            """INSERT INTO security_identifiers
               (company_id,identifier_type,identifier_value,is_current)
               VALUES (?,'NSE','FIXTURE',1)""",
            (self.company_id,),
        )
        self.page_id, self.blob_id = self._page()
        self.artifact_id = self._recovered_artifact("one")
        event_id = register_artifact_event(
            self.conn,
            company_id=self.company_id,
            event_key="earnings-call:q1-fy2026",
            event_type="earnings_call",
            event_date="2026-07-20",
            period_end="2026-06-30",
            fiscal_year=2026,
            fiscal_quarter=1,
            evidenced_at_utc=T0,
            evidence_source="fixture_exchange_result",
            evidence={"filing_id": "RESULT-1", "period_end": "2026-06-30"},
        )
        self.expectation_id = ensure_artifact_expectation(
            self.conn,
            event_id=event_id,
            artifact_type="credit_rating",
            expectation_basis="accepted_result_filing",
            expected_after_utc=T0,
            created_at_utc=T0,
            policy=RetryPolicy(offsets_hours=(2, 6, 12)),
        )
        with self.conn:
            self.conn.execute(
                """INSERT INTO artifact_check_events
                   (expectation_id,checked_at_utc,source,requested_url,
                    result_status,evidence_json,discovered_artifact_id,
                    error_json,retryable,next_check_at_utc,claim_token)
                   VALUES (?,?,'legacy_retry',NULL,'expected_not_yet_published',
                           '{}',NULL,'{}',1,?,'legacy-check-1')""",
                (self.expectation_id, T0, T0),
            )
            self.conn.execute(
                """UPDATE artifact_expectations
                      SET first_checked_at_utc=?,last_checked_at_utc=?,attempt_count=1
                    WHERE expectation_id=?""",
                (T0, T0, self.expectation_id),
            )

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _page(self) -> tuple[int, int]:
        body = b"fixture discovery"
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / "discovery.html"
        path.write_bytes(body)
        with self.conn:
            blob_id = int(
                self.conn.execute(
                    """INSERT INTO response_blobs
                       (content_sha256,content_type,encoding,byte_size,local_path,
                        first_observed_at_utc,created_at_utc)
                       VALUES (?,'text/html',NULL,?,?,?,?)""",
                    (digest, len(body), str(path), T0, T0),
                ).lastrowid
            )
            page_id = int(
                self.conn.execute(
                    """INSERT INTO artifact_discovery_pages
                       (company_id,discovered_from_source,discovery_page_url,
                        publisher_claim,blob_id,parser_name,parser_version,
                        first_observed_at_utc)
                       VALUES (?,'nse','https://www.nseindia.com/api/fixture',
                               'CRISIL',?,'fixture','1',?)""",
                    (self.company_id, blob_id, T0),
                ).lastrowid
            )
        return page_id, blob_id

    def _recovered_artifact(self, suffix: str) -> int:
        with self.conn:
            artifact_id = int(
                self.conn.execute(
                    """INSERT INTO document_artifacts
                       (company_id,artifact_key,artifact_type,publisher,title_raw,
                        event_date,period_end,fiscal_year,fiscal_quarter,
                        published_at_utc,first_observed_at_utc,acquisition_status)
                       VALUES (?,?, 'credit_rating','CRISIL','CRISIL rating',
                               '2026-07-20','2026-06-30',2026,1,?,?, 'downloaded')""",
                    (self.company_id, f"rating:{suffix}", T0, TODAY),
                ).lastrowid
            )
            blocked_source = int(
                self.conn.execute(
                    """INSERT INTO artifact_sources
                       (artifact_id,discovery_page_id,discovered_from_source,
                        discovery_page_url,document_url,publisher_claim,
                        source_position,source_title_raw,
                        classification_evidence_json,is_primary_source,
                        first_observed_at_utc,last_checked_at_utc,
                        automation_status,document_originator)
                       VALUES (?,?,'nse','https://www.nseindia.com/api/fixture',?,
                               'CRISIL',0,'Blocked rating','{}',0,?,?,'BLOCKED_TERMS','CRISIL')""",
                    (
                        artifact_id,
                        self.page_id,
                        f"https://www.crisilratings.com/{suffix}.pdf",
                        T0,
                        T0,
                    ),
                ).lastrowid
            )
            target_id = int(
                self.conn.execute(
                    """INSERT INTO artifact_recovery_targets
                       (artifact_id,company_id,artifact_type,document_originator,
                        blocked_source_id,blocked_source_url,blocked_source_host,
                        company_canonical_name,event_date,fiscal_period,
                        source_confidence,field_provenance_json,contract_version,
                        status,basic_rating_coverage,full_rationale_coverage,
                        created_at_utc,updated_at_utc)
                       VALUES (?,?,'credit_rating','CRISIL',?,?,'www.crisilratings.com',
                               'Fixture Limited','2026-07-20','2026-06-30',
                               'DATABASE_TRUTH','{}','1.0.0','RECOVERED',1,0,?,?)""",
                    (
                        artifact_id,
                        self.company_id,
                        blocked_source,
                        f"https://www.crisilratings.com/{suffix}.pdf",
                        TODAY,
                        TODAY,
                    ),
                ).lastrowid
            )
            self.conn.execute(
                """INSERT INTO artifact_sources
                   (artifact_id,discovery_page_id,discovered_from_source,
                    discovery_page_url,document_url,publisher_claim,
                    source_position,source_title_raw,
                    classification_evidence_json,is_primary_source,
                    first_observed_at_utc,last_checked_at_utc,automation_status,
                    document_originator,retrieval_source_type,retrieval_host,
                    published_at_utc,available_at_utc,availability_basis,
                    recovery_target_id,matching_confidence)
                   VALUES (?,?,'nse','https://www.nseindia.com/api/fixture',?,
                           'CRISIL',1,'Exchange copy','{}',0,?,?,'AVAILABLE',
                           'CRISIL','NSE_EXCHANGE_ATTACHMENT',
                           'nsearchives.nseindia.com',?,?,
                           'exchange_filing_timestamp',?,'HIGH')""",
                (
                    artifact_id,
                    self.page_id,
                    f"https://nsearchives.nseindia.com/corporate/{suffix}.pdf",
                    TODAY,
                    TODAY,
                    T0,
                    T0,
                    target_id,
                ),
            )
            content_body = f"%PDF {suffix}".encode()
            digest = hashlib.sha256(content_body).hexdigest()
            content_path = self.root / f"{suffix}.pdf"
            content_path.write_bytes(content_body)
            self.conn.execute(
                """INSERT INTO response_blobs
                   (content_sha256,content_type,encoding,byte_size,local_path,
                    first_observed_at_utc,created_at_utc)
                   VALUES (?,'application/pdf',NULL,?,?,?,?)""",
                (digest, len(content_body), str(content_path), TODAY, TODAY),
            )
            content_blob = int(
                self.conn.execute(
                    "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (digest,)
                ).fetchone()[0]
            )
            register_artifact_content(
                self.conn,
                artifact_id=artifact_id,
                blob_id=content_blob,
                media_type="application/pdf",
                is_official=True,
                created_at_utc=TODAY,
            )
        return artifact_id

    def test_repair_appends_check_preserves_history_and_is_idempotent(self) -> None:
        first = reconcile_promoted_artifacts(
            self.conn, checked_at_utc=TODAY
        )
        second = reconcile_promoted_artifacts(
            self.conn, checked_at_utc=TODAY
        )
        self.assertEqual(first["outcome_counts"], {"RECONCILED": 1})
        self.assertEqual(second["considered_expectation_count"], 0)
        checks = self.conn.execute(
            """SELECT checked_at_utc,source,evidence_json FROM artifact_check_events
                WHERE expectation_id=? ORDER BY check_id""",
            (self.expectation_id,),
        ).fetchall()
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0]["source"], "legacy_retry")
        self.assertEqual(checks[0]["checked_at_utc"], T0)
        self.assertEqual(checks[1]["source"], "promoted_artifact_reconciliation")
        self.assertEqual(checks[1]["checked_at_utc"], TODAY)
        evidence = json.loads(checks[1]["evidence_json"])
        self.assertFalse(evidence["historical_as_of_backfill"])
        self.assertEqual(
            evidence["sources"][0]["published_at_utc"], T0
        )
        expectation = self.conn.execute(
            "SELECT availability_status,attempt_count FROM artifact_expectations WHERE expectation_id=?",
            (self.expectation_id,),
        ).fetchone()
        self.assertEqual(tuple(expectation), ("available", 2))

    def test_multiple_matching_recovered_artifacts_remain_ambiguous(self) -> None:
        self._recovered_artifact("two")
        result = reconcile_promoted_artifacts(self.conn, checked_at_utc=TODAY)
        self.assertEqual(result["outcome_counts"], {"AMBIGUOUS": 1})
        self.assertEqual(
            self.conn.execute(
                "SELECT availability_status FROM artifact_expectations WHERE expectation_id=?",
                (self.expectation_id,),
            ).fetchone()[0],
            "expected_not_yet_published",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_check_events WHERE expectation_id=?",
                (self.expectation_id,),
            ).fetchone()[0],
            1,
        )

    def test_period_ambiguity_is_unresolved_not_guessed(self) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_events
                      SET period_end=NULL,fiscal_year=NULL,fiscal_quarter=NULL
                    WHERE event_id=(SELECT event_id FROM artifact_expectations
                                    WHERE expectation_id=?)""",
                (self.expectation_id,),
            )
            self.conn.execute(
                """UPDATE document_artifacts SET event_date=NULL WHERE artifact_id=?""",
                (self.artifact_id,),
            )
        result = reconcile_promoted_artifacts(self.conn, checked_at_utc=TODAY)
        self.assertEqual(result["outcome_counts"], {"UNRESOLVED": 1})
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM artifact_check_events WHERE expectation_id=?",
                (self.expectation_id,),
            ).fetchone()[0],
            1,
        )

    def test_fiscal_year_alone_never_matches(self) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_events
                      SET event_date=NULL,period_end=NULL,fiscal_quarter=NULL
                    WHERE event_id=(SELECT event_id FROM artifact_expectations
                                    WHERE expectation_id=?)""",
                (self.expectation_id,),
            )
        result = reconcile_promoted_artifacts(self.conn, checked_at_utc=TODAY)
        self.assertEqual(result["outcome_counts"], {"UNRESOLVED": 1})

    def test_complete_fiscal_year_and_quarter_pair_matches(self) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE artifact_events SET event_date=NULL,period_end=NULL
                    WHERE event_id=(SELECT event_id FROM artifact_expectations
                                    WHERE expectation_id=?)""",
                (self.expectation_id,),
            )
        result = reconcile_promoted_artifacts(self.conn, checked_at_utc=TODAY)
        self.assertEqual(result["outcome_counts"], {"RECONCILED": 1})
        self.assertEqual(
            result["repairs"][0]["match_basis"],
            {"fiscal_year": 2026, "fiscal_quarter": 1},
        )

    def test_period_end_match_is_sufficient_when_event_dates_differ(self) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE document_artifacts SET event_date='2026-07-21' WHERE artifact_id=?",
                (self.artifact_id,),
            )
        result = reconcile_promoted_artifacts(self.conn, checked_at_utc=TODAY)
        self.assertEqual(result["outcome_counts"], {"RECONCILED": 1})
        self.assertEqual(result["repairs"][0]["match_basis"]["period_end"], "2026-06-30")


if __name__ == "__main__":
    unittest.main()
