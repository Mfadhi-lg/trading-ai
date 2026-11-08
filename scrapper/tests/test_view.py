from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.view.render import (
    _section_matrix,
    _select_periods,
    company_document_links,
    render_browse,
    render_company,
    render_table,
    resolve_company,
)


OBSERVED = "2026-08-06T10:00:00+00:00"

# Two line items across three periods. The last period is a TTM column with no
# `period_end` -- ordering by date would silently drop it, so the renderer must
# order by `column_position` instead.
PNL_ROWS = [
    (1, 1, "Sales +", "Mar 2025", "2025-03-31", "fiscal_year", "5,554"),
    (1, 2, "Sales +", "Mar 2026", "2026-03-31", "fiscal_year", "6,813"),
    (1, 3, "Sales +", "TTM", None, "ttm", "7,270"),
    (2, 1, "Net Profit +", "Mar 2025", "2025-03-31", "fiscal_year", "358"),
    (2, 2, "Net Profit +", "Mar 2026", "2026-03-31", "fiscal_year", "890"),
    (2, 3, "Net Profit +", "TTM", None, "ttm", "1,091"),
    # A row carrying no values at all: dropped from the rendered matrix.
    (3, 1, "Raw PDF", "Mar 2025", "2025-03-31", "fiscal_year", ""),
    (3, 2, "Raw PDF", "Mar 2026", "2026-03-31", "fiscal_year", ""),
    (3, 3, "Raw PDF", "TTM", None, "ttm", ""),
]

METRICS = [
    ("market_cap", "Market Cap", "₹ 99,931 Cr."),
    ("stock_pe", "Stock P/E", "91.4"),
    # Only present on a logged-in fetch: the view must not filter metrics to a
    # fixed allow-list, or customised ratios never surface.
    ("peg_ratio", "PEG Ratio", "21.6"),
    ("intrinsic_value", "Intrinsic Value", "₹ 250"),
]


class ViewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "scraper.db")
        init_db(self.conn, apply_migrations=True)
        # These are rendering tests; seeding the full fetch/parse/blob chain
        # adds nothing, so referential integrity is relaxed for the fixture.
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self._seed()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def _seed(self) -> None:
        c = self.conn
        c.execute(
            "INSERT INTO companies (company_id, current_legal_name, status) "
            "VALUES (1, 'Laurus Labs Ltd', 'active')"
        )
        c.execute(
            "INSERT INTO security_identifiers "
            "(company_id, identifier_type, identifier_value, is_current) "
            "VALUES (1, 'NSE', 'LAURUSLABS', 1)"
        )
        for row_pos, col_pos, item, label, end, ptype, value in PNL_ROWS:
            c.execute(
                """
                INSERT INTO financial_observations
                (parse_id, reporting_scope, section_code, line_item_code,
                 line_item_raw, row_position, period_label_raw, period_end,
                 period_type, column_position, first_observed_at_utc,
                 available_at_utc, availability_basis, display_value)
                VALUES (1, 'standalone', 'pnl', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'first_observed', ?)
                """,
                (
                    item.lower().replace(" ", "_"), item, row_pos, label, end,
                    ptype, col_pos, OBSERVED, OBSERVED, value,
                ),
            )
        for position, (code, label, value) in enumerate(METRICS, start=1):
            c.execute(
                """
                INSERT INTO company_metric_observations
                (parse_id, reporting_scope, metric_code, label_raw, position,
                 display_value, first_observed_at_utc, available_at_utc,
                 availability_basis)
                VALUES (1, 'standalone', ?, ?, ?, ?, ?, ?, 'first_observed')
                """,
                (code, label, position, value, OBSERVED, OBSERVED),
            )
        c.execute(
            """
            INSERT INTO growth_observations
            (parse_id, reporting_scope, metric_code, metric_label_raw,
             block_position, row_position, period_code, period_label_raw,
             display_value, first_observed_at_utc, available_at_utc,
             availability_basis)
            VALUES (1, 'standalone', 'compounded_sales_growth',
                    'Compounded Sales Growth', 0, 1, '10y', '10 Years', '14%',
                    ?, ?, 'first_observed')
            """,
            (OBSERVED, OBSERVED),
        )
        c.execute(
            "INSERT INTO parse_artifacts (parse_id, blob_id, parser_name, "
            "parser_version, manifest_version, parsed_at_utc, parse_status, "
            "validation_status) VALUES "
            "(1, 1, 'screener-company', '1.0.0', '1.3.0', ?, 'ok', 'passed')",
            (OBSERVED,),
        )
        c.execute(
            "INSERT INTO accepted_page_versions (company_id, source, "
            "reporting_scope, resolved_source_period, parse_id, "
            "accepted_at_utc, accepted_by, acceptance_mode) VALUES "
            "(1, 'screener', 'standalone', 'not_applicable', 1, ?, "
            "'system', 'automatic')",
            (OBSERVED,),
        )
        c.commit()


