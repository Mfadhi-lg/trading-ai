from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.pipeline.extract_document import extract_document


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "documents"
GOLDEN = ROOT / "golden" / "d03_extraction_expected.json"
OBSERVED = "2026-08-05T14:30:00+00:00"


class FakeOcr:
    name = "fixture-ocr"
    version = "1.0.0"
    language = "eng"

    def __init__(self, responses: tuple[tuple[str, str | None], ...] = ()) -> None:
        self.responses = list(responses)
        self.calls = 0

    def extract_text(self, image: Any) -> tuple[str, str | None]:
        self.calls += 1
        self.assert_image = (image.width, image.height)
        if not self.responses:
            raise AssertionError("OCR was invoked for a native-text surface")
        return self.responses.pop(0)

    def options(self) -> dict[str, str]:
        return {"language": self.language, "config": "fixture"}


class DocumentExtractionD03Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        conn = connect(self.db_path)
        try:
            init_db(conn)
            company_id = int(
                conn.execute(
                    "INSERT INTO companies(current_legal_name, status) VALUES ('Fixture Limited', 'active')"
                ).lastrowid
            )
            conn.execute(
                """INSERT INTO security_identifiers
                   (company_id, identifier_type, identifier_value, is_current)
                   VALUES (?, 'NSE', 'FIXTURE', 1)""",
                (company_id,),
            )
            conn.commit()
            self.company_id = company_id
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _content(
        self, fixture_name: str, *, media_type: str, artifact_type: str
    ) -> tuple[int, int]:
        source = FIXTURES / fixture_name
        body = source.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        snapshot = self.root / digest / fixture_name
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(body)
        conn = connect(self.db_path)
        try:
            with conn:
                artifact_id = int(
                    conn.execute(
                        """INSERT INTO document_artifacts
                           (company_id, artifact_key, artifact_type, publisher,
                            title_raw, first_observed_at_utc, acquisition_status,
                            transcript_preference_status)
                           VALUES (?, ?, ?, 'Fixture Limited', ?, ?, 'downloaded', ?)""",
                        (
                            self.company_id,
                            fixture_name,
                            artifact_type,
                            fixture_name,
                            OBSERVED,
                            "official_candidate" if artifact_type == "transcript" else "not_applicable",
                        ),
                    ).lastrowid
                )
                blob_id = int(
                    conn.execute(
                        """INSERT INTO response_blobs
                           (content_sha256, content_type, encoding, byte_size,
                            local_path, first_observed_at_utc, created_at_utc)
                           VALUES (?, ?, NULL, ?, ?, ?, ?)""",
                        (digest, media_type, len(body), str(snapshot), OBSERVED, OBSERVED),
                    ).lastrowid
                )
                content_id = register_artifact_content(
                    conn,
                    artifact_id=artifact_id,
                    blob_id=blob_id,
                    media_type=media_type,
                    is_official=True,
                    created_at_utc=OBSERVED,
                )
            return artifact_id, content_id
        finally:
            conn.close()

    def _run(
        self,
        artifact_id: int,
        content_id: int,
        engine: FakeOcr,
        name: str,
    ) -> dict[str, Any]:
        times = iter(("2026-08-05T14:31:00+00:00", "2026-08-05T14:31:01+00:00"))
        return extract_document(
            artifact_id=artifact_id,
            content_id=content_id,
            db_path=self.db_path,
            output=self.root / f"{name}.json",
            ocr_engine=engine,
            now_utc=lambda: next(times),
        )

    def test_native_pdf_preserves_pages_blocks_table_and_image_without_ocr(self) -> None:
        artifact_id, content_id = self._content(
            "d03_text.pdf", media_type="application/pdf", artifact_type="presentation"
        )
        engine = FakeOcr()
        report = self._run(artifact_id, content_id, engine, "text-pdf")
        extraction = report["extraction"]
        self.assertEqual(extraction["extraction_status"], "ok")
        self.assertEqual(extraction["page_or_slide_count"], 2)
        self.assertEqual(extraction["native_page_count"], 2)
        self.assertEqual(extraction["ocr_page_count"], 0)
        self.assertEqual(engine.calls, 0)
        self.assertEqual(
            [page["selected_text_origin"] for page in extraction["pages"]],
            ["native", "native"],
        )
        first = extraction["pages"][0]
        self.assertIn("Q1 FY2026 Results", first["native_text"])
        self.assertEqual(first["ocr_text"], "")
        self.assertIn("table", {row["element_kind"] for row in first["elements"]})
        self.assertIn("image", {row["element_kind"] for row in first["elements"]})
        table = next(row for row in first["elements"] if row["element_kind"] == "table")
        self.assertEqual(table["rows"][0], ["Metric", "Q1 FY26", "Q1 FY25"])
        self.assertFalse(first["layout"]["rendered_for_ocr"])
        self.assertTrue(all(row["status"] == "passed" for row in extraction["validations"]))

    def test_scanned_pdf_uses_ocr_and_keeps_native_and_selected_origins_separate(self) -> None:
        artifact_id, content_id = self._content(
            "d03_scanned.pdf", media_type="application/pdf", artifact_type="transcript"
        )
        engine = FakeOcr((("SCANNED EBITDA MARGIN 24 PERCENT", "0.9825"),))
        report = self._run(artifact_id, content_id, engine, "scanned-pdf")
        extraction = report["extraction"]
        self.assertEqual(engine.calls, 1)
        self.assertEqual(extraction["native_page_count"], 0)
        self.assertEqual(extraction["ocr_page_count"], 1)
        page = extraction["pages"][0]
        self.assertEqual(page["native_text"], "")
        self.assertEqual(page["native_unusable_reason"], "absent")
        self.assertEqual(page["selected_text_origin"], "ocr")
        self.assertEqual(page["ocr_text"], page["selected_text"])
        self.assertEqual(page["extraction_confidence"], "0.9825")
        self.assertTrue(page["layout"]["rendered_for_ocr"])
        self.assertEqual(page["layout"]["render_dpi"], 200)
        self.assertIn("image", {row["element_kind"] for row in page["elements"]})

    def test_pptx_preserves_slide_structure_notes_and_picture_only_ocr(self) -> None:
        artifact_id, content_id = self._content(
            "d03_presentation.pptx",
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            artifact_type="presentation",
        )
        engine = FakeOcr((("IMAGE ONLY GUIDANCE RAISED", "0.9700"),))
        report = self._run(artifact_id, content_id, engine, "pptx")
        extraction = report["extraction"]
        self.assertEqual(engine.calls, 1)
        self.assertEqual(extraction["page_or_slide_count"], 2)
        self.assertEqual(extraction["native_page_count"], 1)
        self.assertEqual(extraction["ocr_page_count"], 1)
        first, second = extraction["pages"]
        self.assertEqual(first["title_raw"], "Q1 FY2026 Investor Presentation")
        self.assertEqual(first["selected_text_origin"], "native")
        self.assertTrue(first["speaker_notes_available"])
        self.assertIn("native slide structure fixture", first["speaker_notes_text"])
        self.assertEqual(
            {row["element_kind"] for row in first["elements"]},
            {"text_block", "table", "chart"},
        )
        table = next(row for row in first["elements"] if row["element_kind"] == "table")
        self.assertEqual(table["rows"][1], ["Revenue", "1234", "1050"])
        chart = next(row for row in first["elements"] if row["element_kind"] == "chart")
        self.assertEqual(chart["reference"]["series_count"], 1)
        self.assertEqual(second["selected_text_origin"], "ocr")
        self.assertEqual(second["native_text"], "")
        self.assertEqual(second["layout"]["ocr_surface"], "embedded_pictures")
        self.assertEqual(second["elements"][0]["element_kind"], "image")

    def test_same_content_parser_engine_and_options_reuse_one_extraction(self) -> None:
        artifact_id, content_id = self._content(
            "d03_scanned.pdf", media_type="application/pdf", artifact_type="transcript"
        )
        first_engine = FakeOcr((("SCANNED EBITDA MARGIN 24 PERCENT", "0.9825"),))
        first = self._run(artifact_id, content_id, first_engine, "first")
        second_engine = FakeOcr()
        second = extract_document(
            artifact_id=artifact_id,
            content_id=content_id,
            db_path=self.db_path,
            output=self.root / "second.json",
            ocr_engine=second_engine,
        )
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(second_engine.calls, 0)
        self.assertEqual(
            first["extraction"]["extraction_id"],
            second["extraction"]["extraction_id"],
        )
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM document_extractions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0], 1)
            preferred = conn.execute(
                """SELECT preferred_content_id, acquisition_status,
                          transcript_preference_status
                   FROM document_artifacts WHERE artifact_id=?""",
                (artifact_id,),
            ).fetchone()
            self.assertEqual(preferred["preferred_content_id"], content_id)
            self.assertEqual(preferred["acquisition_status"], "validated")
            self.assertEqual(preferred["transcript_preference_status"], "official_preferred")
        finally:
            conn.close()

        snapshot = next((self.root / hashlib.sha256((FIXTURES / "d03_scanned.pdf").read_bytes()).hexdigest()).iterdir())
        snapshot.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "byte size changed"):
            extract_document(
                artifact_id=artifact_id,
                content_id=content_id,
                db_path=self.db_path,
                output=self.root / "tampered.json",
                ocr_engine=FakeOcr(),
            )

    def test_malformed_signature_valid_pdf_is_recorded_as_failed_not_partial(self) -> None:
        artifact_id, content_id = self._content(
            "d03_malformed.pdf", media_type="application/pdf", artifact_type="credit_rating"
        )
        report = self._run(artifact_id, content_id, FakeOcr(), "malformed")
        extraction = report["extraction"]
        self.assertEqual(extraction["extraction_status"], "failed")
        self.assertEqual(extraction["page_or_slide_count"], 0)
        self.assertEqual(extraction["pages"], [])
        self.assertTrue(extraction["error"]["type"])
        pages_rule = next(
            row for row in extraction["validations"]
            if row["rule_code"] == "document_pages_present"
        )
        self.assertEqual(pages_rule["status"], "failed")
        conn = connect(self.db_path)
        try:
            artifact = conn.execute(
                "SELECT preferred_content_id, acquisition_status FROM document_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            self.assertIsNone(artifact["preferred_content_id"])
            self.assertEqual(artifact["acquisition_status"], "downloaded")
            self.assertEqual(
                conn.execute(
                    "SELECT extraction_status FROM artifact_contents WHERE content_id=?",
                    (content_id,),
                ).fetchone()[0],
                "failed",
            )
        finally:
            conn.close()

    def test_three_fixture_page_level_projection_matches_golden(self) -> None:
        specs = (
            ("d03_text.pdf", "application/pdf", "presentation", FakeOcr()),
            ("d03_scanned.pdf", "application/pdf", "transcript", FakeOcr((("SCANNED EBITDA MARGIN 24 PERCENT", "0.9825"),))),
            (
                "d03_presentation.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "presentation",
                FakeOcr((("IMAGE ONLY GUIDANCE RAISED", "0.9700"),)),
            ),
        )
        projection: dict[str, Any] = {}
        for filename, media_type, artifact_type, engine in specs:
            artifact_id, content_id = self._content(
                filename, media_type=media_type, artifact_type=artifact_type
            )
            report = self._run(artifact_id, content_id, engine, filename)
            extraction = report["extraction"]
            projection[filename] = {
                "content_sha256": report["content"]["content_sha256"],
                "status": extraction["extraction_status"],
                "page_count": extraction["page_or_slide_count"],
                "native_pages": extraction["native_page_count"],
                "ocr_pages": extraction["ocr_page_count"],
                "origins": [page["selected_text_origin"] for page in extraction["pages"]],
                "element_kinds": [
                    [element["element_kind"] for element in page["elements"]]
                    for page in extraction["pages"]
                ],
            }
        self.assertEqual(projection, json.loads(GOLDEN.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
