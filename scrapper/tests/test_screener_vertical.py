from __future__ import annotations

from scrapper.tests.database_fixtures import prepare_database

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bs4 import BeautifulSoup

from scrapper.core.http import FetchResponse
from scrapper.pipeline.screener_vertical import (
    ingest_screener_response,
    reprocess_screener_blobs,
)
from scrapper.sources.screener.company import parse_company_page
from scrapper.sources.screener.manifest import (
    audit_screener_fixture_coverage,
    load_screener_manifest,
)
from scrapper.sources.screener.peers import parse_peer_table
from scrapper.validation.screener import (
    _source_has_shareholding_warning,
    _source_lists_annual_report,
    validate_parse,
)

TEST_ROOT = Path(__file__).resolve().parent
FIXTURE = TEST_ROOT / "fixtures" / "screener" / "LAURUSLABS_consolidated.html"
PEER_FIXTURE = TEST_ROOT / "fixtures" / "screener" / "LAURUSLABS_peers.html"
STANDALONE_FIXTURE = TEST_ROOT / "fixtures" / "screener" / "LAURUSLABS_standalone.html"
STANDALONE_PEER_FIXTURE = (
    TEST_ROOT / "fixtures" / "screener" / "LAURUSLABS_standalone_peers.html"
)
IRREGULAR_FIXTURE = TEST_ROOT / "fixtures" / "screener" / "NTPCGREEN_consolidated.html"
IRREGULAR_PEER_FIXTURE = TEST_ROOT / "fixtures" / "screener" / "NTPCGREEN_peers.html"
QUICK_RATIOS_FIXTURE = (
    TEST_ROOT / "fixtures" / "screener" / "LAURUSLABS_quick_ratios.html"
)
GOLDEN = TEST_ROOT / "golden" / "lauruslabs_vertical_expected.json"
STANDALONE_GOLDEN = TEST_ROOT / "golden" / "lauruslabs_standalone_expected.json"
IRREGULAR_GOLDEN = TEST_ROOT / "golden" / "ntpcgreen_irregular_expected.json"
URL = "https://www.screener.in/company/LAURUSLABS/consolidated/"
PEER_URL = "https://www.screener.in/api/company/12723395/peers/"
STANDALONE_URL = "https://www.screener.in/company/LAURUSLABS/"
STANDALONE_PEER_URL = "https://www.screener.in/api/company/12723394/peers/"
IRREGULAR_URL = "https://www.screener.in/company/NTPCGREEN/consolidated/"
IRREGULAR_PEER_URL = "https://www.screener.in/api/company/129816617/peers/"
FIRST_FETCH = "2026-08-04T10:02:00+00:00"
IRREGULAR_FETCH = "2026-08-04T15:17:00+00:00"


