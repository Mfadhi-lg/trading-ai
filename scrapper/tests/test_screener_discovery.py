from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

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
    requeue_failed_screener_companies,
    requeue_incomplete_metric_screener_companies,
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
          <tr data-row-company-id="13"><td>3.</td><td><a href="/company/GVT&D/">Gamma Ltd</a></td><td>42</td><td>202604.00</td></tr>
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
            prepare_database(db)
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
                prepare_database(db)
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

            prepare_database(db)
            first = discover_screener_results(
                query_role="current_reporting_cycle",
                query="Last result date > 202605",
                fixture=fixture,
                db_path=db,
                snapshot_root=snapshots,
            )
            prepare_database(db)
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
            prepare_database(db)
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
            prepare_database(db)
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

    def _seeded_queue(self, root: Path) -> Path:
        fixture = root / "screen.html"
        fixture.write_bytes(screen_html())
        db = root / "discovery.db"
        prepare_database(db)
        discover_screener_results(
            query_role="current_reporting_cycle", query="x", fixture=fixture,
            db_path=db, snapshot_root=root / "snapshots",
        )
        return db

    @staticmethod
    def _settle(db: Path, status: str, error_type: str = "ValueError") -> int:
        conn = connect(db)
        try:
            with conn:
                conn.execute(
                    """UPDATE screener_company_queue SET status=?,
                       attempt_count=1, next_attempt_at_utc=NULL,
                       last_error_json=?""",
                    (status, '{"error":{"type":"%s","message":"boom"}}' % error_type),
                )
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM screener_company_queue WHERE status=?",
                    (status,),
                ).fetchone()[0]
            )
        finally:
            conn.close()

    def test_terminal_failures_are_invisible_to_the_worker_until_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = self._seeded_queue(root)
            settled = self._settle(db, "failed_terminal")
            self.assertEqual(settled, 3)

            stuck = run_screener_company_worker(
                due=True, limit=5, dry_run=True, db_path=db,
                now_utc=lambda: "2099-01-01T00:00:00+00:00",
            )
            self.assertEqual(stuck["summary"]["due_count"], 0)

            prepare_database(db)
            payload = requeue_failed_screener_companies(
                db_path=db, now_utc=lambda: "2099-01-01T00:00:00+00:00"
            )
            self.assertEqual(payload["summary"]["requeued_count"], 3)
            self.assertEqual(payload["summary"]["by_status"], {"failed_terminal": 3})
            self.assertEqual(payload["summary"]["by_error_type"], {"ValueError": 3})

            due = run_screener_company_worker(
                due=True, limit=5, dry_run=True, db_path=db,
                now_utc=lambda: "2099-01-01T00:00:00+00:00",
            )
            self.assertEqual(due["summary"]["due_count"], 3)

    def test_requeue_preserves_attempt_history_and_records_an_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = self._seeded_queue(root)
            self._settle(db, "failed_terminal")
            prepare_database(db)
            requeue_failed_screener_companies(
                db_path=db, now_utc=lambda: "2099-01-01T00:00:00+00:00"
            )
            conn = connect(db)
            try:
                row = conn.execute(
                    "SELECT * FROM screener_company_queue LIMIT 1"
                ).fetchone()
                self.assertEqual(row["status"], "pending")
                self.assertEqual(row["attempt_count"], 1)
                self.assertIn("boom", row["last_error_json"])
                event = conn.execute(
                    """SELECT * FROM screener_company_queue_events
                       WHERE to_status='pending' AND from_status='failed_terminal'
                       LIMIT 1"""
                ).fetchone()
                self.assertIn("operator_requeue", event["evidence_json"])
            finally:
                conn.close()

    def test_requeue_is_bounded_by_status_path_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = self._seeded_queue(root)
            self._settle(db, "failed_retryable")

            # The default status set leaves retryable failures alone.
            prepare_database(db)
            self.assertEqual(
                requeue_failed_screener_companies(
                    db_path=db, now_utc=lambda: "2099-01-01T00:00:00+00:00"
                )["summary"]["requeued_count"],
                0,
            )
            preview = requeue_failed_screener_companies(
                statuses=("failed_retryable",), paths=("/company/ALPHA/",),
                dry_run=True, db_path=db,
                now_utc=lambda: "2099-01-01T00:00:00+00:00",
            )
            self.assertEqual(preview["summary"]["requeued_count"], 1)
            conn = connect(db)
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM screener_company_queue WHERE status='pending'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

            prepare_database(db)
            limited = requeue_failed_screener_companies(
                statuses=("failed_retryable",), limit=2, db_path=db,
                now_utc=lambda: "2099-01-01T00:00:00+00:00",
            )
            self.assertEqual(limited["summary"]["requeued_count"], 2)

    def test_requeue_refuses_statuses_that_are_not_settled_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self._seeded_queue(Path(directory))
            for status in ("pending", "running", "accepted"):
                with self.assertRaisesRegex(ValueError, "settled failures"):
                    prepare_database(db)
                    requeue_failed_screener_companies(
                        statuses=(status,), db_path=db,
                        now_utc=lambda: "2099-01-01T00:00:00+00:00",
                    )

    @staticmethod
    def _accept_with_metrics(
        db: Path, path: str, *, metric_count: int, fetched_quick_ratios: bool
    ) -> None:
        """Accept one queued company, faking the provenance a real run leaves."""
        conn = connect(db)
        try:
            scope = conn.execute(
                "SELECT reporting_scope FROM screener_company_queue WHERE company_path_normalized=?",
                (path,),
            ).fetchone()["reporting_scope"]
            with conn:
                run_id = conn.execute(
                    """INSERT INTO scrape_runs
                       (started_at_utc, code_version, config_json, status)
                       VALUES ('2099-01-01T00:00:00+00:00', 't', '{}', 'ok')"""
                ).lastrowid
                blob_id = conn.execute(
                    """INSERT INTO response_blobs
                       (content_sha256, byte_size, local_path,
                        first_observed_at_utc, created_at_utc)
                       VALUES (?, 1, ?, '2099-01-01T00:00:00+00:00',
                               '2099-01-01T00:00:00+00:00')""",
                    (f"sha-{path}", f"/dev/null/{path}"),
                ).lastrowid
                kinds = ["company_page"]
                if fetched_quick_ratios:
                    kinds.append("quick_ratios")
                for kind in kinds:
                    conn.execute(
                        """INSERT INTO fetch_observations
                           (run_id, source, requested_url, final_url, fetched_at_utc,
                            http_status, auth_mode, blob_id, fetch_status, resource_kind)
                           VALUES (?, 'screener', 'u', 'u', '2099-01-01T00:00:00+00:00',
                                   200, 'authenticated', ?, 'ok', ?)""",
                        (run_id, blob_id, kind),
                    )
                parse_id = conn.execute(
                    """INSERT INTO parse_artifacts
                       (blob_id, parser_name, parser_version, manifest_version,
                        parsed_at_utc, parse_status, validation_status)
                       VALUES (?, 'p', '1', '1', '2099-01-01T00:00:00+00:00',
                               'ok', 'passed')""",
                    (blob_id,),
                ).lastrowid
                for position in range(metric_count):
                    conn.execute(
                        """INSERT INTO company_metric_observations
                           (parse_id, reporting_scope, label_raw, position,
                            display_value, first_observed_at_utc, available_at_utc,
                            availability_basis)
                           VALUES (?, ?, 'L', ?, 'v',
                                   '2099-01-01T00:00:00+00:00',
                                   '2099-01-01T00:00:00+00:00', 'fetch')""",
                        (parse_id, scope, position),
                    )
                conn.execute(
                    """INSERT INTO screener_company_collection_state
                       (company_path_normalized, reporting_scope,
                        latest_discovered_result_period, latest_accepted_parse_id,
                        updated_at_utc)
                       VALUES (?, ?, '2099-06', ?,
                               '2099-01-01T00:00:00+00:00')
                       ON CONFLICT(company_path_normalized, reporting_scope)
                       DO UPDATE SET latest_accepted_parse_id=excluded.latest_accepted_parse_id""",
                    (path, scope, parse_id),
                )
                conn.execute(
                    """UPDATE screener_company_queue SET status='accepted',
                       accepted_parse_id=?, next_attempt_at_utc=NULL
                       WHERE company_path_normalized=?""",
                    (parse_id, path),
                )
        finally:
            conn.close()

    def test_acceptances_without_a_ratio_panel_fetch_are_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self._seeded_queue(Path(directory))
            self._accept_with_metrics(
                db, "/company/ALPHA/",
                metric_count=9, fetched_quick_ratios=False,
            )
            self._accept_with_metrics(
                db, "/company/500002/",
                metric_count=26, fetched_quick_ratios=True,
            )
            # A company that was asked for the panel and had none is complete at
            # nine metrics; refetching it would change nothing.
            self._accept_with_metrics(
                db, "/company/GVT&D/",
                metric_count=9, fetched_quick_ratios=True,
            )

            stuck = run_screener_company_worker(
                due=True, limit=5, dry_run=True, db_path=db,
                now_utc=lambda: "2099-02-01T00:00:00+00:00",
            )
            self.assertEqual(stuck["summary"]["due_count"], 0)

            prepare_database(db)
            payload = requeue_incomplete_metric_screener_companies(
                db_path=db, now_utc=lambda: "2099-02-01T00:00:00+00:00"
            )
            self.assertEqual(payload["summary"]["requeued_count"], 1)
            self.assertEqual(payload["summary"]["by_metric_count"], {9: 1})
            self.assertEqual(
                payload["items"][0]["company_path_normalized"],
                "/company/ALPHA/",
            )

            due = run_screener_company_worker(
                due=True, limit=5, dry_run=True, db_path=db,
                now_utc=lambda: "2099-02-01T00:00:00+00:00",
            )
            self.assertEqual(due["summary"]["due_count"], 1)

    def test_metric_backfill_dry_run_and_path_limit_do_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self._seeded_queue(Path(directory))
            for path in (
                "/company/ALPHA/", "/company/500002/", "/company/GVT&D/"
            ):
                self._accept_with_metrics(
                    db, path, metric_count=9, fetched_quick_ratios=False
                )

            preview = requeue_incomplete_metric_screener_companies(
                paths=("/company/500002/",), dry_run=True, db_path=db,
                now_utc=lambda: "2099-02-01T00:00:00+00:00",
            )
            self.assertEqual(preview["summary"]["requeued_count"], 1)
            conn = connect(db)
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM screener_company_queue WHERE status='pending'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

            prepare_database(db)
            limited = requeue_incomplete_metric_screener_companies(
                limit=2, db_path=db, now_utc=lambda: "2099-02-01T00:00:00+00:00"
            )
            self.assertEqual(limited["summary"]["requeued_count"], 2)

    def test_metric_backfill_records_the_gap_it_requeued_for(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self._seeded_queue(Path(directory))
            self._accept_with_metrics(
                db, "/company/ALPHA/",
                metric_count=9, fetched_quick_ratios=False,
            )
            prepare_database(db)
            requeue_incomplete_metric_screener_companies(
                db_path=db, now_utc=lambda: "2099-02-01T00:00:00+00:00"
            )
            conn = connect(db)
            try:
                event = conn.execute(
                    """SELECT * FROM screener_company_queue_events
                       WHERE from_status='accepted' AND to_status='pending'"""
                ).fetchone()
                self.assertIn("quick_ratios", event["evidence_json"])
                self.assertIn("quick_ratios_backfill", event["evidence_json"])
            finally:
                conn.close()

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
