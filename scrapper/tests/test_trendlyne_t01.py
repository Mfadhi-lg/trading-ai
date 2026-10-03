from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path

from bs4 import BeautifulSoup

from scrapper.core.http import FetchResponse
from scrapper.pipeline.trendlyne_vertical import ingest_trendlyne_response
from scrapper.sources.trendlyne.manifest import load_trendlyne_manifest
from scrapper.sources.trendlyne.monthly_mf import parse_monthly_mf_page
from scrapper.sources.trendlyne.urls import (
    monthly_mf_url,
    source_period_for_url,
    verified_mapping,
)


TEST_ROOT = Path(__file__).resolve().parent
FIXTURE = TEST_ROOT / "fixtures" / "trendlyne" / "RBA_latest.html"
GOLDEN = TEST_ROOT / "golden" / "rba_t01_expected.json"
T02_GOLDEN = TEST_ROOT / "golden" / "rba_t02_expected.json"
T03_GOLDEN = TEST_ROOT / "golden" / "rba_t03_expected.json"
T04_GOLDEN = TEST_ROOT / "golden" / "rba_t04_expected.json"
T05_GOLDEN = TEST_ROOT / "golden" / "rba_t05_expected.json"
OLDER_FIXTURE = TEST_ROOT / "fixtures" / "trendlyne" / "RBA_2026-05.html"
OLDER_GOLDEN = TEST_ROOT / "golden" / "rba_2026_05_expected.json"
URL = (
    "https://trendlyne.com/equity/monthly-mutual-fund-share-holding/"
    "357598/RBA/latest/restaurant-brands-asia-ltd/"
)
FIRST_FETCH = "2026-08-04T15:21:12+00:00"
FIXTURE_SHA256 = "ce65c6cbcc0dcc6ba372a5898ad39e78634aae44ef41687ad1ffe2c011a0c065"