class TableRenderingTests(ViewTestCase):
    def test_numeric_columns_are_right_aligned(self) -> None:
        out = render_table(["Item", "Value"], [["Sales +", "6,813"], ["OPM %", "26%"]])
        rows = out.split("\n")
        self.assertTrue(rows[2].endswith("6,813"))
        self.assertTrue(rows[3].endswith("26%"))
        self.assertEqual(len(rows[2]), len(rows[3]))

    def test_text_columns_stay_left_aligned(self) -> None:
        out = render_table(["Item", "Note"], [["Sales +", "abc"], ["OPM %", "de"]])
        self.assertIn("abc", out)
        self.assertTrue(out.split("\n")[3].rstrip().endswith("de"))


class MatrixTests(ViewTestCase):
    def test_every_period_is_kept_including_ttm(self) -> None:
        items, periods, grid = _section_matrix(self.conn, 1, "pnl")
        self.assertEqual(periods, ["Mar 2025", "Mar 2026", "TTM"])
        self.assertEqual(items, ["Sales +", "Net Profit +"])
        self.assertEqual(grid[0], ["5,554", "6,813", "7,270"])
        self.assertEqual(grid[1], ["358", "890", "1,091"])

    def test_value_less_rows_are_dropped(self) -> None:
        items, _, _ = _section_matrix(self.conn, 1, "pnl")
        self.assertNotIn("Raw PDF", items)

    def test_missing_section_returns_none(self) -> None:
        self.assertIsNone(_section_matrix(self.conn, 1, "cash_flow"))


class PeriodSelectionTests(unittest.TestCase):
    LABELS = ["Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026", "TTM"]
    COLUMNS = [["1"], ["2"], ["3"], ["4"], ["5"]]

    def test_explicit_count_keeps_most_recent(self) -> None:
        keep, total = _select_periods(self.LABELS, self.COLUMNS, periods=2, width=200)
        self.assertEqual(total, 5)
        self.assertEqual([self.LABELS[i] for i in keep], ["Mar 2026", "TTM"])

    def test_zero_means_every_period(self) -> None:
        keep, total = _select_periods(self.LABELS, self.COLUMNS, periods=0, width=40)
        self.assertEqual(len(keep), 5)
        self.assertEqual(total, 5)

    def test_count_above_available_is_clamped(self) -> None:
        keep, _ = _select_periods(self.LABELS, self.COLUMNS, periods=99, width=200)
        self.assertEqual(len(keep), 5)

    def test_auto_fit_drops_oldest_columns_on_narrow_terminal(self) -> None:
        keep, total = _select_periods(self.LABELS, self.COLUMNS, periods=-1, width=60)
        self.assertEqual(total, 5)
        self.assertLess(len(keep), 5)
        # Whatever survives, it is the most recent run of periods.
        self.assertEqual(keep, sorted(keep))
        self.assertEqual(keep[-1], 4)


