from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from scrapper.db.database import connect, init_db
from scrapper.db.document_artifact_repo import register_artifact_content
from scrapper.db.document_download_repo import store_response_blob
from scrapper.db.html_document_rendering_repo import render_and_persist_html_pdf
from scrapper.documents.html_pdf import render_validated_html_to_pdf


NOW = "2026-09-08T04:00:00+00:00"
URL = "https://www.indiaratings.co.in/pressrelease/123"
HTML = b"""<!doctype html><html><head><title>Example rating rationale</title>
<style>ignored</style><script>ignored()</script></head><body>
<nav>navigation is not report content</nav><main>
<h1>Example Limited</h1><p>Credit rating rationale and key rating drivers.</p>
<table><tr><th>Facility</th><th>Rating</th></tr>
<tr><td>Term loan</td><td>IND A / Stable</td></tr></table>
<h2>Liquidity</h2><p>Adequate liquidity supports the assigned rating.</p>
</main></body></html>"""


class HtmlPdfRenderingTests(unittest.TestCase):
    def test_nested_layout_tables_do_not_duplicate_report_sections(self) -> None:
        content = b'''<html><body><table><tr><td><table><tr><td>
            <h1>Example Limited</h1><p>Distinctive rationale paragraph.</p>
            <table><tr><th>Facility</th><th>Rating</th></tr>
            <tr><td><p>Term loan</p></td><td><p>IND A / Stable</p></td></tr></table>
            <p>Distinctive liquidity paragraph.</p>
            </td></tr></table></td></tr></table></body></html>'''
        rendered = render_validated_html_to_pdf(content, source_url=URL)
        text = '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(rendered.body)).pages)
        self.assertEqual(text.count('Distinctive rationale paragraph.'), 1)
        self.assertEqual(text.count('Distinctive liquidity paragraph.'), 1)
        self.assertEqual(text.count('Term loan | IND A / Stable'), 1)

    def test_render_is_deterministic_and_contains_visible_report_text(self) -> None:
        first = render_validated_html_to_pdf(
            HTML, source_url=URL, document_title="Example Limited rating rationale"
        )
        second = render_validated_html_to_pdf(
            HTML, source_url=URL, document_title="Example Limited rating rationale"
        )

        self.assertEqual(first.body, second.body)
        self.assertTrue(first.body.startswith(b"%PDF-"))
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(first.body)).pages
        )
        self.assertIn("Example Limited", text)
        self.assertIn("Term loan | IND A / Stable", text)
        self.assertIn(URL, text)
        self.assertNotIn("ignored()", text)
        self.assertNotIn("navigation is not report content", text)

    def test_persistence_keeps_original_and_links_generated_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conn = connect(root / "scraper.db")
            try:
                init_db(conn, apply_migrations=True)
                with conn:
                    company_id = int(
                        conn.execute(
                            "INSERT INTO companies(current_legal_name) VALUES ('Example Limited')"
                        ).lastrowid
                    )
                    artifact_id = int(
                        conn.execute(
                            """INSERT INTO document_artifacts
                               (company_id,artifact_key,artifact_type,title_raw,
                                first_observed_at_utc,acquisition_status)
                               VALUES (?,'rating:example','credit_rating',?,?, 'downloaded')""",
                            (company_id, "Example Limited rating rationale", NOW),
                        ).lastrowid
                    )
                    blob_id, _ = store_response_blob(
                        conn,
                        body=HTML,
                        media_type="text/html",
                        observed_at_utc=NOW,
                        snapshot_root=root / "content",
                    )
                    source_content_id = register_artifact_content(
                        conn,
                        artifact_id=artifact_id,
                        blob_id=blob_id,
                        media_type="text/html",
                        is_official=True,
                        created_at_utc=NOW,
                    )

                first = render_and_persist_html_pdf(
                    conn,
                    artifact_id=artifact_id,
                    source_content_id=source_content_id,
                    source_url=URL,
                    snapshot_root=root / "content",
                    created_at_utc=NOW,
                )
                second = render_and_persist_html_pdf(
                    conn,
                    artifact_id=artifact_id,
                    source_content_id=source_content_id,
                    source_url=URL,
                    snapshot_root=root / "content",
                    created_at_utc=NOW,
                )

                self.assertFalse(first.reused)
                self.assertTrue(second.reused)
                self.assertEqual(first.rendered_content_id, second.rendered_content_id)
                with patch('scrapper.db.html_document_rendering_repo.HTML_PDF_RENDERER_VERSION', 'test-next-version'):
                    unchanged = render_and_persist_html_pdf(
                        conn, artifact_id=artifact_id, source_content_id=source_content_id,
                        source_url=URL, snapshot_root=root / 'content', created_at_utc=NOW,
                    )
                self.assertTrue(unchanged.reused)
                self.assertEqual(unchanged.rendered_content_id, first.rendered_content_id)
                rows = conn.execute(
                    """SELECT content_id,media_type,is_official,content_origin
                         FROM artifact_contents WHERE artifact_id=? ORDER BY content_id""",
                    (artifact_id,),
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["content_id"], source_content_id)
                self.assertEqual(rows[0]["media_type"], "text/html")
                self.assertEqual(rows[0]["content_origin"], "publisher_original")
                self.assertEqual(rows[1]["media_type"], "application/pdf")
                self.assertEqual(rows[1]["is_official"], 0)
                self.assertEqual(rows[1]["content_origin"], "generated_derivative")
                relationship = conn.execute(
                    """SELECT relationship_type FROM artifact_content_relationships
                        WHERE source_content_id=? AND derived_content_id=?""",
                    (source_content_id, first.rendered_content_id),
                ).fetchone()
                self.assertEqual(relationship["relationship_type"], "rendered_pdf")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