def fixture_response(content: bytes | None = None, url: str = URL) -> FetchResponse:
    return FetchResponse(
        requested_url=url,
        final_url=url,
        status_code=200,
        content=content if content is not None else FIXTURE.read_bytes(),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


class TrendlyneT05ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parsed = parse_monthly_mf_page(FIXTURE.read_bytes())
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.t02_golden = json.loads(T02_GOLDEN.read_text(encoding="utf-8"))
        cls.t03_golden = json.loads(T03_GOLDEN.read_text(encoding="utf-8"))
        cls.t04_golden = json.loads(T04_GOLDEN.read_text(encoding="utf-8"))
        cls.t05_golden = json.loads(T05_GOLDEN.read_text(encoding="utf-8"))
        cls.older = parse_monthly_mf_page(OLDER_FIXTURE.read_bytes())
        cls.older_golden = json.loads(OLDER_GOLDEN.read_text(encoding="utf-8"))

    def test_verified_rba_mapping_and_url(self) -> None:
        mapping = verified_mapping("rba")
        self.assertEqual(mapping.source_company_id, "357598")
        self.assertEqual(mapping.source_symbol, "RBA")
        self.assertEqual(mapping.source_slug, "restaurant-brands-asia-ltd")
        self.assertEqual(monthly_mf_url(mapping), URL)
        self.assertEqual(source_period_for_url("latest"), "latest")
        self.assertEqual(source_period_for_url("2026-05"), "May-2026")
        self.assertEqual(
            monthly_mf_url(mapping, source_period_for_url("2026-05")),
            URL.replace("latest", "May-2026"),
        )
        with self.assertRaises(ValueError):
            source_period_for_url("May-2026")
        with self.assertRaises(ValueError):
            verified_mapping("LAURUSLABS")

    def test_identity_and_selected_month_match_golden(self) -> None:
        parsed = self.parsed
        actual = {
            "company_name_raw": parsed.company_name_raw,
            "source_company_id": parsed.source_company_id,
            "source_symbol": parsed.source_symbol,
            "source_slug": parsed.source_slug,
            "bse_code": parsed.bse_code,
            "canonical_url": parsed.canonical_url,
            "canonical_period": parsed.canonical_period,
            "selected_month_raw": parsed.selected_month_raw,
            "active_month_raw": parsed.active_month_raw,
            "active_url_period_raw": parsed.active_url_period_raw,
            "heading_month_raw": parsed.heading_month_raw,
            "resolved_source_period": parsed.resolved_source_period,
            "identity_field_count": len(parsed.identity_fields),
            "period_field_count": len(parsed.period_fields),
        }
        self.assertEqual(actual, self.golden)
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), FIXTURE_SHA256
        )

    def test_fund_table_cells_and_scheme_ids_match_golden(self) -> None:
        parsed = self.parsed
        golden = self.t02_golden
        header_cells = [cell for cell in parsed.fund_cells if cell.role == "column_header"]
        body_cells = [cell for cell in parsed.fund_cells if cell.role in {"scheme", "data"}]
        footer_cells = [
            cell
            for cell in parsed.fund_cells
            if cell.role in {"summary_label", "summary_data"}
        ]
        self.assertEqual(len(parsed.fund_cells), golden["source_cell_count"])
        self.assertEqual(len(header_cells), golden["header_cell_count"])
        self.assertEqual(len(body_cells), golden["body_cell_count"])
        self.assertEqual(len(footer_cells), golden["footer_cell_count"])
        self.assertEqual(len(parsed.fund_rows), golden["fund_row_count"])
        self.assertEqual(parsed.fund_logical_column_count, golden["logical_column_count"])
        self.assertEqual(
            [
                [cell.row, cell.column, cell.display_text, cell.rowspan, cell.colspan]
                for cell in header_cells
            ],
            golden["header_fingerprint"],
        )
        for parsed_row, expected_row in (
            (parsed.fund_rows[0], golden["first_fund"]),
            (parsed.fund_rows[-1], golden["last_fund"]),
        ):
            self.assertEqual(
                {
                    "row_position": parsed_row.row,
                    "scheme_name_raw": parsed_row.scheme_name_raw,
                    "source_scheme_id": parsed_row.source_scheme_id,
                    "source_url": parsed_row.source_url,
                },
                expected_row,
            )
        self.assertEqual(
            [cell.display_text for cell in body_cells[:8]],
            golden["first_fund_displays"],
        )
        self.assertEqual(
            [cell.display_text for cell in footer_cells],
            golden["footer_displays"],
        )
        self.assertEqual(
            len({row.source_scheme_id for row in parsed.fund_rows}),
            len(parsed.fund_rows),
        )

    def test_dashes_blanks_and_zeros_remain_distinct_source_cells(self) -> None:
        displays = [cell.display_text for cell in self.parsed.fund_cells]
        self.assertIn("-", displays)
        self.assertIn("-%", displays)
        self.assertIn("", displays)
        self.assertIn("0", displays)
        self.assertIn("0%", displays)
        first_row = [cell for cell in self.parsed.fund_cells if cell.row == 2]
        self.assertEqual(
            [cell.column_label for cell in first_row],
            [
                "MF",
                "Jun-2026 / AUM (Cr)",
                "Jun-2026 / AUM %",
                "Jun-2026 / Shares Held",
                "Jun-2026 / Month Change",
                "Jun-2026 / Month Change %",
                "May-2026 / Shares Held",
                "May-2026 / Month Change %",
            ],
        )

    def test_incomplete_rows_or_scheme_identity_never_parse_partially(self) -> None:
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        first_row = soup.select_one(".mf-shareholding-table tbody > tr")
        self.assertIsNotNone(first_row)
        first_row.find_all("td", recursive=False)[-1].decompose()
        with self.assertRaisesRegex(ValueError, "expected 8"):
            parse_monthly_mf_page(soup.encode())

        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        scheme_link = soup.select_one(".mf-shareholding-table tbody > tr td a[href]")
        self.assertIsNotNone(scheme_link)
        del scheme_link["href"]
        with self.assertRaisesRegex(ValueError, "incomplete scheme identity"):
            parse_monthly_mf_page(soup.encode())

    def test_normalized_fund_observations_match_golden(self) -> None:
        observations = self.parsed.fund_observations
        golden = self.t03_golden
        self.assertEqual(len(observations), golden["observation_count"])
        self.assertEqual(
            Counter(observation.metric_code for observation in observations),
            golden["metric_counts"],
        )
        self.assertEqual(
            Counter(observation.unit_code for observation in observations),
            golden["unit_counts"],
        )
        self.assertEqual(
            Counter(observation.period_role for observation in observations),
            {
                "current": golden["current_observation_count"],
                "comparison": golden["comparison_observation_count"],
            },
        )
        self.assertEqual(
            Counter(observation.missing_kind or "present" for observation in observations),
            golden["missing_counts"],
        )
        first = [
            observation
            for observation in observations
            if observation.source_scheme_id == "6770"
        ]
        self.assertEqual(
            [
                [
                    observation.metric_code,
                    observation.period_role,
                    observation.period_label_raw,
                    observation.display_value,
                    observation.value_decimal,
                    observation.unit_code,
                    observation.scale_multiplier,
                    observation.missing_kind,
                ]
                for observation in first
            ],
            golden["first_fund_observations"],
        )

    def test_normalized_values_keep_missing_zero_signs_and_units(self) -> None:
        by_key = {
            (observation.source_scheme_id, observation.column): observation
            for observation in self.parsed.fund_observations
        }
        missing_aum = by_key[("6770", 1)]
        self.assertEqual((missing_aum.display_value, missing_aum.value_decimal), ("-", None))
        self.assertEqual(missing_aum.missing_kind, "dash")

        zero_aum = by_key[("3010621", 1)]
        missing_aum_percentage = by_key[("3010621", 2)]
        self.assertEqual((zero_aum.display_value, zero_aum.value_decimal), ("0", "0"))
        self.assertIsNone(zero_aum.missing_kind)
        self.assertEqual(
            (
                missing_aum_percentage.display_value,
                missing_aum_percentage.value_decimal,
                missing_aum_percentage.missing_kind,
            ),
            ("-%", None, "dash"),
        )

        negative_change = by_key[("2983238", 4)]
        percentage = by_key[("2983238", 5)]
        self.assertEqual(
            (negative_change.display_value, negative_change.value_decimal, negative_change.unit_code),
            ("-191", "-191", "COUNT"),
        )
        self.assertEqual(
            (percentage.display_value, percentage.value_decimal, percentage.unit_code),
            ("-17.78%", "-17.78", "PERCENT_POINT"),
        )

    def test_all_funds_are_retained_across_typed_month_periods(self) -> None:
        observations = self.parsed.fund_observations
        self.assertEqual(
            {observation.source_scheme_id for observation in observations},
            {row.source_scheme_id for row in self.parsed.fund_rows},
        )
        self.assertIn("2983238", {observation.source_scheme_id for observation in observations})
        self.assertEqual(
            {
                (
                    observation.period_role,
                    observation.period_label_raw,
                    observation.source_period,
                    observation.period_end,
                    observation.period_type,
                )
                for observation in observations
            },
            {
                ("current", "Jun-2026", "2026-06", "2026-06-30", "month"),
                ("comparison", "May-2026", "2026-05", "2026-05-31", "month"),
            },
        )

    def test_monthly_totals_and_claims_match_golden(self) -> None:
        summary = self.parsed.summary
        actual = {
            key: getattr(summary, key)
            for key in self.t04_golden
        }
        self.assertEqual(actual, self.t04_golden)
        self.assertEqual(len(self.parsed.summary_fields), 1)
        self.assertEqual(
            self.parsed.summary_fields[0].display_text,
            self.t04_golden["headline_display_text"],
        )
        self.assertEqual(
            self.parsed.summary_fields[0].role, "untrusted_source_summary"
        )
        self.assertEqual(summary.headline_net_change, "8412818")
        self.assertEqual(summary.table_total_change, "-8412818")
        self.assertEqual(summary.computed_net_change, "-8412818")

    def test_all_month_links_match_golden(self) -> None:
        links = self.parsed.month_links
        golden = self.t05_golden
        self.assertEqual(len(links), golden["month_link_count"])
        active = [link for link in links if link.is_active]
        self.assertEqual(len(active), 1)
        for link, expected in (
            (links[0], golden["first"]),
            (active[0], golden["active"]),
            (links[-1], golden["last"]),
        ):
            self.assertEqual(
                {
                    "position": link.position,
                    "display_text": link.display_text,
                    "source_period": link.source_period,
                    "source_url": link.source_url,
                    "is_active": link.is_active,
                },
                expected,
            )
            self.assertEqual(link.source_company_id, "357598")
            self.assertEqual(link.source_symbol, "RBA")
            self.assertEqual(link.source_slug, "restaurant-brands-asia-ltd")

    def test_month_history_never_parses_partially(self) -> None:
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        month_link = soup.select_one(".mf-sh-month-dd-menu a[href]")
        self.assertIsNotNone(month_link)
        month_link["href"] = str(month_link["href"]).replace("Jul-2026", "Jun-2026")
        with self.assertRaisesRegex(ValueError, "label/URL mismatch"):
            parse_monthly_mf_page(soup.encode())

    def test_older_month_preserves_extra_comparison_and_exiting_fund(self) -> None:
        parsed = self.older
        golden = self.older_golden
        self.assertEqual(
            hashlib.sha256(OLDER_FIXTURE.read_bytes()).hexdigest(),
            golden["fixture_sha256"],
        )
        self.assertEqual(OLDER_FIXTURE.stat().st_size, golden["fixture_byte_size"])
        self.assertEqual(parsed.resolved_source_period, golden["resolved_source_period"])
        self.assertEqual(parsed.canonical_period, golden["canonical_period"])
        self.assertEqual(len(parsed.month_links), golden["month_link_count"])
        self.assertEqual(
            [link.position for link in parsed.month_links if link.is_active],
            [golden["active_month_position"]],
        )
        self.assertEqual(parsed.fund_logical_column_count, golden["logical_column_count"])
        self.assertEqual(len(parsed.fund_cells), golden["source_cell_count"])
        self.assertEqual(len(parsed.fund_rows), golden["fund_row_count"])
        self.assertEqual(len(parsed.fund_observations), golden["observation_count"])
        self.assertEqual(
            Counter(
                (observation.period_role, observation.source_period)
                for observation in parsed.fund_observations
            ),
            Counter(
                {
                    ("current", "2026-05"): golden["current_observation_count"],
                    **{
                        ("comparison", period): count
                        for period, count in golden["comparison_observation_counts"].items()
                    },
                }
            ),
        )
        self.assertEqual(
            {
                "computed_buyers": parsed.summary.computed_buyers,
                "computed_sellers": parsed.summary.computed_sellers,
                "computed_holders": parsed.summary.computed_holders,
                "computed_net_change": parsed.summary.computed_net_change,
                "table_total_change": parsed.summary.table_total_change,
                "headline_net_change": parsed.summary.headline_net_change,
            },
            {
                key: golden[key]
                for key in (
                    "computed_buyers",
                    "computed_sellers",
                    "computed_holders",
                    "computed_net_change",
                    "table_total_change",
                    "headline_net_change",
                )
            },
        )
        exiting = golden["exiting_fund"]
        observations = {
            (observation.source_scheme_id, observation.metric_code, observation.source_period): observation
            for observation in parsed.fund_observations
        }
        prior = observations[
            (exiting["source_scheme_id"], "shares_held", exiting["prior_source_period"])
        ]
        current = observations[
            (exiting["source_scheme_id"], "shares_held", exiting["current_source_period"])
        ]
        change = observations[
            (exiting["source_scheme_id"], "month_change", exiting["current_source_period"])
        ]
        self.assertEqual(current.scheme_name_raw, exiting["scheme_name_raw"])
        self.assertEqual(prior.value_decimal, exiting["prior_shares"])
        self.assertEqual(current.value_decimal, exiting["current_shares"])
        self.assertEqual(change.value_decimal, exiting["current_month_change"])
        self.assertIsNone(current.missing_kind)

    def test_manifest_captures_fund_observations(self) -> None:
        manifest = load_trendlyne_manifest()
        statuses = {row["code"]: row["status"] for row in manifest["components"]}
        self.assertEqual(manifest["manifest_version"], "0.5.0")
        self.assertEqual(statuses["company_identity"], "capture")
        self.assertEqual(statuses["selected_month"], "capture")
        self.assertEqual(statuses["fund_table_cells"], "capture")
        self.assertEqual(statuses["fund_observations"], "capture")
        self.assertEqual(statuses["headline_summary"], "capture")
        self.assertEqual(statuses["table_total"], "capture")
        self.assertEqual(statuses["month_history_links"], "capture")