class CompanyViewTests(ViewTestCase):
    def _render(self, **kwargs) -> str:
        return render_company(self.conn, "LAURUSLABS", periods=0, **kwargs)

    def _seed_documents(self) -> None:
        artifacts = (
            (20, "ppt-old", "presentation", "Q4 FY25 Presentation", None),
            (11, "ppt-new", "presentation", "Q1 FY26 Presentation", None),
            (12, "call-new", "transcript", "Q1 FY26 Earnings Call", "2026-06-30"),
            (13, "rating-new", "credit_rating", "Latest Rating Rationale", None),
        )
        for artifact_id, key, artifact_type, title, period_end in artifacts:
            self.conn.execute(
                """INSERT INTO document_artifacts
                   (artifact_id, company_id, artifact_key, artifact_type,
                    title_raw, period_end, first_observed_at_utc)
                   VALUES (?, 1, ?, ?, ?, ?, ?)""",
                (artifact_id, key, artifact_type, title, period_end, OBSERVED),
            )
            url = (
                "https://documents.example/202607130740_rating.pdf"
                if key == "rating-new"
                else f"https://documents.example/{key}.pdf"
            )
            source_position = 50 if key == "ppt-old" else 10
            self.conn.execute(
                """INSERT INTO artifact_sources
                   (artifact_id, discovery_page_id, discovered_from_source,
                    discovery_page_url, document_url, source_position,
                    source_title_raw, classification_evidence_json,
                    is_primary_source, first_observed_at_utc,
                    last_checked_at_utc, availability_status)
                   VALUES (?, 1, 'company_ir', 'https://documents.example/',
                           ?, ?, ?, '{}', 1, ?, ?, 'available')""",
                (
                    artifact_id,
                    url,
                    source_position,
                    title,
                    OBSERVED,
                    OBSERVED,
                ),
            )
        self.conn.commit()

    def test_renders_all_periods_side_by_side(self) -> None:
        out = self._render()
        self.assertIn("Mar 2025", out)
        self.assertIn("Mar 2026", out)
        self.assertIn("TTM", out)
        self.assertIn("7,270", out)

    def test_renders_every_captured_metric(self) -> None:
        out = self._render()
        for _, label, value in METRICS:
            self.assertIn(label, out)
            self.assertIn(value, out)
        self.assertIn("Key Metrics (4)", out)

    def test_renders_compounded_growth(self) -> None:
        out = self._render()
        self.assertIn("Compounded Growth", out)
        self.assertIn("Compounded Sales Growth", out)
        self.assertIn("10 Years", out)

    def test_renders_latest_document_links_at_the_bottom(self) -> None:
        self._seed_documents()

        documents = company_document_links(self.conn, 1, per_type=1)
        self.assertEqual(
            [document["artifact_id"] for document in documents],
            [11, 12, 13],
        )
        self.assertEqual(documents[-1]["period"], "2026-07-13")

        out = self._render()
        self.assertIn("Documents & Concall Links", out)
        self.assertIn("Presentation / PPT", out)
        self.assertIn("Concall transcript", out)
        self.assertIn("Credit rating", out)
        self.assertIn("https://documents.example/ppt-new.pdf", out)
        self.assertLess(
            out.index("https://documents.example/ppt-new.pdf"),
            out.index("https://documents.example/ppt-old.pdf"),
        )
        self.assertGreater(
            out.index("Documents & Concall Links"), out.index("Profit & Loss")
        )

    def test_section_filter_limits_output(self) -> None:
        out = self._render(sections=["cash_flow"])
        self.assertNotIn("Sales +", out)

    def test_unknown_scope_reports_no_data(self) -> None:
        out = render_company(self.conn, "LAURUSLABS", scope="consolidated")
        self.assertIn("No accepted Screener data", out)

    def test_unknown_company_message(self) -> None:
        self.assertIn("No company matched", render_company(self.conn, "ZZZZ"))


class ResolveAndBrowseTests(ViewTestCase):
    def test_resolves_by_ticker_and_name_fragment(self) -> None:
        self.assertEqual(resolve_company(self.conn, "LAURUSLABS")["company_id"], 1)
        self.assertEqual(resolve_company(self.conn, "laurus")["company_id"], 1)
        self.assertIsNone(resolve_company(self.conn, "NOSUCHTICKER"))

    def test_browse_rejects_unknown_column(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            render_browse(self.conn, "companies", where="nope = 1", limit=5)
        self.assertIn("Unknown column", str(ctx.exception))

    def test_browse_unknown_table_is_reported(self) -> None:
        out = render_browse(self.conn, "no_such_table", where=None, limit=5)
        self.assertIn("Unknown table", out)

    def test_browse_applies_valid_filter(self) -> None:
        out = render_browse(
            self.conn, "financial_observations", where="section_code = 'pnl'", limit=5
        )
        self.assertIn("Sales +", out)


from scrapper.view.shell import (
    CompanyCompleter,
    CompanySlashCompleter,
    resolve_slash_command,
)


class ShellCommandTests(ViewTestCase):
    def test_resolve_slash_command_aliases_and_prefixes(self) -> None:
        self.assertEqual(resolve_slash_command("/COMPA"), "/company")
        self.assertEqual(resolve_slash_command("/comp"), "/company")
        self.assertEqual(resolve_slash_command("/sear"), "/search")
        self.assertEqual(resolve_slash_command("/mon"), "/monitor")
        self.assertEqual(resolve_slash_command("/dl"), "/downloads")
        self.assertEqual(resolve_slash_command("/cov"), "/coverage")
        self.assertEqual(resolve_slash_command("/q"), "/exit")
        self.assertEqual(resolve_slash_command("/h"), "/help")

    def test_company_slash_completer_resolves_prefix_arguments(self) -> None:
        completer = CompanySlashCompleter(self.conn)
        class MockDoc:
            text_before_cursor = "/COMPA laur"

        completions = list(completer.get_completions(MockDoc(), None))
        self.assertTrue(any(c.text == "LAURUSLABS" for c in completions))

    def test_company_completer_sub_prompt(self) -> None:
        completer = CompanyCompleter([{"id": 1, "name": "Laurus Labs Ltd", "nse": "LAURUSLABS", "bse": "540222"}])
        class MockDoc:
            text_before_cursor = "laur"

        completions = list(completer.get_completions(MockDoc(), None))
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].text, "LAURUSLABS")


