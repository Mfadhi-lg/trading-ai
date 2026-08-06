from __future__ import annotations

import sqlite3
import re
import tempfile
import unittest
from pathlib import Path

from scrapper.core.http import validate_screener_result_screen_url
from scrapper.core.http import FetchResponse
from scrapper.db.database import connect, init_db
from scrapper.db.screener_discovery_repo import claim_due_companies
from scrapper.exports.screener_discovery_coverage import build_screener_discovery_coverage
from scrapper.pipeline.screener_discovery import (
    discover_screener_results,
    run_screener_company_worker,
)
from scrapper.sources.screener.company import parse_company_page
from scrapper.sources.screener.result_screen import (
    normalize_result_period,
    parse_result_screen,
)


def screen_html(*, result_count: int = 3, page_count: int = 1) -> bytes:
    return f"""<!doctype html><html><body>
      <div data-page-info>{result_count} results found: Showing page 1 of {page_count}</div>
      <div data-page-results><table>
        <thead><tr><th>S.No.</th><th>Name</th><th>CMP Rs.</th><th title="Last result date">Qtr Date</th></tr></thead>
        <tbody>
          <tr data-row-company-id="11"><td>1.</td><td><a href="/company/ALPHA/consolidated/"> Alpha Ltd </a></td><td>100</td><td>202606.00</td></tr>
          <tr data-row-company-id="12"><td>2.</td><td><a href="/company/500002/">BSE Only</a></td><td></td><td>202605</td></tr>
          <tr data-row-company-id="13"><td>3.</td><td><a href="/company/GAMMA/">Gamma Ltd</a></td><td>42</td><td>202604.00</td></tr>
        </tbody>
      </table></div>
    </body></html>""".encode()


class ResultScreenParserTests(unittest.TestCase):
    def test_real_tbody_layout_and_repeated_headers_are_preserved(self) -> None:
        html = b"""<div data-page-info>2 results found: Showing page 1 of 1</div>
        <div data-page-results><table><tbody>
          <tr><th>S.No.</th><th>Name</th><th data-tooltip="Last result date">Qtr Date</th></tr>
          <tr data-row-company-id="1"><td>1.</td><td><a href="/company/A/">A</a></td><td>202606.00</td></tr>
          <tr><th>S.No.</th><th>Name</th><th data-tooltip="Last result date">Qtr Date</th></tr>
          <tr data-row-company-id="2"><td>2.</td><td><a href="/company/B/consolidated/">B</a></td><td>202605.00</td></tr>
        </tbody></table></div>"""
        parsed = parse_result_screen(html, "x", "u")
        self.assertTrue(parsed.is_complete_capture)
        header_positions = {
            cell.row_position for cell in parsed.source_cells
            if cell.cell_role == "column_header"
        }
        self.assertEqual(header_positions, {0, 2})
        self.assertEqual([row.source_position for row in parsed.rows], [1, 2])

    def test_source_faithful_identity_scope_and_periods(self) -> None:
        url = "https://www.screener.in/screen/raw/?query=Last+result+date+%3E+202605"
        parsed = parse_result_screen(screen_html(), "Last result date > 202605", url)
        self.assertTrue(parsed.is_complete_capture)
        self.assertEqual(parsed.table_headers, ("S.No.", "Name", "CMP Rs.", "Qtr Date"))
        self.assertEqual(parsed.rows[0].company_path_normalized, "/company/ALPHA/consolidated/")
        self.assertEqual(parsed.rows[0].reporting_scope_hint, "consolidated")
        self.assertEqual(parsed.rows[1].reporting_scope_hint, "standalone")
        self.assertEqual(parsed.rows[0].result_period, "2026-06")
        blank = next(
            cell for cell in parsed.rows[1].source_cells if cell.column_label_raw == "CMP Rs."
        )
        self.assertEqual(blank.raw_text, "")
        self.assertEqual(blank.normalized_display_text, "")

    def test_partial_page_never_looks_complete(self) -> None:
        parsed = parse_result_screen(screen_html(result_count=1308, page_count=53), "x", "u")
        self.assertFalse(parsed.is_complete_capture)
        self.assertEqual(parsed.coverage_status, "partial")

    def test_header_and_period_validation_fail_closed(self) -> None:
        changed = screen_html().replace(b"Qtr Date", b"Unknown Date").replace(
            b'title="Last result date"', b""
        )
        with self.assertRaisesRegex(ValueError, "exactly one Last result date"):
            parse_result_screen(changed, "x", "u")
        with self.assertRaises(ValueError):
            normalize_result_period("202613.00")
        with self.assertRaises(ValueError):
            normalize_result_period("202606.5")

    def test_query_only_url_policy(self) -> None:
        good = "https://www.screener.in/screen/raw/?query=Last+result+date+%3E+202605"
        self.assertEqual(validate_screener_result_screen_url(good), good)
        for bad in (
            good + "&page=2",
            "http://www.screener.in/screen/raw/?query=x",
            "https://screener.in/screen/raw/?query=x",
            "https://www.screener.in/screen/raw/?query=",
        ):
            with self.assertRaises(ValueError):
                validate_screener_result_screen_url(bad)