class TrendlyneT05PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = root / "scraper.db"
        self.snapshots = root / "snapshots"
        self.exports = root / "exports"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ingest(
        self,
        content: bytes | None = None,
        *,
        requested_source_period: str = "latest",
        url: str = URL,
    ):
        prepare_database(self.db_path)
        return ingest_trendlyne_response(
            "RBA",
            fixture_response(content, url),
            requested_source_period=requested_source_period,
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )

    def test_end_to_end_resolves_latest_and_uses_month_pointer(self) -> None:
        result = self.ingest()
        self.assertEqual(result.requested_source_period, "latest")
        self.assertEqual(result.resolved_source_period, "2026-06")
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["provenance"]["response_blob"]["content_sha256"],
            FIXTURE_SHA256,
        )
        self.assertEqual(
            payload["provenance"]["fetch"]["requested_source_period"], "latest"
        )
        self.assertEqual(
            payload["provenance"]["fetch"]["resolved_source_period"], "2026-06"
        )
        self.assertEqual(
            payload["provenance"]["accepted_version"]["resolved_source_period"],
            "2026-06",
        )
        self.assertEqual(
            payload["normalized"]["monthly_page"]["selected_month_raw"],
            "Jun 2026",
        )
        self.assertEqual(len(payload["source_faithful"]["sections"]), 5)
        self.assertEqual(
            [len(section["cells"]) for section in payload["source_faithful"]["sections"]],
            [0, 0, 0, 0, 234],
        )
        self.assertEqual(
            payload["source_faithful"]["sections"][3]["fields"][0]["field_role"],
            "untrusted_source_summary",
        )
        self.assertEqual(
            len(payload["source_faithful"]["sections"][2]["fields"]), 91
        )
        self.assertEqual(len(payload["source_faithful"]["fund_rows"]), 27)
        self.assertEqual(
            payload["source_faithful"]["fund_rows"][0]["source_scheme_id"],
            "6770",
        )
        self.assertEqual(
            set(payload["normalized"]),
            {
                "monthly_page",
                "month_history",
                "monthly_summary",
                "fund_observations",
            },
        )
        month_history = payload["normalized"]["month_history"]
        self.assertEqual(len(month_history), 91)
        self.assertEqual(
            [row["source_period"] for row in month_history if row["is_active"]],
            ["2026-06"],
        )
        self.assertTrue(
            all(
                row["first_observed_at_utc"] == FIRST_FETCH
                and row["available_at_utc"] == FIRST_FETCH
                and row["availability_basis"] == "first_observed"
                and row["source_published_at_utc"] is None
                for row in month_history
            )
        )
        summary = payload["normalized"]["monthly_summary"]
        self.assertEqual(summary["headline_net_change"], "8412818")
        self.assertEqual(summary["table_total_change"], "-8412818")
        self.assertEqual(summary["computed_net_change"], "-8412818")
        self.assertEqual(
            (
                summary["computed_buyers"],
                summary["computed_sellers"],
                summary["computed_holders"],
            ),
            ("1", "10", "25"),
        )
        self.assertEqual(summary["headline_is_canonical"], 0)
        self.assertEqual(summary["computed_values_are_canonical"], 1)
        self.assertEqual(summary["first_observed_at_utc"], FIRST_FETCH)
        self.assertEqual(summary["available_at_utc"], FIRST_FETCH)
        observations = payload["normalized"]["fund_observations"]
        self.assertEqual(len(observations), 189)
        self.assertEqual(
            {observation["source_scheme_id"] for observation in observations},
            {row["source_scheme_id"] for row in payload["source_faithful"]["fund_rows"]},
        )
        self.assertTrue(
            all(
                observation["first_observed_at_utc"] == FIRST_FETCH
                and observation["available_at_utc"] == FIRST_FETCH
                and observation["availability_basis"] == "first_observed"
                and observation["source_published_at_utc"] is None
                for observation in observations
            )
        )
        self.assertEqual(
            {(rule["rule_code"], rule["status"]) for rule in payload["quality"]},
            {
                ("trendlyne_identity_company_id", "passed"),
                ("trendlyne_identity_symbol", "passed"),
                ("trendlyne_identity_slug", "passed"),
                ("trendlyne_canonical_url", "passed"),
                ("trendlyne_selected_month_sources_match", "passed"),
                ("trendlyne_resolved_month_canonical", "passed"),
                ("trendlyne_month_history_present", "passed"),
                ("trendlyne_month_history_source_fields_match", "passed"),
                ("trendlyne_month_history_identity", "passed"),
                ("trendlyne_month_history_periods", "passed"),
                ("trendlyne_month_history_active_selected", "passed"),
                ("trendlyne_month_history_availability", "passed"),
                ("trendlyne_fund_table_present", "passed"),
                ("trendlyne_fund_table_known_structure", "passed"),
                ("trendlyne_fund_source_cells_complete", "passed"),
                ("trendlyne_fund_scheme_identity", "passed"),
                ("trendlyne_fund_header_months_match", "passed"),
                ("trendlyne_fund_observations_complete", "passed"),
                ("trendlyne_fund_observations_match_source_cells", "passed"),
                ("trendlyne_fund_observation_metadata", "passed"),
                ("trendlyne_fund_observation_numeric_values", "passed"),
                ("trendlyne_fund_observation_availability", "passed"),
                ("trendlyne_monthly_summary_sources_preserved", "passed"),
                ("trendlyne_monthly_summary_metadata", "passed"),
                ("trendlyne_computed_activity_reproducible", "passed"),
                ("trendlyne_table_total_matches_computed_net_change", "passed"),
                ("trendlyne_table_total_shares_match_fund_rows", "passed"),
                ("trendlyne_headline_counts_match_computed", "passed"),
                ("trendlyne_headline_net_change_matches_computed", "failed"),
                ("trendlyne_headline_never_canonical", "passed"),
            },
        )
        headline_rule = next(
            rule
            for rule in payload["quality"]
            if rule["rule_code"]
            == "trendlyne_headline_net_change_matches_computed"
        )
        self.assertEqual(headline_rule["severity"], "warning")
        self.assertEqual(
            payload["provenance"]["response_blob"]["validation_status"],
            "passed_with_warnings",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            pointer = conn.execute(
                """SELECT source, reporting_scope, resolved_source_period
                   FROM accepted_page_versions"""
            ).fetchone()
            self.assertEqual(pointer, ("trendlyne", "not_applicable", "2026-06"))
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM accepted_page_versions
                       WHERE resolved_source_period='latest'"""
                ).fetchone()[0],
                0,
            )
            source_id = conn.execute(
                """SELECT source_company_id, source_symbol, source_slug,
                          mapping_status FROM source_identifiers
                   WHERE source='trendlyne'"""
            ).fetchone()
            self.assertEqual(
                source_id,
                ("357598", "RBA", "restaurant-brands-asia-ltd", "verified"),
            )

    def test_two_months_coexist_under_month_specific_pointers(self) -> None:
        latest = self.ingest()
        older_url = URL.replace("latest", "May-2026")
        older = self.ingest(
            OLDER_FIXTURE.read_bytes(),
            requested_source_period="2026-05",
            url=older_url,
        )
        self.assertNotEqual(latest.parse_id, older.parse_id)
        self.assertEqual(older.requested_source_period, "2026-05")
        self.assertEqual(older.resolved_source_period, "2026-05")
        self.assertEqual(older.export_path.name, "RBA-2026-05.json")
        payload = json.loads(older.export_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["provenance"]["fetch"]["requested_source_period"], "2026-05"
        )
        self.assertEqual(
            payload["provenance"]["accepted_version"]["resolved_source_period"],
            "2026-05",
        )
        self.assertEqual(len(payload["normalized"]["fund_observations"]), 243)
        with closing(sqlite3.connect(self.db_path)) as conn:
            pointers = conn.execute(
                """SELECT resolved_source_period, parse_id
                   FROM accepted_page_versions
                   WHERE source='trendlyne'
                   ORDER BY resolved_source_period"""
            ).fetchall()
            self.assertEqual(
                pointers,
                [("2026-05", older.parse_id), ("2026-06", latest.parse_id)],
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM accepted_page_versions
                       WHERE resolved_source_period='latest'"""
                ).fetchone()[0],
                0,
            )

    def test_requested_historical_month_must_match_resolved_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "different month than requested"):
            self.ingest(requested_source_period="2026-05")
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT requested_source_period, resolved_source_period
                       FROM fetch_observations"""
                ).fetchone(),
                ("2026-05", "2026-06"),
            )

    def test_non_numeric_fund_value_is_preserved_but_rejected(self) -> None:
        changed = FIXTURE.read_bytes().replace(b"241.50", b"not reported", 1)
        with self.assertRaises(ValueError):
            self.ingest(changed)
        with closing(sqlite3.connect(self.db_path)) as conn:
            observation = conn.execute(
                """SELECT display_value, value_decimal, missing_kind
                   FROM trendlyne_fund_observations
                   WHERE source_scheme_id='8319' AND metric_code='aum'"""
            ).fetchone()
            self.assertEqual(observation, ("not reported", None, "parse_error"))
            rule = conn.execute(
                """SELECT status FROM validation_results
                   WHERE rule_code='trendlyne_fund_observation_numeric_values'"""
            ).fetchone()
            self.assertEqual(rule, ("failed",))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )

    def test_table_total_disagreement_is_preserved_but_rejected(self) -> None:
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        footer_cells = soup.select(".mf-shareholding-table tfoot > tr > th")
        self.assertEqual(len(footer_cells), 8)
        footer_cells[4].string = "8,412,818"
        with self.assertRaises(ValueError):
            self.ingest(soup.encode())
        with closing(sqlite3.connect(self.db_path)) as conn:
            summary = conn.execute(
                """SELECT table_total_change_display, table_total_change,
                          computed_net_change
                   FROM trendlyne_mf_page_summaries"""
            ).fetchone()
            self.assertEqual(summary, ("8,412,818", "8412818", "-8412818"))
            rule = conn.execute(
                """SELECT severity, status FROM validation_results
                   WHERE rule_code=
                         'trendlyne_table_total_matches_computed_net_change'"""
            ).fetchone()
            self.assertEqual(rule, ("error", "failed"))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )

    def test_table_header_month_mismatch_rejects_parse(self) -> None:
        changed = FIXTURE.read_bytes().replace(b">Jun-2026</th>", b">Jul-2026</th>", 1)
        with self.assertRaises(ValueError):
            self.ingest(changed)
        with closing(sqlite3.connect(self.db_path)) as conn:
            rule = conn.execute(
                """SELECT status FROM validation_results
                   WHERE rule_code='trendlyne_fund_header_months_match'"""
            ).fetchone()
            self.assertEqual(rule, ("failed",))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )

    def test_identity_mismatch_rejects_parse(self) -> None:
        changed = FIXTURE.read_bytes().replace(
            b'data-myid="357598"', b'data-myid="999999"', 1
        )
        with self.assertRaises(ValueError):
            self.ingest(changed)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )
            rule = conn.execute(
                """SELECT status FROM validation_results
                   WHERE rule_code='trendlyne_identity_company_id'"""
            ).fetchone()
            self.assertEqual(rule, ("failed",))
            self.assertEqual(
                conn.execute("SELECT action FROM acceptance_events").fetchone(),
                ("rejected",),
            )

    def test_selected_month_mismatch_rejects_parse(self) -> None:
        changed = FIXTURE.read_bytes().replace(
            b'dropdown-item  active ', b'dropdown-item ', 1
        )
        with self.assertRaises(ValueError):
            self.ingest(changed)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )
            rule = conn.execute(
                """SELECT status FROM validation_results
                   WHERE rule_code='trendlyne_selected_month_sources_match'"""
            ).fetchone()
            self.assertEqual(rule, ("failed",))
            fetch_periods = conn.execute(
                """SELECT requested_source_period, resolved_source_period
                   FROM fetch_observations"""
            ).fetchone()
            self.assertEqual(fetch_periods, ("latest", "2026-06"))