from scrapper.view.monitor import fetch_monitor_data
from scrapper.view.download_monitor import (
    fetch_download_monitor_data,
    render_ansi_download_monitor,
)


class MonitorDataTests(ViewTestCase):
    def test_fetch_monitor_data_counts_failed_retryable_and_terminal(self) -> None:
        self.conn.execute(
            """
            INSERT INTO screener_result_candidates (candidate_id, screen_run_id, source_position, screen_row_company_id, company_name_raw, company_href_raw, company_path_normalized, reporting_scope_hint, last_result_date_display, result_period, discovered_at_utc)
            VALUES (1, 1, 1, 1, 'Company A', '/company/A/', '/company/A/', 'standalone', '2026-01-01', '1970-01', '2026-08-01 00:00:00')
            """
        )
        self.conn.execute(
            """
            INSERT INTO screener_company_queue (candidate_id, company_path_normalized, reporting_scope, result_period, queue_reason, status, created_at_utc, updated_at_utc) VALUES
            (1, '/company/A/', 'standalone', '1970-01', 'new_company', 'accepted', '2026-08-01 00:00:00', '2026-08-01 00:00:00'),
            (1, '/company/B/', 'standalone', '1970-01', 'new_company', 'pending', '2026-08-01 00:00:00', '2026-08-01 00:00:00'),
            (1, '/company/C/', 'standalone', '1970-01', 'new_company', 'failed_retryable', '2026-08-01 00:00:00', '2026-08-01 00:00:00'),
            (1, '/company/D/', 'standalone', '1970-01', 'new_company', 'failed_retryable', '2026-08-01 00:00:00', '2026-08-01 00:00:00'),
            (1, '/company/E/', 'standalone', '1970-01', 'new_company', 'failed_terminal', '2026-08-01 00:00:00', '2026-08-01 00:00:00')
            """
        )
        data = fetch_monitor_data(self.conn)
        self.assertEqual(data["failed_retryable"], 2)
        self.assertEqual(data["failed_terminal"], 1)
        self.assertEqual(data["failed"], 3)
        self.assertEqual(data["accepted"], 1)
        self.assertEqual(data["pending"], 1)
        self.assertEqual(data["total"], 5)
        self.assertEqual(len(data["recent_items"]), 4)  # accepted, 2 retryable, 1 terminal

    def test_fetch_monitor_data_avg_seconds(self) -> None:
        # Seed candidates and queue items
        self.conn.execute(
            """
            INSERT INTO screener_result_candidates (candidate_id, screen_run_id, source_position, screen_row_company_id, company_name_raw, company_href_raw, company_path_normalized, reporting_scope_hint, last_result_date_display, result_period, discovered_at_utc)
            VALUES (1, 1, 1, 1, 'Company A', '/company/A/', '/company/A/', 'standalone', '2026-01-01', '1970-01', '2026-08-01 00:00:00'),
                   (2, 1, 2, 2, 'Company B', '/company/B/', '/company/B/', 'standalone', '2026-01-01', '1970-01', '2026-08-01 00:00:00')
            """
        )
        self.conn.execute(
            """
            INSERT INTO screener_company_queue (queue_item_id, candidate_id, company_path_normalized, reporting_scope, result_period, queue_reason, status, created_at_utc, updated_at_utc) VALUES
            (1, 1, '/company/A/', 'standalone', '1970-01', 'new_company', 'accepted', '2026-08-01 00:00:00', '2026-08-01 00:00:00'),
            (2, 2, '/company/B/', 'standalone', '1970-01', 'new_company', 'failed_terminal', '2026-08-01 00:00:00', '2026-08-01 00:00:00')
            """
        )
        # Insert events for item 1 (15s duration)
        self.conn.execute(
            """
            INSERT INTO screener_company_queue_events (queue_item_id, event_at_utc, from_status, to_status) VALUES
            (1, '2026-08-08T10:00:00+00:00', 'claimed', 'running'),
            (1, '2026-08-08T10:00:15+00:00', 'running', 'accepted')
            """
        )
        # Insert events for item 2 (45s duration)
        self.conn.execute(
            """
            INSERT INTO screener_company_queue_events (queue_item_id, event_at_utc, from_status, to_status) VALUES
            (2, '2026-08-08T10:00:00+00:00', 'claimed', 'running'),
            (2, '2026-08-08T10:00:45+00:00', 'running', 'failed_terminal')
            """
        )
        data = fetch_monitor_data(self.conn)
        self.assertEqual(data["avg_seconds"], 30.0)