class DiscoveryPersistenceTests(unittest.TestCase):
    def test_worker_dry_run_does_not_create_or_mutate_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.db"
            empty = run_screener_company_worker(
                due=True, limit=5, dry_run=True, db_path=missing
            )
            self.assertEqual(empty["summary"]["due_count"], 0)
            self.assertFalse(missing.exists())

            fixture = root / "screen.html"
            fixture.write_bytes(screen_html())
            db = root / "discovery.db"
            discover_screener_results(
                query_role="current_reporting_cycle", query="x", fixture=fixture,
                db_path=db, snapshot_root=root / "snapshots",
            )
            before = db.read_bytes()
            payload = run_screener_company_worker(
                due=True, limit=2, dry_run=True, db_path=db,
                now_utc=lambda: "2099-01-01T00:00:00+00:00",
            )
            self.assertEqual(payload["summary"]["due_count"], 2)
            self.assertEqual(db.read_bytes(), before)

    def test_rejected_live_response_is_still_stored_without_secret_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "discovery.db"
            response = FetchResponse(
                requested_url="https://www.screener.in/screen/raw/?query=x",
                final_url="https://www.screener.in/login/",
                status_code=403,
                content=b"<html>Log in to Screener</html>",
                content_type="text/html",
                encoding="utf-8",
                etag=None,
                last_modified=None,
                auth_mode="authenticated",
                fetch_status="rejected",
            )
            with self.assertRaisesRegex(RuntimeError, "rejected before parsing"):
                discover_screener_results(
                    query_role="current_reporting_cycle",
                    query="x",
                    db_path=db,
                    snapshot_root=root / "snapshots",
                    fetcher=lambda _url: response,
                )
            conn = connect(db)
            try:
                run = conn.execute("SELECT * FROM screener_screen_runs").fetchone()
                self.assertEqual(run["coverage_status"], "rejected")
                self.assertEqual(run["validation_status"], "failed")
                fetch = dict(conn.execute("SELECT * FROM fetch_observations").fetchone())
                self.assertNotIn("sessionid", str(fetch).lower())
            finally:
                conn.close()

    def test_dry_run_is_write_free_and_persistence_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "screen.html"
            fixture.write_bytes(screen_html())
            db = root / "discovery.db"
            snapshots = root / "snapshots"
            payload = discover_screener_results(
                query_role="current_reporting_cycle",
                query="Last result date > 202605",
                fixture=fixture,
                dry_run=True,
                db_path=db,
                snapshot_root=snapshots,
            )
            self.assertEqual(payload["coverage_status"], "complete")
            self.assertFalse(db.exists())
            self.assertFalse(snapshots.exists())

            first = discover_screener_results(
                query_role="current_reporting_cycle",
                query="Last result date > 202605",
                fixture=fixture,
                db_path=db,
                snapshot_root=snapshots,
            )
            second = discover_screener_results(
                query_role="current_reporting_cycle",
                query="Last result date > 202605",
                fixture=fixture,
                db_path=db,
                snapshot_root=snapshots,
            )
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            conn = connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM screener_screen_runs").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM screener_result_candidates").fetchone()[0], 3)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM screener_company_queue").fetchone()[0], 3)
            finally:
                conn.close()

    def test_partial_capture_persists_evidence_but_enqueues_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "screen.html"
            fixture.write_bytes(screen_html(result_count=1308, page_count=53))
            db = root / "discovery.db"
            payload = discover_screener_results(
                query_role="current_reporting_cycle",
                query="Last result date > 202605",
                fixture=fixture,
                db_path=db,
                snapshot_root=root / "snapshots",
            )
            self.assertEqual(payload["coverage_status"], "partial")
            conn = connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM screener_result_candidates").fetchone()[0], 3)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM screener_company_queue").fetchone()[0], 0)
                coverage = build_screener_discovery_coverage(conn)
                self.assertEqual(
                    coverage["summary"]["candidate_decision_counts"]["incomplete_coverage"],
                    3,
                )
                self.assertEqual(
                    coverage["summary"]["candidate_decision_counts"]["unchanged"],
                    0,
                )
            finally:
                conn.close()

    def test_claims_are_exclusive_and_expired_leases_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "screen.html"
            fixture.write_bytes(screen_html())
            db = root / "discovery.db"
            discover_screener_results(
                query_role="current_reporting_cycle", query="x", fixture=fixture,
                db_path=db, snapshot_root=root / "snapshots",
            )
            first = connect(db)
            second = connect(db)
            try:
                claimed_1 = claim_due_companies(
                    first, now_utc="2099-08-05T00:00:00+00:00", limit=2,
                    worker_id="one", lease_seconds=60,
                )
                claimed_2 = claim_due_companies(
                    second, now_utc="2099-08-05T00:00:00+00:00", limit=2,
                    worker_id="two", lease_seconds=60,
                )
                self.assertTrue(
                    {item.queue_item_id for item in claimed_1}.isdisjoint(
                        {item.queue_item_id for item in claimed_2}
                    )
                )
                resumed = claim_due_companies(
                    second, now_utc="2099-08-05T00:02:00+00:00", limit=3,
                    worker_id="three", lease_seconds=60,
                )
                self.assertEqual(len(resumed), 3)
            finally:
                first.close()
                second.close()

    def test_bse_only_company_page_parses(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "screener" / "LAURUSLABS_consolidated.html"
        content = re.sub(
            rb'<a href="https://www\.nseindia\.com/[^>]+>.*?</a>',
            b"",
            fixture.read_bytes(),
            flags=re.DOTALL,
        )
        parsed = parse_company_page(content, "2026-08-04T10:02:00+00:00")
        self.assertEqual(parsed.nse_symbol, "")
        self.assertTrue(parsed.bse_code.isdigit())


if __name__ == "__main__":
    unittest.main()