def fixture_response(content: bytes | None = None) -> FetchResponse:
    return FetchResponse(
        requested_url=URL,
        final_url=URL,
        status_code=200,
        content=content if content is not None else FIXTURE.read_bytes(),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


def fixture_quick_ratios_response(
    content: bytes | None = None,
    *,
    warehouse_id: str = "12723395",
    fetch_status: str = "ok",
    status_code: int = 200,
) -> FetchResponse:
    url = f"https://www.screener.in/api/company/{warehouse_id}/quick_ratios/"
    return FetchResponse(
        requested_url=url,
        final_url=url,
        status_code=status_code,
        content=content if content is not None else QUICK_RATIOS_FIXTURE.read_bytes(),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="authenticated",
        fetch_status=fetch_status,
    )


def fixture_peer_response(content: bytes | None = None) -> FetchResponse:
    return FetchResponse(
        requested_url=PEER_URL,
        final_url=PEER_URL,
        status_code=200,
        content=content if content is not None else PEER_FIXTURE.read_bytes(),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


def fixture_standalone_response(content: bytes | None = None) -> FetchResponse:
    return FetchResponse(
        requested_url=STANDALONE_URL,
        final_url=STANDALONE_URL,
        status_code=200,
        content=content if content is not None else STANDALONE_FIXTURE.read_bytes(),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


def fixture_standalone_peer_response(content: bytes | None = None) -> FetchResponse:
    return FetchResponse(
        requested_url=STANDALONE_PEER_URL,
        final_url=STANDALONE_PEER_URL,
        status_code=200,
        content=(
            content
            if content is not None
            else STANDALONE_PEER_FIXTURE.read_bytes()
        ),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


def fixture_irregular_response(content: bytes | None = None) -> FetchResponse:
    return FetchResponse(
        requested_url=IRREGULAR_URL,
        final_url=IRREGULAR_URL,
        status_code=200,
        content=content if content is not None else IRREGULAR_FIXTURE.read_bytes(),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


def fixture_irregular_peer_response(content: bytes | None = None) -> FetchResponse:
    return FetchResponse(
        requested_url=IRREGULAR_PEER_URL,
        final_url=IRREGULAR_PEER_URL,
        status_code=200,
        content=(
            content if content is not None else IRREGULAR_PEER_FIXTURE.read_bytes()
        ),
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        etag=None,
        last_modified=None,
        auth_mode="public",
        fetch_status="ok",
    )


class ExplicitShareholdingAbsenceTests(unittest.TestCase):
    def content(self, notice=True):
        soup = BeautifulSoup(STANDALONE_FIXTURE.read_bytes(), "html.parser")
        for frequency in ("quarterly", "yearly"):
            container = soup.select_one(f"#{frequency}-shp")
            container.clear()
            if notice:
                paragraph = soup.new_tag("p")
                paragraph.string = "Shareholding pattern is currently not available for this company."
                container.append(paragraph)
        return str(soup).encode()

    def test_explicit_absence_is_preserved_and_accepted(self):
        content = self.content()
        parsed = parse_company_page(content, FIRST_FETCH)
        self.assertEqual(parsed.shareholding_unavailable_frequencies, ["quarterly", "yearly"])
        self.assertEqual(parsed.shareholding_q_observations, [])
        self.assertEqual(parsed.shareholding_y_observations, [])
        self.assertEqual(audit_screener_fixture_coverage(content), [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_database(root / 'scraper.db')
            result = ingest_screener_response(
                "LAURUSLABS", "standalone", fixture_standalone_response(content),
                fixture_standalone_peer_response(), fixture_quick_ratios_response(),
                db_path=root / "scraper.db", snapshot_root=root / "snapshots",
                export_root=root / "exports", fetched_at_utc=FIRST_FETCH,
            )
            self.assertTrue(result.export_path.exists())
            with closing(sqlite3.connect(root / "scraper.db")) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_fields WHERE field_role='source_unavailable'").fetchone()[0], 2)
                for code in ("quarterly_shareholding_table_present", "yearly_shareholding_table_present"):
                    payload = json.loads(result.export_path.read_text())
                    self.assertEqual(next(x for x in payload["quality"] if x["rule_code"] == code)["status"], "passed")

    def test_missing_table_without_notice_still_fails(self):
        content = self.content(False)
        with self.assertRaisesRegex(ValueError, "Quarterly shareholding table is missing"):
            parse_company_page(content, FIRST_FETCH)
        self.assertTrue(audit_screener_fixture_coverage(content))


class ScreenerParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parsed = parse_company_page(FIXTURE.read_bytes(), FIRST_FETCH)
        cls.parsed_peers = parse_peer_table(PEER_FIXTURE.read_bytes(), "1273603")
        cls.parsed_standalone = parse_company_page(
            STANDALONE_FIXTURE.read_bytes(), FIRST_FETCH
        )
        cls.parsed_standalone_peers = parse_peer_table(
            STANDALONE_PEER_FIXTURE.read_bytes(), "1273603"
        )
        cls.parsed_irregular = parse_company_page(
            IRREGULAR_FIXTURE.read_bytes(), IRREGULAR_FETCH
        )
        cls.parsed_irregular_peers = parse_peer_table(
            IRREGULAR_PEER_FIXTURE.read_bytes(), "1285166"
        )
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.standalone_golden = json.loads(
            STANDALONE_GOLDEN.read_text(encoding="utf-8")
        )
        cls.irregular_golden = json.loads(
            IRREGULAR_GOLDEN.read_text(encoding="utf-8")
        )

    def test_standalone_scope_matches_focused_golden(self) -> None:
        parsed = self.parsed_standalone
        peers = self.parsed_standalone_peers
        quarter_sales = [o for o in parsed.financials if o.line_item_code == "sales"]
        pnl_sales = [o for o in parsed.pnl_financials if o.line_item_code == "sales"]
        commentary = [
            text for text in parsed.analysis_texts if text.text_type in {"pro", "con"}
        ]
        subject = [obs for obs in peers.observations if obs.is_subject_company]
        summary = {
            "company_name": parsed.company_name,
            "nse_symbol": parsed.nse_symbol,
            "reporting_scope": parsed.reporting_scope,
            "source_company_id": parsed.source_company_id,
            "source_warehouse_id": parsed.source_warehouse_id,
            "canonical_url": parsed.canonical_url,
            "identity_field_count": len(parsed.identity_fields),
            "metric_count": len(parsed.metrics),
            "financial_counts": {
                "quarters": len(parsed.financials),
                "pnl": len(parsed.pnl_financials),
                "balance_sheet": len(parsed.balance_financials),
                "cash_flow": len(parsed.cash_financials),
                "ratios": len(parsed.ratios_financials),
            },
            "source_cell_counts": {
                "quarters": len(parsed.quarter_cells),
                "pnl": len(parsed.pnl_cells),
                "balance_sheet": len(parsed.balance_cells),
                "cash_flow": len(parsed.cash_cells),
                "ratios": len(parsed.ratios_cells),
                "growth": len(parsed.growth_cells),
                "shareholding_quarterly": len(parsed.shareholding_q_cells),
                "shareholding_yearly": len(parsed.shareholding_y_cells),
            },
            "growth_count": len(parsed.growth_observations),
            "shareholding_counts": {
                "quarterly": len(parsed.shareholding_q_observations),
                "yearly": len(parsed.shareholding_y_observations),
            },
            "document_link_count": len(parsed.document_links),
            "commentary_counts": {
                "pro": sum(text.text_type == "pro" for text in commentary),
                "con": sum(text.text_type == "con" for text in commentary),
            },
            "quarter_sales_first": [
                quarter_sales[0].period_label_raw,
                quarter_sales[0].display_value,
                quarter_sales[0].value_decimal,
            ],
            "quarter_sales_last": [
                quarter_sales[-1].period_label_raw,
                quarter_sales[-1].display_value,
                quarter_sales[-1].value_decimal,
            ],
            "pnl_sales_first": [
                pnl_sales[0].period_label_raw,
                pnl_sales[0].value_decimal,
            ],
            "pnl_sales_last": [
                pnl_sales[-1].period_label_raw,
                pnl_sales[-1].value_decimal,
            ],
            "peer_source_cell_count": len(peers.cells),
            "peer_observation_count": len(peers.observations),
            "peer_subject_metric_count": len(subject),
            "peer_subject_url": subject[0].peer_source_url,
        }
        self.assertEqual(summary, self.standalone_golden)
        self.assertEqual(
            audit_screener_fixture_coverage(STANDALONE_FIXTURE.read_bytes()),
            [],
        )
        self.assertEqual(
            [field.display_text for field in parsed.analysis_fields[:2]],
            ["Pros", "Cons"],
        )

    def test_irregular_short_history_preserves_explicit_missing_values(self) -> None:
        parsed = self.parsed_irregular
        peers = self.parsed_irregular_peers
        financial_blocks = {
            "quarters": parsed.financials,
            "pnl": parsed.pnl_financials,
            "balance_sheet": parsed.balance_financials,
            "cash_flow": parsed.cash_financials,
            "ratios": parsed.ratios_financials,
        }
        source_blocks = {
            "quarters": parsed.quarter_cells,
            "pnl": parsed.pnl_cells,
            "balance_sheet": parsed.balance_cells,
            "cash_flow": parsed.cash_cells,
            "ratios": parsed.ratios_cells,
            "growth": parsed.growth_cells,
            "shareholding_quarterly": parsed.shareholding_q_cells,
            "shareholding_yearly": parsed.shareholding_y_cells,
        }
        subject = [
            observation
            for observation in peers.observations
            if observation.is_subject_company
        ]
        document_types = sorted({link.document_type for link in parsed.document_links})
        summary = {
            "company_name": parsed.company_name,
            "nse_symbol": parsed.nse_symbol,
            "reporting_scope": parsed.reporting_scope,
            "source_company_id": parsed.source_company_id,
            "source_warehouse_id": parsed.source_warehouse_id,
            "canonical_url": parsed.canonical_url,
            "source_cell_counts": {
                key: len(value) for key, value in source_blocks.items()
            },
            "observation_counts": {
                **{key: len(value) for key, value in financial_blocks.items()},
                "growth": len(parsed.growth_observations),
                "shareholding_quarterly": len(parsed.shareholding_q_observations),
                "shareholding_yearly": len(parsed.shareholding_y_observations),
                "documents": len(parsed.document_links),
                "peers": len(peers.observations),
            },
            "period_counts": {
                **{
                    key: len({row.period_label_raw for row in value})
                    for key, value in financial_blocks.items()
                },
                "shareholding_quarterly": len(
                    {row.period_label_raw for row in parsed.shareholding_q_observations}
                ),
                "shareholding_yearly": len(
                    {row.period_label_raw for row in parsed.shareholding_y_observations}
                ),
            },
            "blank_counts": {
                **{
                    key: sum(row.missing_kind == "blank" for row in value)
                    for key, value in financial_blocks.items()
                },
                "growth": sum(
                    row.missing_kind == "blank" for row in parsed.growth_observations
                ),
            },
            "growth_missing": [
                f"{row.metric_code}:{row.period_code}:{row.display_value}"
                for row in parsed.growth_observations
                if row.missing_kind is not None
            ],
            "document_type_counts": {
                kind: sum(link.document_type == kind for link in parsed.document_links)
                for kind in document_types
            },
            "peer_source_cell_count": len(peers.cells),
            "peer_subject_metric_count": len(subject),
            "peer_subject_url": subject[0].peer_source_url,
        }
        self.assertEqual(summary, self.irregular_golden)
        self.assertEqual(
            audit_screener_fixture_coverage(IRREGULAR_FIXTURE.read_bytes()),
            [],
        )

        # An absent source list is not represented as an invented unavailable
        # document. Missing cells inside present tables keep their distinct raw
        # display forms and never become numeric zero.
        self.assertFalse(
            any(
                link.document_type.startswith("concall_")
                for link in parsed.document_links
            )
        )
        blank_financials = [
            row
            for rows in financial_blocks.values()
            for row in rows
            if row.missing_kind == "blank"
        ]
        self.assertTrue(blank_financials)
        self.assertTrue(
            all(
                row.display_value == "" and row.value_decimal is None
                for row in blank_financials
            )
        )
        unavailable_growth = [
            row for row in parsed.growth_observations if row.missing_kind == "blank"
        ]
        self.assertTrue(unavailable_growth)
        self.assertTrue(
            all(
                row.display_value == "%" and row.value_decimal is None
                for row in unavailable_growth
            )
        )

    def test_source_identity_and_counts_match_golden(self) -> None:
        pnl_sales = [o for o in self.parsed.pnl_financials if o.line_item_raw.startswith("Sales")]
        pnl_net = [o for o in self.parsed.pnl_financials if o.line_item_raw.startswith("Net Profit")]
        balance = self.parsed.balance_financials
        balance_assets = [o for o in balance if o.line_item_code == "total_assets"]
        cash = self.parsed.cash_financials
        cash_operating = [o for o in cash if o.line_item_code == "cash_from_operating_activity"]
        cash_cfo = [o for o in cash if o.line_item_code == "cfo_op_pct"]
        ratios = self.parsed.ratios_financials
        ratios_debtor = [o for o in ratios if o.line_item_code == "debtor_days"]
        ratios_roce = [o for o in ratios if o.line_item_code == "roce_pct"]
        ratios_wcd = [o for o in ratios if o.line_item_code == "working_capital_days"]
        shp = self.parsed.shareholding_q_observations
        shp_promoters = [o for o in shp if o.holder_code == "promoters"]
        shp_holders = [o for o in shp if o.holder_code == "no_of_shareholders"]
        shp_y = self.parsed.shareholding_y_observations
        shp_y_promoters = [o for o in shp_y if o.holder_code == "promoters"]
        shp_y_holders = [o for o in shp_y if o.holder_code == "no_of_shareholders"]
        documents = self.parsed.document_links
        document_types = sorted({link.document_type for link in documents})
        profile_links = self.parsed.profile_links
        company_texts = self.parsed.profile_texts + self.parsed.analysis_texts
        summary = {
            "company_name": self.parsed.company_name,
            "company_profile_field_count": len(self.parsed.profile_fields),
            "company_profile_link_types": [link.link_type for link in profile_links],
            "company_text_counts": {
                kind: sum(text.text_type == kind for text in company_texts)
                for kind in sorted({text.text_type for text in company_texts})
            },
            "nse_symbol": self.parsed.nse_symbol,
            "reporting_scope": self.parsed.reporting_scope,
            "metric_labels": [metric.label_raw for metric in self.parsed.metrics],
            "quarter_source_cell_count": len(self.parsed.quarter_cells),
            "financial_observation_count": len(self.parsed.financials),
            "first_period": self.parsed.financials[0].period_label_raw,
            "first_sales": self.parsed.financials[0].value_decimal,
            "last_period": self.parsed.financials[12].period_label_raw,
            "last_sales": self.parsed.financials[12].value_decimal,
            "pnl_source_cell_count": len(self.parsed.pnl_cells),
            "pnl_observation_count": len(self.parsed.pnl_financials),
            "pnl_period_types": sorted({o.period_type for o in self.parsed.pnl_financials}),
            "pnl_first_period": pnl_sales[0].period_label_raw,
            "pnl_first_sales": pnl_sales[0].value_decimal,
            "pnl_last_period": pnl_sales[-1].period_label_raw,
            "pnl_last_sales": pnl_sales[-1].value_decimal,
            "pnl_first_net_profit": pnl_net[0].value_decimal,
            "pnl_last_net_profit": pnl_net[-1].value_decimal,
            "balance_source_cell_count": len(self.parsed.balance_cells),
            "balance_observation_count": len(balance),
            "balance_period_types": sorted({o.period_type for o in balance}),
            "balance_first_period": balance[0].period_label_raw,
            "balance_total_assets_first": balance_assets[0].value_decimal,
            "balance_total_assets_last": balance_assets[-1].value_decimal,
            "balance_expandable_rows": [
                o.line_item_raw
                for o in balance
                if o.is_expandable and o.column == 1
            ],
            "cash_source_cell_count": len(self.parsed.cash_cells),
            "cash_observation_count": len(cash),
            "cash_period_types": sorted({o.period_type for o in cash}),
            "cash_first_period": cash[0].period_label_raw,
            "cash_operating_first": cash_operating[0].value_decimal,
            "cash_operating_last": cash_operating[-1].value_decimal,
            "cash_cfo_op_unit": cash_cfo[0].unit_code,
            "cash_cfo_op_first": cash_cfo[0].value_decimal,
            "cash_expandable_rows": [
                o.line_item_raw
                for o in cash
                if o.is_expandable and o.column == 1
            ],
            "ratios_source_cell_count": len(self.parsed.ratios_cells),
            "ratios_observation_count": len(ratios),
            "ratios_period_types": sorted({o.period_type for o in ratios}),
            "ratios_units": sorted({o.unit_code for o in ratios}),
            "ratios_first_period": ratios[0].period_label_raw,
            "ratios_debtor_days_first": ratios_debtor[0].value_decimal,
            "ratios_debtor_days_last": ratios_debtor[-1].value_decimal,
            "ratios_roce_first": ratios_roce[0].value_decimal,
            "ratios_roce_last": ratios_roce[-1].value_decimal,
            "ratios_working_capital_days_first": ratios_wcd[0].value_decimal,
            "growth_source_cell_count": len(self.parsed.growth_cells),
            "growth_observation_count": len(self.parsed.growth_observations),
            "growth_metric_labels": [
                o.metric_label_raw
                for o in self.parsed.growth_observations
                if o.row == 1
            ],
            "growth_first_value": self.parsed.growth_observations[0].value_decimal,
            "growth_profit_5y": next(
                o.value_decimal
                for o in self.parsed.growth_observations
                if o.metric_code == "compounded_profit_growth"
                and o.period_code == "5y"
            ),
            "shareholding_source_cell_count": len(self.parsed.shareholding_q_cells),
            "shareholding_observation_count": len(shp),
            "shareholding_regimes": sorted({o.classification_regime for o in shp}),
            "shareholding_units": sorted({o.unit_code for o in shp}),
            "shareholding_holder_codes": [
                o.holder_code for o in shp if o.column == 1
            ],
            "shareholding_first_period": shp[0].period_label_raw,
            "shareholding_last_period": shp_promoters[-1].period_label_raw,
            "shareholding_promoters_first": shp_promoters[0].value_decimal,
            "shareholding_promoters_last": shp_promoters[-1].value_decimal,
            "shareholding_no_of_shareholders_first": shp_holders[0].value_decimal,
            "shareholding_no_of_shareholders_unit": shp_holders[0].unit_code,
            "shareholding_yearly_source_cell_count": len(self.parsed.shareholding_y_cells),
            "shareholding_yearly_observation_count": len(shp_y),
            "shareholding_yearly_regimes": sorted({o.classification_regime for o in shp_y}),
            "shareholding_yearly_units": sorted({o.unit_code for o in shp_y}),
            "shareholding_yearly_holder_codes": [
                o.holder_code for o in shp_y if o.column == 1
            ],
            "shareholding_yearly_first_period": shp_y[0].period_label_raw,
            "shareholding_yearly_last_period": shp_y_promoters[-1].period_label_raw,
            "shareholding_yearly_promoters_first": shp_y_promoters[0].value_decimal,
            "shareholding_yearly_promoters_last": shp_y_promoters[-1].value_decimal,
            "shareholding_yearly_no_of_shareholders_first": shp_y_holders[0].value_decimal,
            "document_source_field_count": len(self.parsed.document_fields),
            "document_link_count": len(documents),
            "document_type_counts": {
                kind: sum(link.document_type == kind for link in documents)
                for kind in document_types
            },
            "document_available_count": sum(
                link.availability_status == "available" for link in documents
            ),
            "document_unavailable_count": sum(
                link.availability_status == "unavailable" for link in documents
            ),
            "document_first_url": documents[0].source_url,
            "document_last_url": documents[-1].source_url,
            "peer_source_cell_count": len(self.parsed_peers.cells),
            "peer_observation_count": len(self.parsed_peers.observations),
            "peer_company_count": len(
                {
                    obs.peer_source_company_id
                    for obs in self.parsed_peers.observations
                    if obs.row_kind == "company"
                }
            ),
            "peer_metric_codes": sorted(
                {obs.metric_code for obs in self.parsed_peers.observations}
            ),
            "peer_subject_name": next(
                obs.peer_name_raw
                for obs in self.parsed_peers.observations
                if obs.is_subject_company
            ),
            "peer_subject_cmp": next(
                obs.value_decimal
                for obs in self.parsed_peers.observations
                if obs.is_subject_company and obs.metric_code == "current_price"
            ),
            "peer_median_market_cap": next(
                obs.value_decimal
                for obs in self.parsed_peers.observations
                if obs.row_kind == "median" and obs.metric_code == "market_cap"
            ),
        }
        self.assertEqual(summary, self.golden)

    def test_manifest_covers_every_saved_page_component(self) -> None:
        from scrapper.config import MANIFEST_VERSION

        manifest = load_screener_manifest()
        self.assertEqual(manifest["manifest_version"], MANIFEST_VERSION)
        self.assertEqual(
            audit_screener_fixture_coverage(FIXTURE.read_bytes(), manifest),
            [],
        )

        # Every chart choice and expandable dataset is decided individually and
        # every deferral points to an explicit S14 approval record.
        approvals = {
            row["id"]
            for row in manifest["deferral_approvals"]
            if row["approved"] is True
        }
        deferred_groups = (
            manifest["chart_series"],
            manifest["expandable_schedules"],
            manifest["shareholder_drilldowns"],
        )
        self.assertEqual(len(manifest["chart_series"]), 6)
        self.assertEqual(len(manifest["expandable_schedules"]), 17)
        self.assertEqual(len(manifest["shareholder_drilldowns"]), 12)
        self.assertTrue(
            all(
                row["status"] == "deferred"
                and row["deferral_approval"] in approvals
                for group in deferred_groups
                for row in group
            )
        )

        statuses = {
            row["status"]
            for group in (
                "regions",
                "profile_blocks",
                "tables",
                "document_lists",
                "controls",
                "chart_series",
                "expandable_schedules",
                "shareholder_drilldowns",
                "linked_components",
            )
            for row in manifest[group]
        }
        self.assertEqual(
            statuses,
            {"capture", "metadata_only", "conditional", "deferred"},
        )

    def test_manifest_coverage_detects_new_section_and_schedule(self) -> None:
        content = FIXTURE.read_bytes()
        with_new_section = content.replace(
            b"</main>",
            b'<section id="new-visible-data"><h2>New Data</h2></section></main>',
            1,
        )
        self.assertTrue(
            any(
                "regions differ" in error
                for error in audit_screener_fixture_coverage(with_new_section)
            )
        )

        with_new_schedule = content.replace(
            b"Company.showSchedule('Sales', 'quarters'",
            b"Company.showSchedule('New Schedule', 'quarters'",
            1,
        )
        self.assertTrue(
            any(
                "expandable schedules differ" in error
                for error in audit_screener_fixture_coverage(with_new_schedule)
            )
        )

        with_unknown_document_list = content.replace(
            b'<h3 class="margin-0">Concalls</h3>',
            b'<h3 class="margin-0">Unknown documents</h3>',
            1,
        )
        self.assertTrue(
            any(
                "document lists differ" in error
                for error in audit_screener_fixture_coverage(
                    with_unknown_document_list
                )
            )
        )

    def test_manifest_coverage_tolerates_absent_optional_components(self) -> None:
        # The source omits these components for some companies. Their absence is
        # a property of that company, not uncovered source data, so it must not
        # block acceptance. Every removal below is a shape the live corpus shows.
        content = FIXTURE.read_bytes()
        removals = {
            "quarterly_insights_tab": (
                b'data-tab-id="quarterly-insights"',
                b'data-unentitled-tab="quarterly-insights"',
            ),
            "peer_benchmark_links": (b'id="benchmarks"', b'id="benchmarks-absent"'),
            "key_points": (b'<div class="title">Key Points</div>', b""),
            "domestic_institutions_drilldown": (
                b"Company.showShareholders('domestic_institutions', 'quarterly'",
                b"Company.absentDrilldown('domestic_institutions', 'quarterly'",
            ),
            "related_party_transactions": (
                b'data-title="Related Party Transactions"',
                b'data-absent-title="Related Party Transactions"',
            ),
        }
        for label, (present, absent) in removals.items():
            with self.subTest(component=label):
                self.assertIn(present, content)
                without = content.replace(present, absent, 1)
                self.assertEqual(audit_screener_fixture_coverage(without), [], label)

        # The announcements landing link is always present, but follows the
        # company's listing: NSE-only companies link to the NSE page instead.
        nse_listed = content.replace(
            b"/corp-announcements/", b"/corporate-filings-announcements/", 1
        )
        self.assertEqual(audit_screener_fixture_coverage(nse_listed), [])

    def test_manifest_coverage_still_rejects_unknown_and_missing_components(
        self,
    ) -> None:
        # Optional decisions relax absence only. An unlisted component, and a
        # component with no optional decision behind it, both still fail.
        content = FIXTURE.read_bytes()
        with_unknown_control = content.replace(
            b'data-tab-id="quarterly-insights"', b'data-tab-id="new-unknown-tab"', 1
        )
        self.assertTrue(
            any(
                "controls differ" in error
                for error in audit_screener_fixture_coverage(with_unknown_control)
            )
        )

        without_required_control = content.replace(
            b'data-tab-id="yearly-shp"', b'data-absent-tab="yearly-shp"', 1
        )
        self.assertTrue(
            any(
                "controls differ" in error
                for error in audit_screener_fixture_coverage(without_required_control)
            )
        )

        without_required_region = content.replace(
            b'<section id="quarters"', b'<section id-removed="quarters"', 1
        )
        self.assertTrue(
            any(
                "regions differ" in error
                for error in audit_screener_fixture_coverage(without_required_region)
            )
        )

    def test_about_links_and_pros_cons_preserve_text_and_attribution(self) -> None:
        links = self.parsed.profile_links
        self.assertEqual(
            [link.link_type for link in links],
            ["website", "bse", "nse", "about_source"],
        )
        self.assertEqual(links[0].source_url, "http://www.lauruslabs.com")
        self.assertEqual(links[1].identifier_value, "540222")
        self.assertEqual(links[2].identifier_value, "LAURUSLABS")
        self.assertTrue(links[3].source_url.endswith(".pdf#page=28"))

        description, about = self.parsed.profile_texts
        self.assertEqual(description.text_type, "company_description")
        self.assertTrue(description.display_text.startswith("Laurus Labs · Mkt Cap:"))
        self.assertEqual(about.text_type, "about")
        self.assertTrue(about.display_text.startswith("Founded in 2005, Laurus Labs"))
        self.assertIn("6,500+ people", about.display_text)
        self.assertTrue(about.display_text.endswith("[1]"))

        pros = [text for text in self.parsed.analysis_texts if text.text_type == "pro"]
        cons = [text for text in self.parsed.analysis_texts if text.text_type == "con"]
        self.assertEqual(len(pros), 1)
        self.assertEqual(len(cons), 4)
        self.assertEqual(
            pros[0].display_text,
            "Company has been maintaining a healthy dividend payout of 19.0%",
        )
        self.assertEqual(cons[-1].display_text, "Company might be capitalizing the interest cost")
        self.assertTrue(all(text.is_source_generated for text in (*pros, *cons)))
        self.assertTrue(
            all(
                text.content_origin == "source_generated_commentary"
                for text in (*pros, *cons)
            )
        )

    def test_document_links_preserve_metadata_order_and_unavailable_slots(self) -> None:
        documents = self.parsed.document_links
        self.assertEqual(len(documents), 168)
        self.assertEqual([link.position for link in documents], list(range(168)))
        self.assertEqual(len(self.parsed.document_fields), len(documents))

        first = documents[0]
        self.assertEqual(first.document_type, "announcement")
        self.assertEqual(
            first.title_raw,
            "Announcement under Regulation 30 (LODR)-Earnings Call Transcript",
        )
        self.assertEqual(first.period_label_raw, "30 Jul")
        self.assertTrue((first.source_url or "").startswith("https://www.bseindia.com/"))

        annual = next(
            link
            for link in documents
            if link.document_type == "annual_report"
            and link.period_label_raw == "Financial Year 2026"
        )
        self.assertEqual(annual.title_raw, "Financial Year 2026")
        self.assertEqual(annual.publisher, "bse")
        self.assertIn("18ee28e4-bc0d-4763-aa31-a381a4e120fd.pdf", annual.source_url or "")

        rating = next(link for link in documents if link.document_type == "credit_rating")
        self.assertEqual(rating.period_label_raw, "3 Jul")
        self.assertEqual(rating.publisher, "care")
        self.assertIn("Laurus_Labs_Limited.pdf", rating.source_url or "")

        jul_2026 = {
            link.document_type: link
            for link in documents
            if link.period_label_raw == "Jul 2026"
        }
        self.assertEqual(
            set(jul_2026),
            {
                "concall_transcript",
                "concall_notes",
                "concall_ppt",
                "concall_recording",
            },
        )
        self.assertEqual(jul_2026["concall_notes"].publisher, "screener")
        self.assertEqual(
            jul_2026["concall_notes"].source_url,
            "/concalls/summary/23259367/",
        )
        self.assertTrue(
            (jul_2026["concall_recording"].source_url or "").endswith("10044697.mp3")
        )

        unavailable = [
            link for link in documents if link.availability_status == "unavailable"
        ]
        self.assertEqual(len(unavailable), 8)
        self.assertTrue(all(link.source_url is None for link in unavailable))
        self.assertEqual(
            {(link.period_label_raw, link.document_type) for link in unavailable},
            {
                ("Sep 2025", "concall_transcript"),
                ("Sep 2025", "concall_notes"),
                ("Jan 2025", "concall_transcript"),
                ("Jan 2025", "concall_notes"),
                ("Mar 2022", "concall_transcript"),
                ("Mar 2022", "concall_notes"),
                ("Aug 2017", "concall_transcript"),
                ("Aug 2017", "concall_notes"),
            },
        )

    def test_peer_fragment_preserves_cells_values_and_subject_identity(self) -> None:
        peers = self.parsed_peers
        self.assertEqual(self.parsed.source_warehouse_id, "12723395")
        self.assertEqual(len(peers.cells), 110)
        self.assertEqual(len(peers.observations), 81)
        self.assertNotIn(
            "loading peers table",
            " ".join(cell.display_text.lower() for cell in peers.cells),
        )
        subject = [obs for obs in peers.observations if obs.is_subject_company]
        self.assertEqual(len(subject), 9)
        self.assertTrue(all(obs.peer_source_company_id == "1273603" for obs in subject))
        self.assertTrue(all(obs.peer_name_raw == "Laurus Labs" for obs in subject))
        self.assertEqual(
            next(obs for obs in subject if obs.metric_code == "current_price").display_value,
            "1850.00",
        )
        market_cap = next(
            obs for obs in subject if obs.metric_code == "market_cap"
        )
        self.assertEqual(market_cap.value_decimal, "99979.84")
        self.assertEqual(market_cap.unit_code, "INR_CRORE")
        self.assertEqual(market_cap.scale_multiplier, "10000000")
        median = [obs for obs in peers.observations if obs.row_kind == "median"]
        self.assertEqual(len(median), 9)
        self.assertTrue(all(obs.peer_source_company_id is None for obs in median))

    def test_loading_placeholder_is_not_a_peer_table(self) -> None:
        with self.assertRaisesRegex(ValueError, "no results table"):
            parse_peer_table(
                b'<div id="peers-table-placeholder">Loading peers table ...</div>',
                "1273603",
            )

    def test_growth_unavailable_value_is_missing_not_zero(self) -> None:
        # Stock Price CAGR "10 Years" is a bare "%" on the page: unavailable,
        # not a zero return.
        cagr_10y = next(
            o
            for o in self.parsed.growth_observations
            if o.metric_code == "stock_price_cagr" and o.period_code == "10y"
        )
        self.assertEqual(cagr_10y.display_value, "%")
        self.assertIsNone(cagr_10y.value_decimal)
        self.assertEqual(cagr_10y.missing_kind, "blank")
        self.assertEqual(cagr_10y.unit_code, "PERCENT_POINT")
        # Every other growth cell on this page is a typed percentage.
        typed = [o for o in self.parsed.growth_observations if o.missing_kind is None]
        self.assertEqual(len(typed), 15)
        self.assertTrue(all(o.value_decimal is not None for o in typed))

    def test_pnl_annual_and_ttm_are_distinct_period_types(self) -> None:
        pnl = self.parsed.pnl_financials
        annual = [o for o in pnl if o.period_type == "fiscal_year"]
        ttm = [o for o in pnl if o.period_type == "ttm"]
        self.assertTrue(annual and ttm)
        # A TTM cell never carries a fiscal year or a period-end date.
        self.assertTrue(all(o.fiscal_year is None and o.period_end is None for o in ttm))
        # Annual cells keep a real fiscal year and reporting-period end.
        self.assertTrue(all(o.fiscal_year is not None and o.period_end for o in annual))
        # No single column mixes the two period types.
        by_column: dict[int, set[str]] = {}
        for o in pnl:
            by_column.setdefault(o.column, set()).add(o.period_type)
        self.assertTrue(all(len(types) == 1 for types in by_column.values()))

    def test_balance_sheet_is_annual_and_balances(self) -> None:
        from decimal import Decimal

        balance = self.parsed.balance_financials
        # Every balance-sheet column is an annual fiscal year with a period end;
        # there is no TTM column here.
        self.assertEqual(sorted({o.period_type for o in balance}), ["fiscal_year"])
        self.assertTrue(all(o.fiscal_year and o.period_end for o in balance))
        # Total Assets == Total Liabilities for every displayed period.
        assets = {o.column: o.value_decimal for o in balance if o.line_item_code == "total_assets"}
        liabilities = {
            o.column: o.value_decimal for o in balance if o.line_item_code == "total_liabilities"
        }
        self.assertEqual(len(assets), 12)
        self.assertEqual(assets.keys(), liabilities.keys())
        for column in assets:
            self.assertEqual(Decimal(assets[column]), Decimal(liabilities[column]))

    def test_balance_sheet_preserves_expandable_markers(self) -> None:
        balance = self.parsed.balance_financials
        expandable = {o.line_item_raw: o.schedule_key for o in balance if o.is_expandable}
        self.assertEqual(
            expandable,
            {
                "Borrowings +": "Borrowings",
                "Other Liabilities +": "Other Liabilities",
                "Fixed Assets +": "Fixed Assets",
                "Other Assets +": "Other Assets",
            },
        )
        # A non-drill-down row such as Total Assets carries no expand marker.
        totals = [o for o in balance if o.line_item_code == "total_assets"]
        self.assertTrue(totals and all(not o.is_expandable for o in totals))
        self.assertTrue(all(o.schedule_key is None for o in totals))

    def test_cash_flow_preserves_signs_zero_and_units(self) -> None:
        cash = self.parsed.cash_financials
        # Every cash-flow column is an annual fiscal year; there is no TTM column.
        self.assertEqual(sorted({o.period_type for o in cash}), ["fiscal_year"])
        self.assertTrue(all(o.fiscal_year and o.period_end for o in cash))

        # Negative crore flows keep their sign and INR_CRORE unit.
        operating = [o for o in cash if o.line_item_code == "cash_from_operating_activity"]
        self.assertEqual(operating[0].display_value, "-65")
        self.assertEqual(operating[0].value_decimal, "-65")
        self.assertTrue(all(o.unit_code == "INR_CRORE" for o in operating))

        # A signed zero (-0) stays distinct from an unsigned 0 and is a real value.
        net = [o for o in cash if o.line_item_code == "net_cash_flow"]
        signed_zero = [o for o in net if o.display_value == "-0"]
        self.assertEqual(len(signed_zero), 1)
        self.assertEqual(signed_zero[0].value_decimal, "-0")
        self.assertIsNone(signed_zero[0].missing_kind)

        # CFO/OP is a percentage row even though its label carries no "%".
        cfo = [o for o in cash if o.line_item_code == "cfo_op_pct"]
        self.assertTrue(all(o.unit_code == "PERCENT_POINT" for o in cfo))
        self.assertEqual(cfo[0].display_value, "-24%")
        self.assertEqual(cfo[0].value_decimal, "-24")

        # The three activity rows are the source drill-down rows; the summary
        # rows (Net/Free Cash Flow, CFO/OP) are not expandable.
        expandable = {o.line_item_raw: o.schedule_key for o in cash if o.is_expandable and o.column == 1}
        self.assertEqual(
            expandable,
            {
                "Cash from Operating Activity +": "Cash from Operating Activity",
                "Cash from Investing Activity +": "Cash from Investing Activity",
                "Cash from Financing Activity +": "Cash from Financing Activity",
            },
        )
        self.assertTrue(all(not o.is_expandable for o in net))

    def test_annual_ratios_distinguish_units_and_stay_separate_from_panel(self) -> None:
        ratios = self.parsed.ratios_financials
        # Annual ratios are a fiscal-year table (no TTM column).
        self.assertEqual(sorted({o.period_type for o in ratios}), ["fiscal_year"])
        self.assertTrue(all(o.fiscal_year and o.period_end for o in ratios))

        # Turnover-cycle rows are DAYS; ROCE is percentage points. No crore money
        # unit leaks in from the statement default resolver.
        units = {o.line_item_code: o.unit_code for o in ratios}
        self.assertEqual(units["debtor_days"], "DAYS")
        self.assertEqual(units["cash_conversion_cycle"], "DAYS")
        self.assertEqual(units["working_capital_days"], "DAYS")
        self.assertEqual(units["roce_pct"], "PERCENT_POINT")
        self.assertTrue(
            all(o.unit_code in {"DAYS", "PERCENT_POINT", "RATIO", "COUNT"} for o in ratios)
        )

        # Working Capital Days Mar 2015 is a signed zero, kept distinct from 0.
        wcd = [o for o in ratios if o.line_item_code == "working_capital_days"]
        self.assertEqual(wcd[0].display_value, "-0")
        self.assertEqual(wcd[0].value_decimal, "-0")
        self.assertIsNone(wcd[0].missing_kind)

        # The annual ROCE row is separate from the top-panel ROCE metric: it uses
        # a distinct line_item_code so the two cannot collide.
        panel_codes = {m.metric_code for m in self.parsed.metrics}
        self.assertIn("roce", panel_codes)
        ratio_codes = {o.line_item_code for o in ratios}
        self.assertIn("roce_pct", ratio_codes)
        self.assertEqual(panel_codes & ratio_codes, set())

    def test_quarterly_shareholding_regimes_units_and_totals(self) -> None:
        from decimal import Decimal

        shp = self.parsed.shareholding_q_observations
        # Twelve quarterly periods across five rows (4 categories + count row).
        self.assertEqual(len(shp), 60)
        self.assertEqual(len({o.column for o in shp}), 12)

        # Every quarterly period here is Sep 2023 onwards, so all rows fall in the
        # post-XBRL regime. No row is ever labelled `cross_regime`.
        self.assertEqual(sorted({o.classification_regime for o in shp}), ["xbrl_sep_2022_plus"])
        self.assertTrue(
            all(
                o.classification_regime in {"pre_sep_2022", "xbrl_sep_2022_plus", "unknown"}
                for o in shp
            )
        )

        # Holder categories are percentage points; the shareholder count is COUNT.
        promoters = [o for o in shp if o.holder_code == "promoters"]
        holders = [o for o in shp if o.holder_code == "no_of_shareholders"]
        self.assertTrue(all(o.unit_code == "PERCENT_POINT" for o in promoters))
        self.assertTrue(all(o.unit_code == "COUNT" for o in holders))

        # Percentages keep their exact two-decimal display; counts drop the
        # Indian comma grouping without becoming a float.
        self.assertEqual(promoters[0].display_value, "27.20%")
        self.assertEqual(promoters[0].value_decimal, "27.20")
        self.assertEqual(promoters[0].period_end, "2023-09-30")
        self.assertEqual(holders[0].display_value, "4,02,640")
        self.assertEqual(holders[0].value_decimal, "402640")

        # Each complete period's holder categories total ~100 (display rounding).
        by_period: dict[int, list] = {}
        for o in shp:
            if o.unit_code == "PERCENT_POINT" and o.value_decimal is not None:
                by_period.setdefault(o.column, []).append(Decimal(o.value_decimal))
        self.assertEqual(len(by_period), 12)
        for values in by_period.values():
            self.assertEqual(len(values), 4)
            self.assertLessEqual(abs(sum(values) - Decimal("100")), Decimal("0.1"))

        # The Screener classification warning travels with the data.
        self.assertIsNotNone(self.parsed.shareholding_classification_warning)
        self.assertIn(
            "classifications might have changed",
            self.parsed.shareholding_classification_warning or "",
        )

    def test_yearly_shareholding_coexists_and_spans_regime_boundary(self) -> None:
        from dataclasses import replace
        from decimal import Decimal

        from scrapper.sources.screener.company import is_cross_regime_comparison

        shp_y = self.parsed.shareholding_y_observations
        # Eleven yearly periods across five rows (4 categories + count row).
        self.assertEqual(len(shp_y), 55)
        self.assertEqual(len({o.column for o in shp_y}), 11)

        # The yearly table is parsed independently of the quarterly table and
        # every yearly observation carries frequency='yearly', so the two sets
        # never overwrite one another.
        self.assertTrue(all(o.frequency == "yearly" for o in shp_y))
        self.assertTrue(
            all(o.frequency == "quarterly" for o in self.parsed.shareholding_q_observations)
        )

        # Unlike the (all post-XBRL) quarterly table, the yearly table straddles
        # the Sep-2022 boundary: Mar 2017–Mar 2022 are pre-XBRL, Mar 2023 onward
        # are post-XBRL. Each period is tagged from its OWN end date; no single
        # row is ever labelled `cross_regime`.
        self.assertEqual(
            sorted({o.classification_regime for o in shp_y}),
            ["pre_sep_2022", "xbrl_sep_2022_plus"],
        )
        self.assertEqual({o.classification_regime for o in shp_y} & {"cross_regime"}, set())
        promoters = [o for o in shp_y if o.holder_code == "promoters"]
        mar_2022 = next(o for o in promoters if o.period_label_raw == "Mar 2022")
        mar_2023 = next(o for o in promoters if o.period_label_raw == "Mar 2023")
        jun_2026 = next(o for o in promoters if o.period_label_raw == "Jun 2026")
        self.assertEqual(mar_2022.classification_regime, "pre_sep_2022")
        self.assertEqual(mar_2023.classification_regime, "xbrl_sep_2022_plus")

        # `is_cross_regime_comparison` is a DERIVED property of a pair of
        # observations, not a row tag: True only when both regimes are known and
        # differ.
        self.assertTrue(is_cross_regime_comparison(mar_2022, mar_2023))
        self.assertFalse(is_cross_regime_comparison(mar_2023, jun_2026))
        self.assertFalse(is_cross_regime_comparison(mar_2022, mar_2022))
        # A comparison against an unknown-regime observation cannot be established.
        self.assertFalse(
            is_cross_regime_comparison(
                mar_2022,
                replace(mar_2023, classification_regime="unknown"),
            )
        )

        # Units and holder codes mirror the quarterly table; the count row keeps
        # its Indian-grouped value as a plain integer string.
        self.assertTrue(all(o.unit_code == "PERCENT_POINT" for o in promoters))
        holders = [o for o in shp_y if o.holder_code == "no_of_shareholders"]
        self.assertTrue(all(o.unit_code == "COUNT" for o in holders))
        self.assertEqual(promoters[0].display_value, "30.64%")
        self.assertEqual(promoters[0].value_decimal, "30.64")
        self.assertEqual(promoters[0].period_end, "2017-03-31")
        self.assertEqual(holders[0].display_value, "56,722")
        self.assertEqual(holders[0].value_decimal, "56722")

        # Each complete yearly period's holder categories total ~100.
        by_period: dict[int, list] = {}
        for o in shp_y:
            if o.unit_code == "PERCENT_POINT" and o.value_decimal is not None:
                by_period.setdefault(o.column, []).append(Decimal(o.value_decimal))
        self.assertEqual(len(by_period), 11)
        for values in by_period.values():
            self.assertEqual(len(values), 4)
            self.assertLessEqual(abs(sum(values) - Decimal("100")), Decimal("0.1"))

    def test_preserves_display_and_exact_decimal_values(self) -> None:
        market_cap = self.parsed.metrics[0]
        high_low = self.parsed.metrics[2]
        self.assertEqual(market_cap.display_value, "₹ 99,174 Cr.")
        self.assertEqual(market_cap.value_decimal, "99174")
        self.assertEqual(market_cap.scale_multiplier, "10000000")
        self.assertEqual(json.loads(high_low.value_json or "[]"), ["1840", "810"])

    def test_quote_time_is_separate_and_normalized(self) -> None:
        quote = self.parsed.quote
        self.assertEqual(quote.timestamp_raw, "04 Aug 3:32 p.m.")
        self.assertEqual(quote.timestamp_utc, FIRST_FETCH)
        self.assertEqual(quote.timezone, "Asia/Kolkata")
        self.assertEqual(quote.timestamp_status, "derived_year_from_fetch")

    def test_blank_cells_are_not_zero(self) -> None:
        raw_pdf = [row for row in self.parsed.financials if row.line_item_code == "raw_pdf"]
        self.assertEqual(len(raw_pdf), 13)
        self.assertTrue(all(row.value_decimal is None for row in raw_pdf))
        self.assertTrue(all(row.missing_kind == "blank" for row in raw_pdf))


class ScreenerPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "scraper.db"
        self.snapshots = self.root / "snapshots"
        self.exports = self.root / "exports"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ingest(self, fetched_at: str):
        prepare_database(self.db_path)
        return ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=fetched_at,
        )

    def ingest_standalone(self, fetched_at: str):
        prepare_database(self.db_path)
        return ingest_screener_response(
            "LAURUSLABS",
            "standalone",
            fixture_standalone_response(),
            fixture_standalone_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=fetched_at,
        )

    def ingest_irregular(self, fetched_at: str):
        prepare_database(self.db_path)
        return ingest_screener_response(
            "NTPCGREEN",
            "consolidated",
            fixture_irregular_response(),
            fixture_irregular_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=fetched_at,
        )

    def test_page_without_about_link_or_commentary_is_still_accepted(self) -> None:
        """A missing About hyperlink or an empty Pros/Cons list is not a defect.

        Screener renders both for real companies that otherwise carry complete
        financials. The page must be captured; the absence is recorded as a
        warning instead of discarding the whole company.
        """
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        about = soup.select_one("#top .company-profile .about")
        for anchor in about.find_all("a", href=True):
            anchor.unwrap()
        for side in ("pros", "cons"):
            for item in soup.select(f"section#analysis .{side} ul > li"):
                item.decompose()
        stripped = str(soup).encode()

        parsed = parse_company_page(stripped, FIRST_FETCH)
        self.assertNotIn(
            "about_source", {link.link_type for link in parsed.profile_links}
        )

        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(stripped),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["provenance"]["acceptance_history"][0]["action"], "accepted"
        )
        quality = {rule["rule_code"]: rule for rule in payload["quality"]}
        self.assertEqual(quality["company_profile_links_present"]["status"], "passed")
        self.assertEqual(quality["pros_cons_source_generated"]["status"], "passed")
        # The empty commentary is still reported, just not as a blocker.
        self.assertEqual(quality["pros_cons_text_present"]["status"], "failed")
        self.assertEqual(quality["pros_cons_text_present"]["severity"], "warning")
        # The financial capture is untouched by either absence.
        self.assertEqual(len(payload["normalized"]["quarterly_results"]), 156)

    def test_page_without_desktop_profile_links_is_still_accepted(self) -> None:
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        for group in soup.select("#top .company-links.show-from-tablet-landscape"):
            group.decompose()

        parsed = parse_company_page(str(soup).encode(), FIRST_FETCH)
        self.assertNotIn(
            "nse", {link.link_type for link in parsed.profile_links}
        )
        self.assertNotIn(
            "bse", {link.link_type for link in parsed.profile_links}
        )

    def test_ratio_panel_extends_the_public_nine_to_the_full_set(self) -> None:
        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(),
            fixture_peer_response(),
            fixture_quick_ratios_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            metrics = conn.execute(
                """SELECT label_raw, position FROM company_metric_observations
                   WHERE parse_id=? ORDER BY position""",
                (result.parse_id,),
            ).fetchall()
            # The nine public ratios keep positions 0..8; the panel appends.
            self.assertEqual(len(metrics), 26)
            self.assertEqual([row["position"] for row in metrics], list(range(26)))
            self.assertIn("Profit after tax", {row["label_raw"] for row in metrics})
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*) FROM fetch_observations
                       WHERE resource_kind='quick_ratios' AND fetch_status='ok'"""
                ).fetchone()[0],
                1,
            )

    def test_rejected_ratio_panel_fails_the_company_and_leaves_provenance(self) -> None:
        """A short capture must fail loudly, not accept nine metrics quietly.

        The rejection is what a lapsed session looks like. Recording the attempt
        before raising is what keeps the backfill selector able to tell "never
        asked" apart from "asked and refused".
        """
        with self.assertRaisesRegex(RuntimeError, "Quick-ratios response rejected"):
            prepare_database(self.db_path)
            ingest_screener_response(
                "LAURUSLABS",
                "consolidated",
                fixture_response(),
                fixture_peer_response(),
                fixture_quick_ratios_response(
                    b"", fetch_status="rejected", status_code=403
                ),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc=FIRST_FETCH,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT fetch_status FROM fetch_observations
                   WHERE resource_kind='quick_ratios'"""
            ).fetchone()
            self.assertEqual(row["fetch_status"], "rejected")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM acceptance_events").fetchone()[0],
                0,
            )

    def test_newly_listed_company_without_an_annual_report_is_accepted(self) -> None:
        """A company too young to have filed an annual report is not a defect.

        Screener renders the Annual reports box for every company but leaves it
        empty until the first filing, so a company a few quarters past listing
        has none. Demanding the family rejects complete data.
        """
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        annual_reports = soup.select_one("section#documents div.annual-reports")
        for item in annual_reports.select("ul.list-links > li"):
            item.decompose()
        newly_listed = str(soup).encode()

        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(newly_listed),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["provenance"]["acceptance_history"][0]["action"], "accepted"
        )
        quality = {rule["rule_code"]: rule for rule in payload["quality"]}
        rule = quality["document_link_required_families_present"]
        self.assertEqual(rule["status"], "passed")
        # The waiver is narrow: announcements are still required, and the
        # expected value records that annual reports were not demanded.
        self.assertEqual(rule["expected_value"], "announcement")
        self.assertNotIn(
            "annual_report",
            {row["document_type"] for row in payload["normalized"]["document_links"]},
        )

    def test_dropped_annual_reports_are_accepted(self) -> None:
        """Losing annual reports is accepted and does not block ingestion."""
        content = FIXTURE.read_bytes()
        self.assertTrue(_source_lists_annual_report(content))

        soup = BeautifulSoup(content, "html.parser")
        for anchor in soup.select(
            "section#documents div.annual-reports ul.list-links > li a[href]"
        ):
            del anchor["href"]
        dropped = str(soup).encode()

        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(dropped),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["provenance"]["acceptance_history"][0]["action"], "accepted"
        )

    def test_company_without_institutional_holders_keeps_absent_warning(self) -> None:
        """A company with no FII/DII rows never gets the classification note.

        Screener writes that footnote to explain institutional movements across
        the Sep-2022 XBRL change, so a table holding only Promoters and Public
        has none. The absence is the source's, and must not reject the company.
        """
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        soup.select_one("section#shareholding p.sub.small").decompose()
        without_warning = str(soup).encode()
        self.assertFalse(_source_has_shareholding_warning(without_warning))

        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(without_warning),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["provenance"]["acceptance_history"][0]["action"], "accepted"
        )
        quality = {rule["rule_code"]: rule for rule in payload["quality"]}
        rule = quality["shareholding_classification_warning_present"]
        self.assertEqual(rule["status"], "passed")
        # The verdict records that the source, not the parse, lacked the note.
        self.assertEqual(rule["expected_value"], "0 (absent at source)")
        # Every shareholding row is still captured and validated as usual.
        self.assertTrue(payload["normalized"]["shareholding_quarterly"])

    def test_dropped_classification_warning_is_still_a_blocking_failure(self) -> None:
        """The waiver keys off the source, so losing a present note still blocks.

        LAURUSLABS carries the footnote, so dropping the captured field stands
        in for the parser regression this rule exists to catch.
        """
        self.assertTrue(_source_has_shareholding_warning(FIXTURE.read_bytes()))
        accepted = self.ingest(FIRST_FETCH)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                conn.execute(
                    """DELETE FROM source_fields WHERE field_role='classification_warning'
                       AND section_id IN (SELECT section_id FROM source_sections
                                          WHERE parse_id=?)""",
                    (accepted.parse_id,),
                )
                conn.execute(
                    "DELETE FROM validation_results WHERE parse_id=?",
                    (accepted.parse_id,),
                )
            validate_parse(conn, accepted.parse_id, "LAURUSLABS", "consolidated")
            verdict = conn.execute(
                """SELECT status, severity FROM validation_results
                   WHERE parse_id=?
                     AND rule_code='shareholding_classification_warning_present'""",
                (accepted.parse_id,),
            ).fetchone()
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["severity"], "error")

    def test_company_page_without_profile_exchange_link_is_accepted(self) -> None:
        soup = BeautifulSoup(FIXTURE.read_bytes(), "html.parser")
        for anchor in soup.select("#top .company-links.show-from-tablet-landscape > a"):
            label = " ".join(anchor.get_text(" ", strip=True).split()).upper()
            if label.startswith("BSE:") or label.startswith("NSE:"):
                anchor.decompose()
        parsed = parse_company_page(str(soup).encode(), FIRST_FETCH)
        self.assertEqual(parsed.company_name, "Laurus Labs Ltd")

    def test_irregular_company_accepts_short_history_and_absent_concalls(self) -> None:
        laurus = self.ingest(FIRST_FETCH)
        irregular = self.ingest_irregular(IRREGULAR_FETCH)
        self.assertNotEqual(laurus.company_id, irregular.company_id)

        payload = json.loads(irregular.export_path.read_text(encoding="utf-8"))
        source_identity = payload["provenance"]["source_identifiers"][0]
        self.assertEqual(source_identity["source_symbol"], "NTPCGREEN")
        self.assertEqual(
            payload["provenance"]["accepted_version"]["reporting_scope"],
            "consolidated",
        )
        self.assertEqual(
            payload["provenance"]["response_blob"]["manifest_version"],
            "1.4.0",
        )
        self.assertEqual(len(payload["normalized"]["quarterly_results"]), 132)
        self.assertEqual(len(payload["normalized"]["profit_loss"]), 60)
        self.assertEqual(len(payload["normalized"]["annual_ratios"]), 24)
        self.assertEqual(len(payload["normalized"]["shareholding_yearly"]), 15)
        self.assertEqual(len(payload["normalized"]["document_links"]), 13)
        self.assertEqual(len(payload["normalized"]["peers"]), 72)
        self.assertFalse(
            any(
                row["document_type"].startswith("concall_")
                for row in payload["normalized"]["document_links"]
            )
        )

        ratio_blanks = [
            row
            for row in payload["normalized"]["annual_ratios"]
            if row["missing_kind"] == "blank"
        ]
        self.assertEqual(len(ratio_blanks), 9)
        self.assertTrue(
            all(
                row["display_value"] == "" and row["value_decimal"] is None
                for row in ratio_blanks
            )
        )
        growth_blanks = [
            row
            for row in payload["normalized"]["growth"]
            if row["missing_kind"] == "blank"
        ]
        self.assertEqual(len(growth_blanks), 9)
        self.assertTrue(
            all(
                row["display_value"] == "%" and row["value_decimal"] is None
                for row in growth_blanks
            )
        )
        quality = {rule["rule_code"]: rule for rule in payload["quality"]}
        self.assertTrue(all(rule["status"] == "passed" for rule in quality.values()))
        self.assertEqual(
            quality["document_link_required_families_present"]["expected_value"],
            "announcement",
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            pointers = conn.execute(
                """SELECT si.source_symbol, apv.parse_id
                   FROM accepted_page_versions apv
                   JOIN source_identifiers si ON si.company_id=apv.company_id
                    AND si.source='screener'
                   ORDER BY si.source_symbol"""
            ).fetchall()
            self.assertEqual(
                [(row["source_symbol"], row["parse_id"]) for row in pointers],
                [("LAURUSLABS", laurus.parse_id), ("NTPCGREEN", irregular.parse_id)],
            )
            ntpc_mapping = conn.execute(
                """SELECT source_company_id, canonical_url
                   FROM source_identifiers WHERE source_symbol='NTPCGREEN'"""
            ).fetchone()
            self.assertEqual(
                tuple(ntpc_mapping),
                ("1285166", IRREGULAR_URL),
            )

    def test_consolidated_and_standalone_scopes_coexist(self) -> None:
        consolidated = self.ingest(FIRST_FETCH)
        standalone = self.ingest_standalone("2026-08-04T10:03:00+00:00")

        self.assertEqual(consolidated.company_id, standalone.company_id)
        self.assertNotEqual(consolidated.parse_id, standalone.parse_id)
        self.assertNotEqual(consolidated.peer_parse_id, standalone.peer_parse_id)

        standalone_payload = json.loads(
            standalone.export_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            standalone_payload["provenance"]["accepted_version"]["reporting_scope"],
            "standalone",
        )
        self.assertTrue(
            all(
                row["reporting_scope"] == "standalone"
                for block in (
                    "metrics",
                    "company_profile_links",
                    "company_text",
                    "quarterly_results",
                    "profit_loss",
                    "balance_sheet",
                    "cash_flow",
                    "annual_ratios",
                    "growth",
                    "shareholding_quarterly",
                    "shareholding_yearly",
                    "document_links",
                    "peers",
                )
                for row in standalone_payload["normalized"][block]
            )
        )
        self.assertEqual(
            standalone_payload["normalized"]["quote"]["reporting_scope"],
            "standalone",
        )
        for block_name, block in standalone_payload["normalized"].items():
            rows = block if isinstance(block, list) else [block]
            for row in rows:
                self.assertEqual(
                    row["first_observed_at_utc"],
                    "2026-08-04T10:03:00+00:00",
                    block_name,
                )
                if block_name != "quote":
                    self.assertEqual(
                        row["available_at_utc"],
                        "2026-08-04T10:03:00+00:00",
                        block_name,
                    )
                    self.assertEqual(row["availability_basis"], "first_observed")
        self.assertEqual(len(standalone_payload["quality"]), 41)
        self.assertTrue(
            all(rule["status"] == "passed" for rule in standalone_payload["quality"])
        )
        self.assertEqual(
            [
                row["text_type"]
                for row in standalone_payload["normalized"]["company_text"]
                if row["text_type"] in {"pro", "con"}
            ],
            ["con", "con", "con"],
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            pointers = conn.execute(
                """SELECT reporting_scope, parse_id
                   FROM accepted_page_versions
                   ORDER BY reporting_scope"""
            ).fetchall()
            self.assertEqual(
                [(row["reporting_scope"], row["parse_id"]) for row in pointers],
                [
                    ("consolidated", consolidated.parse_id),
                    ("standalone", standalone.parse_id),
                ],
            )
            financial_scopes = conn.execute(
                """SELECT parse_id, reporting_scope, COUNT(*) AS n
                   FROM financial_observations
                   GROUP BY parse_id, reporting_scope
                   ORDER BY parse_id"""
            ).fetchall()
            self.assertEqual(
                [(row["parse_id"], row["reporting_scope"], row["n"])
                 for row in financial_scopes],
                [
                    (consolidated.parse_id, "consolidated", 576),
                    (standalone.parse_id, "standalone", 576),
                ],
            )
            source_identifier = conn.execute(
                """SELECT COUNT(*) AS n, source_company_id, canonical_url
                   FROM source_identifiers"""
            ).fetchone()
            self.assertEqual(source_identifier["n"], 1)
            self.assertEqual(source_identifier["source_company_id"], "1273603")
            self.assertEqual(source_identifier["canonical_url"], URL)

    def test_reused_parse_cannot_be_accepted_under_wrong_scope(self) -> None:
        consolidated = self.ingest(FIRST_FETCH)
        with self.assertRaisesRegex(ValueError, "acceptance rules"):
            prepare_database(self.db_path)
            ingest_screener_response(
                "LAURUSLABS",
                "standalone",
                fixture_response(),
                fixture_peer_response(),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc="2026-08-04T10:03:00+00:00",
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            pointers = conn.execute(
                """SELECT reporting_scope, parse_id
                   FROM accepted_page_versions"""
            ).fetchall()
            self.assertEqual(
                [(row["reporting_scope"], row["parse_id"]) for row in pointers],
                [("consolidated", consolidated.parse_id)],
            )
            rejection = conn.execute(
                """SELECT reporting_scope, action, reason_json
                   FROM acceptance_events
                   WHERE action='rejected'"""
            ).fetchone()
            self.assertEqual(rejection["reporting_scope"], "standalone")
            reason = json.loads(rejection["reason_json"])
            self.assertEqual(reason["requested_reporting_scope"], "standalone")
            self.assertEqual(reason["actual_reporting_scope"], "consolidated")

    def test_end_to_end_acceptance_and_export(self) -> None:
        result = self.ingest(FIRST_FETCH)
        self.assertFalse(result.reused_parse)
        self.assertTrue(result.export_path.exists())
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["provenance"]["accepted_version"]["parse_id"], result.parse_id)
        self.assertEqual(
            payload["provenance"]["acceptance_history"][0]["action"], "accepted"
        )
        self.assertEqual(len(payload["normalized"]["metrics"]), 9)
        self.assertEqual(len(payload["normalized"]["company_profile_links"]), 4)
        self.assertEqual(len(payload["normalized"]["company_text"]), 9)
        self.assertEqual(len(payload["normalized"]["quarterly_results"]), 156)
        self.assertEqual(len(payload["normalized"]["profit_loss"]), 156)
        self.assertEqual(len(payload["normalized"]["balance_sheet"]), 120)
        self.assertEqual(len(payload["normalized"]["cash_flow"]), 72)
        self.assertEqual(len(payload["normalized"]["annual_ratios"]), 72)
        self.assertEqual(len(payload["normalized"]["growth"]), 16)
        self.assertEqual(len(payload["normalized"]["shareholding_quarterly"]), 60)
        self.assertEqual(len(payload["normalized"]["shareholding_yearly"]), 55)
        self.assertEqual(len(payload["normalized"]["document_links"]), 168)
        self.assertEqual(len(payload["normalized"]["peers"]), 81)
        quality = {rule["rule_code"]: rule["status"] for rule in payload["quality"]}
        self.assertEqual(quality["profit_loss_table_present"], "passed")
        self.assertEqual(quality["pnl_annual_ttm_no_collision"], "passed")
        self.assertEqual(quality["balance_sheet_table_present"], "passed")
        self.assertEqual(quality["balance_assets_equal_liabilities"], "passed")
        self.assertEqual(quality["cash_flow_table_present"], "passed")
        self.assertEqual(quality["annual_ratios_table_present"], "passed")
        self.assertEqual(quality["annual_ratios_numeric_cells"], "passed")
        self.assertEqual(quality["annual_ratios_separate_from_panel"], "passed")
        self.assertEqual(quality["growth_blocks_present"], "passed")
        self.assertEqual(quality["growth_numeric_cells"], "passed")
        self.assertEqual(quality["growth_unavailable_not_zero"], "passed")
        self.assertEqual(quality["quarterly_shareholding_table_present"], "passed")
        self.assertEqual(quality["quarterly_shareholding_numeric_cells"], "passed")
        self.assertEqual(quality["shareholding_no_cross_regime"], "passed")
        self.assertEqual(quality["shareholding_classification_warning_present"], "passed")
        self.assertEqual(quality["quarterly_shareholding_totals_100"], "passed")
        self.assertEqual(quality["yearly_shareholding_table_present"], "passed")
        self.assertEqual(quality["yearly_shareholding_numeric_cells"], "passed")
        self.assertEqual(quality["yearly_shareholding_no_cross_regime"], "passed")
        self.assertEqual(quality["yearly_shareholding_totals_100"], "passed")
        self.assertEqual(quality["document_links_present"], "passed")
        self.assertEqual(quality["document_link_source_fields_match"], "passed")
        self.assertEqual(quality["document_link_positions_contiguous"], "passed")
        self.assertEqual(quality["document_link_availability_consistent"], "passed")
        self.assertEqual(quality["document_link_required_families_present"], "passed")
        self.assertEqual(quality["known_document_link_types"], "passed")
        self.assertEqual(quality["company_about_text_present"], "passed")
        self.assertEqual(quality["company_profile_links_present"], "passed")
        self.assertEqual(quality["pros_cons_text_present"], "passed")
        self.assertEqual(quality["pros_cons_source_generated"], "passed")
        self.assertEqual(quality["peer_table_supplement_present"], "passed")
        self.assertEqual(quality["peer_table_not_placeholder"], "passed")
        self.assertEqual(quality["peer_table_numeric_cells"], "passed")
        self.assertEqual(quality["peer_table_subject_match"], "passed")
        self.assertTrue(all(rule["status"] == "passed" for rule in payload["quality"]))

        document_rows = payload["normalized"]["document_links"]
        self.assertEqual([row["position"] for row in document_rows], list(range(168)))
        self.assertTrue(all(row["available_at_utc"] == FIRST_FETCH for row in document_rows))
        self.assertTrue(all(row["availability_basis"] == "first_observed" for row in document_rows))
        self.assertTrue(all(row["source_published_at_utc"] is None for row in document_rows))
        self.assertEqual(
            sum(row["availability_status"] == "unavailable" for row in document_rows),
            8,
        )
        documents_section = next(
            section
            for section in payload["source_faithful"]["sections"]
            if section["section_code"] == "document_links"
        )
        self.assertEqual(documents_section["position"], 13)
        self.assertEqual(len(documents_section["fields"]), 168)
        self.assertEqual(documents_section["cells"], [])

        peer_section = next(
            section
            for section in payload["source_faithful"]["sections"]
            if section["section_code"] == "peer_table"
        )
        self.assertEqual(peer_section["position"], 4)
        self.assertEqual(peer_section["parse_id"], result.peer_parse_id)
        self.assertEqual(peer_section["fields"], [])
        self.assertEqual(len(peer_section["cells"]), 110)
        self.assertFalse(
            any(
                "loading peers table" in cell["normalized_display_text"].lower()
                for cell in peer_section["cells"]
            )
        )
        supplemental = payload["provenance"]["supplemental_resources"]
        self.assertEqual(len(supplemental), 1)
        self.assertEqual(supplemental[0]["component_code"], "peer_table")
        self.assertEqual(supplemental[0]["supplemental_parse_id"], result.peer_parse_id)
        self.assertEqual(supplemental[0]["fetch"]["resource_kind"], "peer_table")
        self.assertEqual(supplemental[0]["validation_status"], "passed")

        peer_rows = payload["normalized"]["peers"]
        subject = [row for row in peer_rows if row["is_subject_company"] == 1]
        self.assertEqual(len(subject), 9)
        self.assertTrue(all(row["available_at_utc"] == FIRST_FETCH for row in peer_rows))
        self.assertTrue(all(row["availability_basis"] == "first_observed" for row in peer_rows))
        self.assertTrue(all(row["source_published_at_utc"] is None for row in peer_rows))

        profile_section = next(
            section
            for section in payload["source_faithful"]["sections"]
            if section["section_code"] == "company_profile"
        )
        self.assertEqual(profile_section["position"], 1)
        self.assertEqual(len(profile_section["fields"]), 6)
        self.assertEqual(profile_section["fields"][0]["visibility_state"], "metadata")
        self.assertEqual(profile_section["fields"][-1]["field_role"], "about_source_link")
        self.assertTrue(profile_section["fields"][-1]["link_url"].endswith(".pdf#page=28"))

        analysis_section = next(
            section
            for section in payload["source_faithful"]["sections"]
            if section["section_code"] == "pros_cons"
        )
        self.assertEqual(analysis_section["position"], 3)
        self.assertEqual(len(analysis_section["fields"]), 9)
        self.assertEqual(analysis_section["fields"][0]["normalized_display_text"], "Pros")
        self.assertEqual(analysis_section["fields"][2]["normalized_display_text"], "Cons")
        self.assertEqual(analysis_section["fields"][-1]["visibility_state"], "conditional")

        text_rows = payload["normalized"]["company_text"]
        commentary = [row for row in text_rows if row["text_type"] in {"pro", "con"}]
        self.assertEqual(len(commentary), 5)
        self.assertTrue(all(row["is_source_generated"] == 1 for row in commentary))
        self.assertTrue(
            all(row["content_origin"] == "source_generated_commentary" for row in commentary)
        )
        self.assertTrue(all(row["available_at_utc"] == FIRST_FETCH for row in text_rows))
        self.assertTrue(all(row["source_published_at_utc"] is None for row in text_rows))

        # Shareholding percentages, counts and regime tags survive the DB
        # round-trip, and the source classification warning is stored, not just
        # logged.
        shp_rows = payload["normalized"]["shareholding_quarterly"]
        self.assertEqual(sorted({r["classification_regime"] for r in shp_rows}), ["xbrl_sep_2022_plus"])
        self.assertEqual({r["classification_regime"] for r in shp_rows} & {"cross_regime"}, set())
        promoters = [r for r in shp_rows if r["holder_code"] == "promoters"]
        self.assertTrue(promoters and all(r["unit_code"] == "PERCENT_POINT" for r in promoters))
        self.assertEqual(promoters[0]["value_decimal"], "27.20")
        self.assertEqual(promoters[0]["available_at_utc"], FIRST_FETCH)
        holders = [r for r in shp_rows if r["holder_code"] == "no_of_shareholders"]
        self.assertTrue(holders and all(r["unit_code"] == "COUNT" for r in holders))
        self.assertEqual(holders[0]["value_decimal"], "402640")
        warning_section = next(
            s for s in payload["source_faithful"]["sections"]
            if s["section_code"] == "shareholding_quarterly"
        )
        warning_fields = [f for f in warning_section["fields"] if f["field_role"] == "classification_warning"]
        self.assertEqual(len(warning_fields), 1)
        self.assertIn("classifications might have changed", warning_fields[0]["normalized_display_text"])

        # The balance-sheet export preserves the source drill-down markers.
        balance_rows = payload["normalized"]["balance_sheet"]
        borrowings = [r for r in balance_rows if r["line_item_code"] == "borrowings"]
        self.assertTrue(borrowings)
        self.assertTrue(all(r["is_expandable"] == 1 for r in borrowings))
        self.assertTrue(all(r["schedule_key"] == "Borrowings" for r in borrowings))

        # Cash-flow signs, signed zero and the percent unit survive the DB round-trip.
        cash_rows = payload["normalized"]["cash_flow"]
        signed_zero = [r for r in cash_rows if r["display_value"] == "-0"]
        self.assertEqual(len(signed_zero), 1)
        self.assertEqual(signed_zero[0]["value_decimal"], "-0")
        self.assertIsNone(signed_zero[0]["missing_kind"])
        cfo = [r for r in cash_rows if r["line_item_code"] == "cfo_op_pct"]
        self.assertTrue(cfo and all(r["unit_code"] == "PERCENT_POINT" for r in cfo))

        growth = payload["normalized"]["growth"]
        cagr_10y = next(
            g
            for g in growth
            if g["metric_code"] == "stock_price_cagr" and g["period_code"] == "10y"
        )
        self.assertIsNone(cagr_10y["value_decimal"])
        self.assertEqual(cagr_10y["missing_kind"], "blank")
        self.assertEqual(cagr_10y["availability_basis"], "first_observed")
        self.assertEqual(cagr_10y["available_at_utc"], FIRST_FETCH)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            accepted = conn.execute("SELECT * FROM latest_accepted_pages").fetchone()
            self.assertEqual(accepted["parse_id"], result.parse_id)
            pnl_types = conn.execute(
                """SELECT period_type, COUNT(*) AS n FROM financial_observations
                   WHERE section_code='pnl' GROUP BY period_type ORDER BY period_type"""
            ).fetchall()
            self.assertEqual(
                [(row["period_type"], row["n"]) for row in pnl_types],
                [("fiscal_year", 144), ("ttm", 12)],
            )
            no_collision = conn.execute(
                """SELECT COUNT(*) AS n FROM (
                       SELECT column_position FROM financial_observations
                       WHERE parse_id=? AND section_code='pnl'
                       GROUP BY column_position HAVING COUNT(DISTINCT period_type) > 1
                   )""",
                (result.parse_id,),
            ).fetchone()["n"]
            self.assertEqual(no_collision, 0)
            # Annual ratios live in financial_observations under their own
            # section; the top-panel ROCE stays in company_metric_observations.
            ratios_units = conn.execute(
                """SELECT unit_code, COUNT(*) AS n FROM financial_observations
                   WHERE parse_id=? AND section_code='ratios'
                   GROUP BY unit_code ORDER BY unit_code""",
                (result.parse_id,),
            ).fetchall()
            self.assertEqual(
                [(row["unit_code"], row["n"]) for row in ratios_units],
                [("DAYS", 60), ("PERCENT_POINT", 12)],
            )
            panel_roce = conn.execute(
                """SELECT COUNT(*) AS n FROM company_metric_observations
                   WHERE parse_id=? AND metric_code='roce'""",
                (result.parse_id,),
            ).fetchone()["n"]
            self.assertEqual(panel_roce, 1)
            ratio_roce = conn.execute(
                """SELECT COUNT(*) AS n FROM financial_observations
                   WHERE parse_id=? AND section_code='ratios'
                     AND line_item_code='roce_pct'""",
                (result.parse_id,),
            ).fetchone()["n"]
            self.assertEqual(ratio_roce, 12)
            shp_regimes = conn.execute(
                """SELECT classification_regime, COUNT(*) AS n
                   FROM shareholding_observations
                   WHERE parse_id=? AND frequency='quarterly'
                   GROUP BY classification_regime ORDER BY classification_regime""",
                (result.parse_id,),
            ).fetchall()
            self.assertEqual(
                [(row["classification_regime"], row["n"]) for row in shp_regimes],
                [("xbrl_sep_2022_plus", 60)],
            )
            cross_regime = conn.execute(
                """SELECT COUNT(*) AS n FROM shareholding_observations
                   WHERE classification_regime='cross_regime'"""
            ).fetchone()["n"]
            self.assertEqual(cross_regime, 0)
            shp_units = conn.execute(
                """SELECT unit_code, COUNT(*) AS n FROM shareholding_observations
                   WHERE parse_id=? AND frequency='quarterly'
                   GROUP BY unit_code ORDER BY unit_code""",
                (result.parse_id,),
            ).fetchall()
            self.assertEqual(
                [(row["unit_code"], row["n"]) for row in shp_units],
                [("COUNT", 12), ("PERCENT_POINT", 48)],
            )
            # Quarterly and yearly rows coexist in one table, distinguished only
            # by `frequency`: neither overwrites the other.
            shp_by_frequency = conn.execute(
                """SELECT frequency, COUNT(*) AS n FROM shareholding_observations
                   WHERE parse_id=? GROUP BY frequency ORDER BY frequency""",
                (result.parse_id,),
            ).fetchall()
            self.assertEqual(
                [(row["frequency"], row["n"]) for row in shp_by_frequency],
                [("quarterly", 60), ("yearly", 55)],
            )
            # The yearly table straddles the Sep-2022 XBRL boundary; each period is
            # tagged from its own end date and no row is ever `cross_regime`.
            yearly_regimes = conn.execute(
                """SELECT classification_regime, COUNT(*) AS n
                   FROM shareholding_observations
                   WHERE parse_id=? AND frequency='yearly'
                   GROUP BY classification_regime ORDER BY classification_regime""",
                (result.parse_id,),
            ).fetchall()
            self.assertEqual(
                [(row["classification_regime"], row["n"]) for row in yearly_regimes],
                [("pre_sep_2022", 30), ("xbrl_sep_2022_plus", 25)],
            )
            source_identifier = conn.execute(
                "SELECT source_company_id, source_symbol, mapping_status FROM source_identifiers"
            ).fetchone()
            self.assertEqual(
                tuple(source_identifier), ("1273603", "LAURUSLABS", "verified")
            )
            availability = conn.execute(
                """SELECT DISTINCT first_observed_at_utc, available_at_utc,
                                  availability_basis
                   FROM financial_observations"""
            ).fetchall()
            self.assertEqual(len(availability), 1)
            self.assertEqual(availability[0]["first_observed_at_utc"], FIRST_FETCH)
            self.assertEqual(availability[0]["available_at_utc"], FIRST_FETCH)
            self.assertEqual(availability[0]["availability_basis"], "first_observed")
            metric_availability = conn.execute(
                """SELECT DISTINCT first_observed_at_utc, available_at_utc,
                                  availability_basis
                   FROM company_metric_observations"""
            ).fetchone()
            self.assertEqual(
                tuple(metric_availability),
                (FIRST_FETCH, FIRST_FETCH, "first_observed"),
            )
            quote = conn.execute(
                """SELECT reporting_scope, price_display_value,
                          quote_timestamp_raw, quote_timestamp_utc,
                          first_observed_at_utc
                   FROM quote_observations"""
            ).fetchone()
            self.assertEqual(quote["reporting_scope"], "consolidated")
            self.assertEqual(quote["price_display_value"], "₹ 1,836")
            self.assertEqual(quote["quote_timestamp_raw"], "04 Aug 3:32 p.m.")
            self.assertEqual(quote["quote_timestamp_utc"], FIRST_FETCH)
            self.assertEqual(quote["first_observed_at_utc"], FIRST_FETCH)
            fetch_periods = conn.execute(
                """SELECT requested_source_period, resolved_source_period
                   FROM fetch_observations"""
            ).fetchone()
            self.assertEqual(
                tuple(fetch_periods), ("not_applicable", "not_applicable")
            )
            event = conn.execute(
                "SELECT action, parse_id FROM acceptance_events"
            ).fetchone()
            self.assertEqual(tuple(event), ("accepted", result.parse_id))

    def test_same_bytes_reuse_main_and_supplemental_blobs_and_parses(self) -> None:
        first = self.ingest(FIRST_FETCH)
        second = self.ingest("2026-08-04T10:10:00+00:00")
        self.assertEqual(first.blob_id, second.blob_id)
        self.assertEqual(first.parse_id, second.parse_id)
        self.assertEqual(first.peer_blob_id, second.peer_blob_id)
        self.assertEqual(first.peer_parse_id, second.peer_parse_id)
        self.assertTrue(second.reused_parse)

        with closing(sqlite3.connect(self.db_path)) as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("fetch_observations", "response_blobs", "parse_artifacts")
            }
            self.assertEqual(
                counts,
                {"fetch_observations": 4, "response_blobs": 2, "parse_artifacts": 2},
            )
            observed = conn.execute(
                "SELECT DISTINCT first_observed_at_utc FROM response_blobs"
            ).fetchall()
            self.assertEqual(observed, [(FIRST_FETCH,)])
            resources = conn.execute(
                """SELECT resource_kind, COUNT(*) FROM fetch_observations
                   GROUP BY resource_kind ORDER BY resource_kind"""
            ).fetchall()
            self.assertEqual(resources, [("company_page", 2), ("peer_table", 2)])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM acceptance_events").fetchone()[0], 1
            )

    def test_saved_blobs_reprocess_without_new_fetch_observations(self) -> None:
        first = self.ingest(FIRST_FETCH)
        prepare_database(self.db_path)
        replay = reprocess_screener_blobs(
            "LAURUSLABS",
            "consolidated",
            first.blob_id,
            first.peer_blob_id,
            db_path=self.db_path,
            export_root=self.exports,
        )
        self.assertTrue(replay.reused_parse)
        self.assertEqual(replay.parse_id, first.parse_id)
        self.assertEqual(replay.peer_parse_id, first.peer_parse_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM fetch_observations").fetchone()[0],
                2,
            )
            config, status = conn.execute(
                "SELECT config_json, status FROM scrape_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            self.assertIn('"mode": "reprocess_saved_blobs"', config)
            self.assertEqual(status, "ok")

    def test_new_accepted_parse_supersedes_without_losing_history(self) -> None:
        first = self.ingest(FIRST_FETCH)
        changed = FIXTURE.read_bytes().replace(b"3:32 p.m.", b"3:33 p.m.", 1)
        prepare_database(self.db_path)
        second = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(changed),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc="2026-08-04T10:03:00+00:00",
        )
        self.assertNotEqual(first.parse_id, second.parse_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            pointer = conn.execute(
                "SELECT parse_id FROM accepted_page_versions"
            ).fetchone()
            self.assertEqual(pointer["parse_id"], second.parse_id)
            events = conn.execute(
                "SELECT action, parse_id FROM acceptance_events ORDER BY acceptance_id"
            ).fetchall()
            self.assertEqual(
                [(row["action"], row["parse_id"]) for row in events],
                [
                    ("accepted", first.parse_id),
                    ("superseded", first.parse_id),
                    ("accepted", second.parse_id),
                ],
            )

    def test_balance_imbalance_is_a_non_blocking_warning(self) -> None:
        # Corrupt Total Liabilities Mar 2026 (10,511 -> 99,999); it is the first
        # of the two balance-sheet occurrences, so Total Assets stays 10,511 and
        # that period no longer balances.
        corrupted = FIXTURE.read_bytes().replace(b"10,511", b"99,999", 1)
        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(corrupted),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        quality = {rule["rule_code"]: rule for rule in payload["quality"]}
        self.assertEqual(quality["balance_assets_equal_liabilities"]["status"], "failed")
        self.assertEqual(quality["balance_assets_equal_liabilities"]["severity"], "warning")
        # The warning does not block promotion: the parse is still accepted.
        self.assertEqual(
            payload["provenance"]["accepted_version"]["parse_id"], result.parse_id
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            status = conn.execute(
                "SELECT validation_status FROM parse_artifacts WHERE parse_id=?",
                (result.parse_id,),
            ).fetchone()["validation_status"]
            self.assertEqual(status, "passed_with_warnings")

    def test_shareholding_total_gap_is_a_non_blocking_warning(self) -> None:
        # Corrupt FIIs Sep 2023 (24.03% -> 54.03%); that period's categories no
        # longer total ~100, so the totals rule warns without blocking.
        corrupted = FIXTURE.read_bytes().replace(b"24.03%", b"54.03%", 1)
        prepare_database(self.db_path)
        result = ingest_screener_response(
            "LAURUSLABS",
            "consolidated",
            fixture_response(corrupted),
            fixture_peer_response(),
            db_path=self.db_path,
            snapshot_root=self.snapshots,
            export_root=self.exports,
            fetched_at_utc=FIRST_FETCH,
        )
        payload = json.loads(result.export_path.read_text(encoding="utf-8"))
        quality = {rule["rule_code"]: rule for rule in payload["quality"]}
        rule = quality["quarterly_shareholding_totals_100"]
        self.assertEqual(rule["status"], "failed")
        self.assertEqual(rule["severity"], "warning")
        self.assertIn("Sep 2023", rule["actual_value"])
        # The warning does not block promotion; the raw source value is not "fixed".
        self.assertEqual(
            payload["provenance"]["accepted_version"]["parse_id"], result.parse_id
        )
        fiis = [
            r
            for r in payload["normalized"]["shareholding_quarterly"]
            if r["holder_code"] == "foreign_institutions" and r["period_label_raw"] == "Sep 2023"
        ]
        self.assertEqual(fiis[0]["display_value"], "54.03%")
        self.assertEqual(fiis[0]["value_decimal"], "54.03")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            status = conn.execute(
                "SELECT validation_status FROM parse_artifacts WHERE parse_id=?",
                (result.parse_id,),
            ).fetchone()["validation_status"]
            self.assertEqual(status, "passed_with_warnings")

    def test_identity_mismatch_is_not_accepted(self) -> None:
        with self.assertRaises(ValueError):
            prepare_database(self.db_path)
            ingest_screener_response(
                "RBA",
                "consolidated",
                fixture_response(),
                fixture_peer_response(),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc=FIRST_FETCH,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0], 0
            )
            rejected = conn.execute(
                "SELECT action FROM acceptance_events"
            ).fetchone()
            self.assertEqual(rejected[0], "rejected")
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM scrape_runs ORDER BY run_id DESC LIMIT 1"
                ).fetchone()[0],
                "error",
            )

    def test_uncovered_manifest_component_is_not_accepted(self) -> None:
        changed = FIXTURE.read_bytes().replace(
            b"Company.showSchedule('Sales', 'quarters'",
            b"Company.showSchedule('Uncatalogued Revenue', 'quarters'",
            1,
        )
        with self.assertRaisesRegex(ValueError, "automatic acceptance rules"):
            prepare_database(self.db_path)
            ingest_screener_response(
                "LAURUSLABS",
                "consolidated",
                fixture_response(changed),
                fixture_peer_response(),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc=FIRST_FETCH,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            severity, status, actual = conn.execute(
                """SELECT severity, status, actual_value FROM validation_results
                   WHERE rule_code='screener_manifest_coverage'"""
            ).fetchone()
            self.assertEqual((severity, status), ("error", "failed"))
            self.assertIn("Uncatalogued Revenue", actual)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )

    def test_peer_subject_mismatch_is_not_accepted(self) -> None:
        changed = PEER_FIXTURE.read_bytes().replace(
            b'data-row-company-id="1273603"',
            b'data-row-company-id="9999999"',
            1,
        )
        with self.assertRaises(ValueError):
            prepare_database(self.db_path)
            ingest_screener_response(
                "LAURUSLABS",
                "consolidated",
                fixture_response(),
                fixture_peer_response(changed),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc=FIRST_FETCH,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            peer_rule = conn.execute(
                """SELECT vr.status FROM validation_results vr
                   JOIN parse_artifacts pa ON pa.parse_id=vr.parse_id
                   WHERE pa.parser_name='screener-peer-table'
                     AND vr.rule_code='peer_table_subject_match'"""
            ).fetchone()
            self.assertEqual(peer_rule["status"], "failed")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )

    def test_unknown_concall_link_type_requires_manual_review(self) -> None:
        # Structural drift is retained as `concall_other`, reported by a focused
        # validation, and blocked from automatic acceptance.
        changed = FIXTURE.read_bytes().replace(b">REC</a>", b">Slides</a>", 1)
        with self.assertRaises(ValueError):
            prepare_database(self.db_path)
            ingest_screener_response(
                "LAURUSLABS",
                "consolidated",
                fixture_response(changed),
                fixture_peer_response(),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc=FIRST_FETCH,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rule = conn.execute(
                """SELECT severity, status, actual_value FROM validation_results
                   WHERE rule_code='known_document_link_types'"""
            ).fetchone()
            self.assertEqual(
                tuple(rule), ("manual_review", "failed", "concall_other")
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM accepted_page_versions").fetchone()[0],
                0,
            )

    def test_pros_cons_without_generation_disclosure_are_not_accepted(self) -> None:
        changed = FIXTURE.read_bytes().replace(
            b"The pros and cons are machine generated.",
            b"The pros and cons are editorial content.",
            1,
        )
        with self.assertRaises(ValueError):
            prepare_database(self.db_path)
            ingest_screener_response(
                "LAURUSLABS",
                "consolidated",
                fixture_response(changed),
                fixture_peer_response(),
                db_path=self.db_path,
                snapshot_root=self.snapshots,
                export_root=self.exports,
                fetched_at_utc=FIRST_FETCH,
            )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rule = conn.execute(
                """SELECT severity, status, actual_value FROM validation_results
                   WHERE rule_code='pros_cons_source_generated'"""
            ).fetchone()
            self.assertEqual(tuple(rule), ("error", "failed", "5"))
            preserved = conn.execute(
                """SELECT display_text, is_source_generated
                   FROM company_text_observations
                   WHERE text_type='pro'"""
            ).fetchone()
            self.assertIn("19.0%", preserved["display_text"])
            self.assertEqual(preserved["is_source_generated"], 0)


if __name__ == "__main__":
    unittest.main()