class DownloadMonitorDataTests(ViewTestCase):
    def _seed_download_jobs(self) -> None:
        self.conn.executemany(
            """INSERT INTO document_artifacts
               (artifact_id, company_id, artifact_key, artifact_type, title_raw,
                first_observed_at_utc, acquisition_status,
                transcript_preference_status)
               VALUES (?, 1, ?, 'credit_rating', ?, ?, 'discovered',
                       'not_applicable')""",
            [
                (101, "rating-a", "Rating A", OBSERVED),
                (102, "rating-b", "Rating B", OBSERVED),
                (103, "rating-c", "Rating C", OBSERVED),
                (104, "rating-d", "Rating D", OBSERVED),
            ],
        )
        self.conn.executemany(
            """INSERT INTO credit_rating_download_queue
               (download_job_id, artifact_id, requested_url, status,
                attempt_count, lease_owner, lease_expires_at_utc,
                last_result_status, last_error, created_at_utc, updated_at_utc,
                target_host)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (1, 101, "https://www.careratings.com/a.pdf", "PENDING", 0,
                 None, None, None, None, OBSERVED, OBSERVED, "www.careratings.com"),
                (2, 102, "https://www.careratings.com/b.pdf", "LEASED", 1,
                 "worker-a", "2099-01-01T00:00:00+00:00", None, None,
                 OBSERVED, OBSERVED, "www.careratings.com"),
                (3, 103, "https://www.indiaratings.co.in/c.pdf", "SUCCEEDED", 1,
                 None, None, "VALIDATED_PDF", None, OBSERVED, OBSERVED,
                 "www.indiaratings.co.in"),
                (4, 104, "https://www.icra.in/d.pdf", "SKIPPED_POLICY", 0,
                 None, None, "PUBLISHER_TERMS_BLOCK_AUTOMATION", "Policy blocked",
                 OBSERVED, OBSERVED, "www.icra.in"),
            ],
        )

    def _seed_general_document_jobs(self) -> None:
        self.conn.executemany(
            """INSERT INTO document_artifacts
               (artifact_id, company_id, artifact_key, artifact_type, title_raw,
                first_observed_at_utc, acquisition_status,
                transcript_preference_status)
               VALUES (?, 1, ?, ?, ?, ?, 'discovered', 'not_applicable')""",
            [
                (105, "presentation-a", "presentation", "Investor PPT", OBSERVED),
                (106, "transcript-a", "transcript", "Concall transcript", OBSERVED),
                (107, "recording-a", "recording", "Concall recording", OBSERVED),
            ],
        )
        self.conn.execute(
            """INSERT INTO artifact_download_scopes
               (scope_id, scope_kind, description, artifact_types_json,
                policy_json, created_at_utc, updated_at_utc)
               VALUES ('accepted-screener-concall', 'custom', 'test',
                       '["presentation","transcript","recording"]', '{}', ?, ?)""",
            (OBSERVED, OBSERVED),
        )
        self.conn.execute(
            """INSERT INTO artifact_download_scope_companies
               (scope_id, company_id, symbol)
               VALUES ('accepted-screener-concall', 1, 'LAURUSLABS')"""
        )
        self.conn.executemany(
            """INSERT INTO artifact_download_queue
               (download_job_id, artifact_id, requested_url, target_host,
                scope_id, status, attempt_count, lease_owner,
                lease_expires_at_utc, last_result_status, last_error,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, 'accepted-screener-concall', ?, ?, ?, ?, ?,
                       NULL, ?, ?)""",
            [
                (11, 105, "https://example.com/ppt.pdf", "example.com", "PENDING",
                 0, None, None, None, OBSERVED, OBSERVED),
                (12, 106, "https://example.com/transcript.pdf", "example.com", "LEASED",
                 1, "document-worker", "2099-01-01T00:00:00+00:00", None,
                 OBSERVED, OBSERVED),
                (13, 107, "https://example.com/recording.mp3", "example.com", "SUCCEEDED",
                 1, None, None, "validated", OBSERVED, OBSERVED),
            ],
        )

    def test_fetch_download_monitor_data_tracks_queue_workers_and_hosts(self) -> None:
        self._seed_download_jobs()
        data = fetch_download_monitor_data(self.conn)

        self.assertEqual(data["total"], 4)
        self.assertEqual(data["completed"], 2)
        self.assertEqual(data["pending"], 1)
        self.assertEqual(data["leased"], 1)
        self.assertEqual(data["succeeded"], 1)
        self.assertEqual(data["skipped"], 1)
        self.assertEqual(data["ready"], 1)
        self.assertEqual(data["active_items"][0]["worker"], "worker-a")
        care = next(item for item in data["hosts"] if item["host"] == "www.careratings.com")
        self.assertEqual(care["queued"], 1)
        self.assertEqual(care["active"], 1)
        icra = next(item for item in data["hosts"] if item["host"] == "www.icra.in")
        self.assertEqual(icra["policy"], "BLOCKED_TERMS")

    def test_ansi_download_monitor_includes_file_metrics(self) -> None:
        self._seed_download_jobs()
        data = fetch_download_monitor_data(self.conn)
        output = render_ansi_download_monitor(data, 0.0, 0, data["completed"])

        self.assertIn("FILE DOWNLOAD MONITOR", output)
        self.assertIn("www.careratings.com", output)
        self.assertIn("Downloaded 1", output)

    def test_download_monitor_combines_rating_and_general_document_queues(self) -> None:
        self._seed_download_jobs()
        self._seed_general_document_jobs()

        # The same artifact can be materialized into overlapping acquisition
        # scopes/queue implementations.  The monitor reports files, not those
        # internal queue copies, and keeps the successful state.
        self.conn.execute(
            """INSERT INTO artifact_download_queue
               (download_job_id, artifact_id, requested_url, target_host,
                scope_id, status, attempt_count, last_result_status, last_error,
                created_at_utc, updated_at_utc)
               VALUES (14, 103, 'https://www.indiaratings.co.in/c.pdf',
                       'www.indiaratings.co.in', 'accepted-screener-concall',
                       'RETRYABLE_FAILURE', 0, 'HOST_CIRCUIT_OPEN',
                       'deferred duplicate', ?, ?)""",
            (OBSERVED, OBSERVED),
        )

        data = fetch_download_monitor_data(self.conn)

        self.assertEqual(data["total"], 7)
        self.assertEqual(data["pending"], 2)
        self.assertEqual(data["leased"], 2)
        self.assertEqual(data["succeeded"], 2)
        self.assertEqual(data["ready"], 2)
        self.assertEqual(
            data["type_counts"],
            {
                "credit_rating": 4,
                "presentation": 1,
                "recording": 1,
                "transcript": 1,
            },
        )
        self.assertIn(
            "transcript",
            {item["artifact_type"] for item in data["active_items"]},
        )
        output = render_ansi_download_monitor(data, 0.0, 0, data["completed"])
        self.assertIn("presentation=1", output)
        self.assertIn("transcript=1", output)


class DownloadMonitorCliTests(unittest.TestCase):
    def test_top_level_and_view_commands_are_available(self) -> None:
        from scrapper.cli import _parser

        top = _parser().parse_args(["download-monitor", "--refresh", "2"])
        alias = _parser().parse_args(["monitor-downloads"])
        view = _parser().parse_args(["view", "downloads"])

        self.assertEqual(top.command, "download-monitor")
        self.assertEqual(top.refresh, 2.0)
        self.assertEqual(alias.command, "monitor-downloads")
        self.assertEqual(view.view_mode, "downloads")


if __name__ == "__main__":
    unittest.main()
