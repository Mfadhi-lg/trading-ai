from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.documents.credit_rating import normalize_credit_rating
from scrapper.pipeline.extract_document import extract_document
from scrapper.pipeline.normalize_credit_rating import normalize_credit_rating_document


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
GOLDEN = ROOT / "golden" / "d04_credit_rating_expected.json"
OBSERVED = "2026-08-05T15:00:00+00:00"


class NoOcr:
    name = "fixture-ocr"
    version = "1.0.0"
    language = "eng"
    calls = 0

    def extract_text(self, image: Any) -> tuple[str, str | None]:
        self.calls += 1
        raise AssertionError("OCR must not run for the native rating fixture")

    def options(self) -> dict[str, str]:
        return {"language": "eng", "config": "fixture"}


class CreditRatingNormalizationD04Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        conn = connect(self.db_path)
        try:
            init_db(conn)
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
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _extracted_artifact(self, key: str) -> tuple[int, int, int, Path]:
        source = FIXTURES / "d04_credit_rating.pdf"
        body = source.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        snapshot = self.root / digest / f"{key}.pdf"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(body)
        conn = connect(self.db_path)
        try:
            with conn:
                artifact_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, event_date, first_observed_at_utc,
                            acquisition_status, transcript_preference_status)
                           VALUES (?, ?, 'credit_rating', 'CARE Ratings Limited',
                            'Rating rationale for Fixture Limited', '2026-07-15',
                            ?, 'downloaded', 'not_applicable')""",
                        (self.company_id, key, OBSERVED),
                    ).lastrowid
                )
                conn.execute(
                    """INSERT OR IGNORE INTO response_blobs
                       (content_sha256, content_type, encoding, byte_size,
                        local_path, first_observed_at_utc, created_at_utc)
                       VALUES (?, 'application/pdf', NULL, ?, ?, ?, ?)""",
                    (digest, len(body), str(snapshot), OBSERVED, OBSERVED),
                )
                blob_id = int(
                    conn.execute(
                        "SELECT blob_id FROM response_blobs WHERE content_sha256=?",
                        (digest,),
                    ).fetchone()[0]
                )
                content_id = register_artifact_content(
                    conn,
                    artifact_id=artifact_id,
                    blob_id=blob_id,
                    media_type="application/pdf",
                    is_official=True,
                    created_at_utc=OBSERVED,
                )
            times = iter(("2026-08-05T15:01:00+00:00", "2026-08-05T15:01:01+00:00"))
            extracted = extract_document(
                artifact_id=artifact_id,
                content_id=content_id,
                db_path=self.db_path,
                output=self.root / f"{key}-extraction.json",
                ocr_engine=NoOcr(),
                now_utc=lambda: next(times),
            )
            return (
                artifact_id,
                content_id,
                int(extracted["extraction"]["extraction_id"]),
                snapshot,
            )
        finally:
            conn.close()

    def _normalize(self, artifact_id: int, extraction_id: int, key: str) -> dict[str, Any]:
        times = iter(("2026-08-05T15:02:00+00:00", "2026-08-05T15:02:01+00:00"))
        return normalize_credit_rating_document(
            artifact_id=artifact_id,
            extraction_id=extraction_id,
            db_path=self.db_path,
            output=self.root / f"{key}-rating.json",
            now_utc=lambda: next(times),
        )

    def _mutate_table(self, extraction_id: int, page: int, mutation: Any) -> None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                """SELECT element_position, rows_json
                   FROM document_page_elements
                   WHERE extraction_id=? AND page_or_slide_number=?
                     AND element_kind='table'""",
                (extraction_id, page),
            ).fetchone()
            rows = json.loads(row["rows_json"])
            mutation(rows)
            with conn:
                conn.execute(
                    """UPDATE document_page_elements SET rows_json=?
                       WHERE extraction_id=? AND page_or_slide_number=?
                         AND element_position=?""",
                    (json.dumps(rows), extraction_id, page, row["element_position"]),
                )
        finally:
            conn.close()

    def test_rating_rationale_normalizes_three_page_evidenced_actions(self) -> None:
        artifact_id, content_id, extraction_id, _ = self._extracted_artifact("accepted")
        report = self._normalize(artifact_id, extraction_id, "accepted")
        rating = report["normalization"]
        self.assertEqual(report["content"]["content_sha256"], "f1c0ba13d242217b04ffda70629a07016a2cc24756aed0b98a0ac042f9b9bb17")
        self.assertEqual(rating["normalization_status"], "accepted")
        self.assertTrue(rating["is_preferred"])
        self.assertEqual(rating["document_class"], "rating_rationale")
        self.assertEqual(rating["agency_code"], "care")
        self.assertEqual(rating["action_date"], "2026-07-15")
        self.assertEqual(len(rating["source_tables"]), 2)
        self.assertEqual(len(rating["actions"]), 3)
        first, second, third = rating["actions"]
        self.assertEqual(first["amount_display"], "150.00")
        self.assertEqual(first["amount_decimal"], "150.00")
        self.assertEqual(first["unit_code"], "INR_CRORE")
        self.assertEqual(first["scale_multiplier"], "10000000")
        self.assertEqual(first["rating_code"], "CARE A+")
        self.assertEqual(first["outlook_code"], "stable")
        self.assertEqual(first["rating_action_code"], "reaffirmed")
        self.assertIsNone(second["outlook_raw"])
        self.assertEqual(third["rating_action_code"], "upgraded")
        rationale = [
            row for row in rating["passages"] if row["passage_kind"] == "rationale"
        ]
        self.assertEqual(len(rationale), 1)
        self.assertEqual(rationale[0]["page_number"], 1)
        self.assertIn("Stable outlook", rationale[0]["text_raw"])
        self.assertTrue(all(action["is_canonical"] for action in rating["actions"]))
        for action in rating["actions"]:
            evidence_fields = {row["field_code"] for row in action["evidence"]}
            self.assertTrue(
                {"facility_or_instrument", "amount", "rating", "rating_action", "action_date"}
                .issubset(evidence_fields)
            )
            self.assertTrue(all(row["page_number"] in {1, 2} for row in action["evidence"]))
        self.assertIn("Previous rating", report["extraction"]["source_pages"][1]["selected_text"])
        self.assertTrue(all(row["status"] == "passed" for row in rating["validations"]))
        conn = connect(self.db_path)
        try:
            current = conn.execute(
                "SELECT COUNT(*) FROM current_credit_rating_actions WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()[0]
            self.assertEqual(current, 3)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM credit_rating_field_evidence"
                ).fetchone()[0],
                20,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT content_id FROM credit_rating_normalizations"
                ).fetchone()[0],
                content_id,
            )
        finally:
            conn.close()

    def test_same_extraction_and_options_are_idempotent_and_bytes_are_reverified(self) -> None:
        artifact_id, _, extraction_id, snapshot = self._extracted_artifact("idempotent")
        first = self._normalize(artifact_id, extraction_id, "first")
        second = normalize_credit_rating_document(
            artifact_id=artifact_id,
            extraction_id=extraction_id,
            db_path=self.db_path,
            output=self.root / "second.json",
        )
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(
            first["normalization"]["normalization_id"],
            second["normalization"]["normalization_id"],
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM credit_rating_normalizations").fetchone()[0],
                1,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM credit_rating_actions").fetchone()[0], 3)
        finally:
            conn.close()
        snapshot.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "byte size changed"):
            normalize_credit_rating_document(
                artifact_id=artifact_id,
                extraction_id=extraction_id,
                db_path=self.db_path,
                output=self.root / "tampered.json",
            )

    def test_ambiguous_rating_table_is_retained_for_review_and_never_preferred(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact("ambiguous")
        self._mutate_table(
            extraction_id,
            1,
            lambda rows: rows[0].__setitem__(3, "Rating"),
        )
        report = self._normalize(artifact_id, extraction_id, "ambiguous")
        rating = report["normalization"]
        self.assertEqual(rating["normalization_status"], "manual_review")
        self.assertFalse(rating["is_preferred"])
        self.assertEqual(rating["source_tables"][0]["table_status"], "ambiguous")
        self.assertIn("missing_headers:rating_action", rating["source_tables"][0]["review_reasons"])
        table_rule = next(
            row for row in rating["validations"]
            if row["rule_code"] == "credit_rating_tables_unambiguous"
        )
        self.assertEqual(table_rule["status"], "failed")
        conn = connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM preferred_credit_rating_normalizations WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM current_credit_rating_actions WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_conflicting_same_date_facility_actions_are_noncanonical(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact("conflict")

        def conflict(rows: list[list[str]]) -> None:
            rows[1] = [
                "Long-term bank facilities",
                "150.00",
                "CARE BBB; Negative",
                "Downgraded",
            ]

        self._mutate_table(extraction_id, 2, conflict)
        rating = self._normalize(artifact_id, extraction_id, "conflict")["normalization"]
        self.assertEqual(rating["normalization_status"], "manual_review")
        self.assertEqual(rating["conflict_action_count"], 2)
        conflicts = [row for row in rating["actions"] if row["canonical_status"] == "conflict"]
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(all(not row["is_canonical"] for row in conflicts))
        self.assertEqual({row["rating_code"] for row in conflicts}, {"CARE A+", "CARE BBB"})
        self.assertTrue(
            all("conflicting_action_rows" in row["ambiguity_reasons"] for row in conflicts)
        )
        self.assertFalse(rating["is_preferred"])

    def test_all_four_document_classes_are_deterministically_recognized(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact("classes")
        conn = connect(self.db_path)
        try:
            from scrapper.db.document_extraction_repo import extraction_payload

            source = extraction_payload(conn, extraction_id)
        finally:
            conn.close()
        for phrase, expected in (
            ("Rating Rationale", "rating_rationale"),
            ("Rating Letter", "rating_letter"),
            ("Surveillance", "surveillance"),
            ("Reaffirmation", "reaffirmation"),
        ):
            fixture = copy.deepcopy(source)
            fixture["pages"][0]["selected_text"] = fixture["pages"][0]["selected_text"].replace(
                "Rating Rationale", phrase
            )
            fixture["pages"][0]["native_text"] = fixture["pages"][0]["native_text"].replace(
                "Rating Rationale", phrase
            )
            for element in fixture["pages"][0]["elements"]:
                if element["text_raw"] == "Rating Rationale":
                    element["text_raw"] = phrase
            normalized = normalize_credit_rating(fixture)
            self.assertEqual(normalized.document_class, expected)
            self.assertEqual(normalized.status, "accepted")

    def test_accepted_projection_matches_golden(self) -> None:
        artifact_id, _, extraction_id, _ = self._extracted_artifact("golden")
        report = self._normalize(artifact_id, extraction_id, "golden")
        rating = report["normalization"]
        projection = {
            "content_sha256": report["content"]["content_sha256"],
            "document_class": rating["document_class"],
            "agency_code": rating["agency_code"],
            "action_date": rating["action_date"],
            "source_table_pages": [row["page_number"] for row in rating["source_tables"]],
            "rationale_passages": [
                {"page": row["page_number"], "text": row["text_raw"]}
                for row in rating["passages"]
                if row["passage_kind"] == "rationale"
            ],
            "actions": [
                {
                    "page": row["page_number"],
                    "facility": row["facility_or_instrument_raw"],
                    "amount_decimal": row["amount_decimal"],
                    "rating_code": row["rating_code"],
                    "outlook_code": row["outlook_code"],
                    "action_code": row["rating_action_code"],
                    "evidence_fields": sorted({item["field_code"] for item in row["evidence"]}),
                }
                for row in rating["actions"]
            ],
            "validation_codes": [row["rule_code"] for row in rating["validations"]],
        }
        self.assertEqual(projection, json.loads(GOLDEN.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
