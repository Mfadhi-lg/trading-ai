"""Offline tests for the exchange universe and result-discovery layer."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scrapper.core.http import validate_exchange_dataset_url
from scrapper.db.database import connect, init_db
from scrapper.db.exchange_repo import resolve_screener_path
from scrapper.db.screener_discovery_repo import claim_due_companies
from scrapper.exports.exchange_universe_coverage import build_exchange_universe_coverage
from scrapper.exports.screener_discovery_coverage import build_screener_discovery_coverage
from scrapper.pipeline.exchange_universe import (
    discover_exchange_results,
    refresh_exchange_universe,
)
from scrapper.pipeline.screener_discovery import _failure_policy
from scrapper.sources.exchange.corporate_changes import parse_corporate_changes
from scrapper.sources.exchange.result_filings import parse_result_filings
from scrapper.sources.exchange.security_master import parse_security_master
from scrapper.sources.exchange.tabular import SourceRowError, normalize_isin


ALPHA_ISIN = "INE002A01018"
BETA_ISIN = "INE467B01029"
GAMMA_ISIN = "INE040A01034"
DELTA_ISIN = "INE009A01021"


def nse_master(rows: tuple[str, ...]) -> bytes:
    header = (
        "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE,"
        " MARKET LOT, ISIN NUMBER, FACE VALUE"
    )
    return ("\n".join((header, *rows)) + "\n").encode()


NSE_DAY_ONE = nse_master(
    (
        f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10",
        f"BETA,Beta Services Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1",
        "BROKEN,Broken Row Limited,EQ,01-JAN-2020,10,1,INE002A01019,10",
    )
)
NSE_DAY_TWO = nse_master(
    (
        f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10",
        f"BETANEW,Beta Global Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1",
        f"DELTA,Delta Power Limited,EQ,01-JUN-2026,10,1,{DELTA_ISIN},10",
    )
)
# The universe used by the discovery tests: Beta has already been renamed, and
# Delta has not yet entered the NSE master.
NSE_CURRENT = nse_master(
    (
        f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10",
        f"BETANEW,Beta Global Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1",
    )
)
BSE_MASTER = (
    "Security Code,Issuer Name,Security Id,Security Name,Status,Group,Face Value,"
    "ISIN No,Industry,Instrument\n"
    f"500001,Alpha Industries Limited,ALPHAIND,ALPHA IND,Active,A,10,{ALPHA_ISIN},Capital Goods,Equity\n"
    f"500002,Gamma Textiles Limited,GAMMATEX,GAMMA TEX,Active,B,5,{GAMMA_ISIN},Textiles,Equity\n"
    "500003,Old Mills Limited,OLDMILL,OLD MILL,Delisted,Z,10,,Textiles,Equity\n"
    f"543001,SME Foods Limited,SMEFOOD,SME FOOD,Active,M,10,{DELTA_ISIN},Food,Equity\n"
).encode()
SYMBOL_CHANGE = (
    f"Beta Global Limited,BETA,BETANEW,15-JUL-2026\n"
    f"Unknown Corp Limited,OLDUNK,NEWUNK,16-JUL-2026\n"
).encode()


def nse_results(rows: tuple[str, ...]) -> bytes:
    header = (
        "SYMBOL,COMPANY NAME,INDUSTRY,RELATING TO,PERIOD ENDED,AUDITED,CUMULATIVE,"
        "CONSOLIDATED,BROADCAST DATE/TIME"
    )
    return ("\n".join((header, *rows)) + "\n").encode()


NSE_RESULTS = nse_results(
    (
        "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-JUN-2026,"
        "Un-Audited,Non-cumulative,Consolidated,05-Aug-2026 18:30:00",
        "BETANEW,Beta Global Limited,Services,Quarterly,30-JUN-2026,"
        "Un-Audited,Non-cumulative,Non-Consolidated,04-Aug-2026 11:15:00",
        "NOTLISTED,Never Listed Limited,Misc,Quarterly,30-JUN-2026,"
        "Un-Audited,Non-cumulative,Consolidated,03-Aug-2026 09:00:00",
    )
)
BSE_RESULTS = (
    "Security Code,Security Name,Period Ended,Result Type,Submission Date\n"
    "500001,ALPHA IND,30-JUN-2026,Consolidated,05-Aug-2026 18:45:00\n"
    "500002,GAMMA TEX,30-JUN-2026,Consolidated,05-Aug-2026 19:05:00\n"
).encode()


class ParserTests(unittest.TestCase):
    def test_nse_master_binds_headers_and_retains_rejected_rows(self) -> None:
        parsed = parse_security_master(NSE_DAY_ONE, "nse_equity_master")
        self.assertEqual([item.exchange_key for item in parsed.records], ["ALPHA", "BETA"])
        self.assertEqual(parsed.records[0].isin, ALPHA_ISIN)
        self.assertEqual(parsed.records[0].listing_date, "1995-01-20")
        self.assertEqual(parsed.records[0].series_or_group, "EQ")
        self.assertEqual(parsed.rejected[0].reason_code, "invalid_isin")
        self.assertEqual(parsed.rejected[0].cells[0], "BROKEN")

    def test_column_order_change_does_not_change_identity(self) -> None:
        reordered = (
            "NAME OF COMPANY,SYMBOL,ISIN NUMBER,SERIES\n"
            f"Alpha Industries Limited,ALPHA,{ALPHA_ISIN},EQ\n"
        ).encode()
        parsed = parse_security_master(reordered, "nse_equity_master")
        self.assertEqual(parsed.records[0].exchange_key, "ALPHA")
        self.assertEqual(parsed.records[0].isin, ALPHA_ISIN)

    def test_missing_required_column_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_security_master(b"SYMBOL,SERIES\nALPHA,EQ\n", "nse_equity_master")

    def test_bse_master_segments_status_and_optional_isin(self) -> None:
        parsed = parse_security_master(BSE_MASTER, "bse_security_master")
        by_key = {item.exchange_key: item for item in parsed.records}
        self.assertEqual(by_key["543001"].segment, "sme")
        self.assertEqual(by_key["500002"].segment, "equity")
        self.assertEqual(by_key["500003"].status, "delisted")
        self.assertIsNone(by_key["500003"].isin)
        self.assertEqual(by_key["500001"].isin, ALPHA_ISIN)

    def test_isin_check_digit_is_enforced(self) -> None:
        self.assertEqual(normalize_isin(" ine002a01018 "), ALPHA_ISIN)
        with self.assertRaises(SourceRowError):
            normalize_isin("INE002A01019")

    def test_headerless_symbol_change_contract(self) -> None:
        parsed = parse_corporate_changes(SYMBOL_CHANGE, "nse_symbol_change")
        self.assertEqual(parsed.changes[0].old_value, "BETA")
        self.assertEqual(parsed.changes[0].new_value, "BETANEW")
        self.assertEqual(parsed.changes[0].effective_date, "2026-07-15")
        self.assertEqual(parsed.changes[0].key_hints, ("BETANEW", "BETA"))

    def test_result_filings_normalize_scope_period_and_broadcast(self) -> None:
        parsed = parse_result_filings(NSE_RESULTS, "nse_financial_results")
        first = parsed.filings[0]
        self.assertEqual(first.result_period, "2026-06")
        self.assertEqual(first.period_end_date, "2026-06-30")
        self.assertEqual(first.reporting_scope, "consolidated")
        self.assertEqual(parsed.filings[1].reporting_scope, "standalone")
        # 18:30 IST is exactly 13:00 UTC; no float or locale drift is permitted.
        self.assertEqual(first.broadcast_at_utc, "2026-08-05T13:00:00+00:00")
        self.assertEqual(
            first.filing_key, "ALPHA|2026-06-30|consolidated|2026-08-05T13:00:00+00:00"
        )
        self.assertEqual(parsed.max_broadcast_at_utc, "2026-08-05T13:00:00+00:00")

    def test_unknown_scope_row_is_retained_not_dropped(self) -> None:
        parsed = parse_result_filings(
            nse_results(
                (
                    "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-JUN-2026,"
                    "Un-Audited,Non-cumulative,Combined,05-Aug-2026 18:30:00",
                )
            ),
            "nse_financial_results",
        )
        self.assertEqual(parsed.filings, ())
        self.assertEqual(parsed.rejected[0].reason_code, "unknown_scope")

    def test_underscored_headers_bind_to_the_same_declared_columns(self) -> None:
        # The NSE SME master publishes the equity master's columns underscored
        # and dates with a two-digit year.
        sme = (
            "SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAID_UP_VALUE,"
            "ISIN_NUMBER,FACE_VALUE,\n"
            f"ALPHA,Alpha Industries Limited,SM,20-Jan-95,10,{ALPHA_ISIN},10,\n"
        ).encode()
        parsed = parse_security_master(sme, "nse_sme_master")
        self.assertEqual(parsed.records[0].exchange_key, "ALPHA")
        self.assertEqual(parsed.records[0].isin, ALPHA_ISIN)
        self.assertEqual(parsed.records[0].listing_date, "1995-01-20")
        self.assertEqual(parsed.records[0].segment, "sme")

    def test_nse_name_change_binds_published_nch_headers(self) -> None:
        published = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "BETA,Beta Limited,Beta Industries Limited,15-Jul-2026\n"
        ).encode()
        parsed = parse_corporate_changes(published, "nse_name_change")
        self.assertEqual(parsed.changes[0].old_value, "Beta Limited")
        self.assertEqual(parsed.changes[0].new_value, "Beta Industries Limited")
        self.assertEqual(parsed.changes[0].effective_date, "2026-07-15")

    def test_json_result_feed_binds_the_same_fields_as_the_export(self) -> None:
        feed = json.dumps(
            [
                {
                    "symbol": "ALPHA",
                    "companyName": "Alpha Industries Limited",
                    "consolidated": "Consolidated",
                    "toDate": "30-Jun-2026",
                    "fromDate": "01-Apr-2026",
                    "broadCastDate": "05-Aug-2026 18:30:00",
                    "period": "Quarterly",
                    "relatingTo": "First Quarter",
                    "isin": ALPHA_ISIN,
                    "resultDescription": None,
                }
            ]
        ).encode()
        parsed = parse_result_filings(feed, "nse_financial_results")
        first = parsed.filings[0]
        self.assertEqual(first.exchange_key, "ALPHA")
        self.assertEqual(first.result_period, "2026-06")
        self.assertEqual(first.reporting_scope, "consolidated")
        self.assertEqual(first.broadcast_at_utc, "2026-08-05T13:00:00+00:00")
        self.assertEqual(first.relating_to_raw, "First Quarter")
        # A null JSON value is an empty cell, not the string "None".
        self.assertEqual(first.raw_row["resultDescription"], "")

    def test_integrated_filing_csv_binds_current_nse_columns(self) -> None:
        # NSE moved quarter-ended March 2025 and later results to the Integrated
        # Filing - Financials export. Its headings differ from the legacy feed,
        # but the same source-faithful filing model and queue are retained.
        feed = (
            'SYMBOL,COMPANY NAME,QUARTER END DATE,TYPE OF SUBMISSION,'
            'AUDITED / UNAUDITED,CONSOLIDATED / STANDALONE,DETAILS,XBRL,'
            'BROADCAST DATE/TIME\n'
            'ALPHA,Alpha Industries Limited,30-JUN-2026,Original,Un-Audited,'
            'Standalone,https://example.invalid/details,https://example.invalid/xbrl,'
            '05-Aug-2026 18:30:00\n'
        ).encode()
        parsed = parse_result_filings(feed, "nse_financial_results")
        first = parsed.filings[0]
        self.assertEqual(first.exchange_key, "ALPHA")
        self.assertEqual(first.company_name_raw, "Alpha Industries Limited")
        self.assertEqual(first.result_period, "2026-06")
        self.assertEqual(first.reporting_scope, "standalone")
        self.assertEqual(first.audited_raw, "Un-Audited")
        self.assertEqual(first.broadcast_at_utc, "2026-08-05T13:00:00+00:00")

    def test_integrated_revision_uses_its_revised_filing_timestamp(self) -> None:
        feed = (
            'SYMBOL,COMPANY NAME,QUARTER END DATE,AUDITED / UNAUDITED,'
            'CONSOLIDATED / STANDALONE,BROADCAST DATE/TIME,REVISED DATE/TIME\n'
            'ALPHA,Alpha Industries Limited,30-JUN-2026,Un-Audited,'
            'Consolidated,,05-Aug-2026 19:45:00\n'
        ).encode()
        parsed = parse_result_filings(feed, "nse_financial_results")
        first = parsed.filings[0]
        self.assertEqual(first.broadcast_raw, "05-Aug-2026 19:45:00")
        self.assertEqual(first.broadcast_at_utc, "2026-08-05T14:15:00+00:00")
        self.assertEqual(first.raw_row["BROADCAST DATE/TIME"], "")

    def test_json_security_master_binds_published_bse_keys(self) -> None:
        feed = json.dumps(
            [
                {
                    "SCRIP_CD": "500001",
                    "Scrip_Name": "Alpha Industries Ltd",
                    "Status": "Active",
                    "GROUP": "A",
                    "ISIN_NUMBER": ALPHA_ISIN,
                    "Segment": "Equity",
                    "scrip_id": "ALPHA",
                }
            ]
        ).encode()
        parsed = parse_security_master(feed, "bse_security_master")
        self.assertEqual(parsed.records[0].exchange_key, "500001")
        self.assertEqual(parsed.records[0].isin, ALPHA_ISIN)
        self.assertEqual(parsed.records[0].segment, "equity")

    def test_bse_integrated_feed_expands_scopes_and_filters_period(self) -> None:
        feed = json.dumps(
            {
                "Table": [
                    {
                        "Scrip_cd": 500001,
                        "company_name": "Alpha Industries Limited",
                        "quarter_code": "JQ2026-2027",
                        "audited": "Unaudited",
                        "Fld_CreateDate": "2026-08-05T18:30:00.125",
                        "XMLName": "standalone.xml",
                        "Consol_XMLName": "consolidated.xml",
                    },
                    {
                        "Scrip_cd": 500002,
                        "company_name": "Old Period Limited",
                        "quarter_code": "MQ2025-2026",
                        "audited": "Audited",
                        "Fld_CreateDate": "2026-05-30T10:00:00",
                        "XMLName": "standalone.xml",
                        "Consol_XMLName": None,
                    },
                ]
            }
        ).encode()
        parsed = parse_result_filings(
            feed, "bse_financial_results", result_period="2026-06"
        )
        self.assertEqual(parsed.excluded_row_count, 1)
        self.assertEqual(len(parsed.filings), 2)
        self.assertEqual(
            {item.reporting_scope for item in parsed.filings},
            {"standalone", "consolidated"},
        )
        self.assertTrue(all(item.exchange_key == "500001" for item in parsed.filings))
        self.assertTrue(all(item.result_period == "2026-06" for item in parsed.filings))
        self.assertEqual(
            parsed.filings[0].broadcast_at_utc, "2026-08-05T13:00:00+00:00"
        )

    def test_json_feed_missing_required_field_fails_closed(self) -> None:
        feed = json.dumps([{"companyName": "Alpha Industries Limited"}]).encode()
        with self.assertRaises(ValueError):
            parse_result_filings(feed, "nse_financial_results")

    def test_dataset_url_allowlist_fails_closed(self) -> None:
        validate_exchange_dataset_url(
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "NSE"
        )
        for url, exchange in (
            ("http://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "NSE"),
            ("https://example.com/content/equities/EQUITY_L.csv", "NSE"),
            ("https://www.nseindia.com/other/EQUITY_L.csv", "NSE"),
            ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "BSE"),
        ):
            with self.assertRaises(ValueError):
                validate_exchange_dataset_url(url, exchange)


class ExchangeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.db = root / "scraper.db"
        self.snapshots = root / "snapshots"
        self.files = root / "files"
        self.files.mkdir()
        self.clock = iter(
            f"2026-08-{day:02d}T00:00:0{index}+00:00"
            for day in range(1, 30)
            for index in range(9)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def now(self) -> str:
        return next(self.clock)

    def write(self, name: str, content: bytes) -> Path:
        path = self.files / name
        path.write_bytes(content)
        return path

    def refresh(self, dataset: str, content: bytes, effective_date: str, **kwargs):
        return refresh_exchange_universe(
            dataset=dataset,
            file=self.write(f"{dataset}-{effective_date}.csv", content),
            effective_date=effective_date,
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
            **kwargs,
        )

    def discover(self, dataset: str, content: bytes, effective_date: str, **kwargs):
        return discover_exchange_results(
            dataset=dataset,
            file=self.write(f"{dataset}-{effective_date}.csv", content),
            effective_date=effective_date,
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
            **kwargs,
        )

    def rows(self, sql: str, *parameters):
        conn = connect(self.db)
        try:
            init_db(conn)
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()

    def build_universe(self) -> None:
        self.refresh("nse_equity_master", NSE_CURRENT, "2026-08-01")
        self.refresh("bse_security_master", BSE_MASTER, "2026-08-01")


class RegistryTests(ExchangeCase):
    def test_master_creates_effective_dated_securities_and_isin_registry(self) -> None:
        payload = self.refresh("nse_equity_master", NSE_DAY_ONE, "2026-08-01")
        self.assertEqual(payload["registry"]["added"], 2)
        self.assertEqual(payload["rejected_row_count"], 1)
        rejected = self.rows("SELECT * FROM exchange_source_rejected_rows")
        self.assertEqual(rejected[0]["reason_code"], "invalid_isin")
        self.assertIn("BROKEN", json.loads(rejected[0]["raw_cells_json"]))

        self.refresh("bse_security_master", BSE_MASTER, "2026-08-01")
        companies = self.rows(
            "SELECT isin, canonical_name_raw FROM exchange_company_registry ORDER BY isin"
        )
        self.assertEqual(
            [item["isin"] for item in companies],
            sorted([ALPHA_ISIN, BETA_ISIN, GAMMA_ISIN, DELTA_ISIN]),
        )
        # The NSE master name wins over the abbreviated BSE security name.
        alpha = next(item for item in companies if item["isin"] == ALPHA_ISIN)
        self.assertEqual(alpha["canonical_name_raw"], "Alpha Industries Limited")
        links = self.rows(
            """SELECT security.exchange, security.exchange_key
               FROM exchange_company_links AS link
               JOIN exchange_securities AS security USING (exchange_security_id)
               JOIN exchange_company_registry AS registry USING (registry_company_id)
               WHERE registry.isin=? AND link.is_current=1
               ORDER BY security.exchange""",
            ALPHA_ISIN,
        )
        self.assertEqual(
            [(item["exchange"], item["exchange_key"]) for item in links],
            [("BSE", "500001"), ("NSE", "ALPHA")],
        )
        # A delisted BSE scrip without an ISIN is retained but never unioned.
        unlinked = self.rows(
            """SELECT exchange_key FROM exchange_securities
               WHERE current_isin IS NULL"""
        )
        self.assertEqual([item["exchange_key"] for item in unlinked], ["500003"])

    def test_same_name_different_isin_never_merges(self) -> None:
        self.refresh(
            "nse_equity_master",
            nse_master(
                (
                    f"ONEA,Twin Holdings Limited,EQ,01-JAN-2020,10,1,{ALPHA_ISIN},10",
                    f"ONEB,Twin Holdings Limited,EQ,01-JAN-2020,10,1,{BETA_ISIN},10",
                )
            ),
            "2026-08-01",
        )
        self.assertEqual(
            len(self.rows("SELECT * FROM exchange_company_registry")), 2
        )

    def test_second_snapshot_records_additions_removals_and_renames(self) -> None:
        self.refresh("nse_equity_master", NSE_DAY_ONE, "2026-08-01")
        payload = self.refresh("nse_equity_master", NSE_DAY_TWO, "2026-08-02")
        self.assertEqual(payload["registry"]["added"], 2)  # BETANEW and DELTA
        self.assertEqual(payload["registry"]["unchanged"], 1)  # ALPHA
        self.assertEqual(payload["registry"]["removed"], 1)  # BETA left the master
        beta = self.rows(
            "SELECT * FROM exchange_securities WHERE exchange_key='BETA'"
        )[0]
        self.assertEqual(beta["is_present"], 0)
        self.assertEqual(beta["current_status"], "absent_from_master")
        events = self.rows(
            """SELECT security.exchange_key, event.event_type, event.event_date
               FROM exchange_security_events AS event
               JOIN exchange_securities AS security USING (exchange_security_id)
               ORDER BY event.security_event_id"""
        )
        self.assertIn(("BETA", "removed", "2026-08-02"), [tuple(item.values()) for item in events])
        self.assertIn(("DELTA", "added", "2026-08-02"), [tuple(item.values()) for item in events])
        # The same ISIN now points at the new symbol without losing the old one.
        beta_links = self.rows(
            """SELECT security.exchange_key, link.is_current
               FROM exchange_company_links AS link
               JOIN exchange_securities AS security USING (exchange_security_id)
               JOIN exchange_company_registry AS registry USING (registry_company_id)
               WHERE registry.isin=? ORDER BY security.exchange_key""",
            BETA_ISIN,
        )
        self.assertEqual(
            [(item["exchange_key"], item["is_current"]) for item in beta_links],
            [("BETA", 1), ("BETANEW", 1)],
        )

    def test_attribute_change_closes_the_previous_version(self) -> None:
        self.refresh("nse_equity_master", NSE_DAY_ONE, "2026-08-01")
        renamed = nse_master(
            (
                f"ALPHA,Alpha Industries And Power Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10",
                f"BETA,Beta Services Limited,EQ,04-JUL-2004,1,1,{BETA_ISIN},1",
            )
        )
        self.refresh("nse_equity_master", renamed, "2026-08-03")
        versions = self.rows(
            """SELECT version.name_raw, version.valid_from_date, version.valid_to_date
               FROM exchange_security_versions AS version
               JOIN exchange_securities AS security USING (exchange_security_id)
               WHERE security.exchange_key='ALPHA'
               ORDER BY version.valid_from_date"""
        )
        self.assertEqual(
            [(item["valid_from_date"], item["valid_to_date"]) for item in versions],
            [("2026-08-01", "2026-08-03"), ("2026-08-03", None)],
        )
        self.assertEqual(versions[1]["name_raw"], "Alpha Industries And Power Limited")

    def test_older_snapshot_never_moves_state_backward(self) -> None:
        self.refresh("nse_equity_master", NSE_DAY_ONE, "2026-08-05")
        payload = self.refresh("nse_equity_master", NSE_DAY_TWO, "2026-08-02")
        self.assertEqual(payload["registry"]["stale_rows"], 1)  # ALPHA is older
        self.assertEqual(payload["registry"]["removed"], 0)
        alpha = self.rows("SELECT * FROM exchange_securities WHERE exchange_key='ALPHA'")[0]
        self.assertEqual(alpha["last_observed_date"], "2026-08-05")

    def test_symbol_change_file_is_applied_or_retained_unmatched(self) -> None:
        self.refresh("nse_equity_master", NSE_DAY_TWO, "2026-08-02")
        payload = self.refresh("nse_symbol_change", SYMBOL_CHANGE, "2026-08-02")
        self.assertEqual(payload["registry"], {"applied": 1, "unmatched": 1, "reused": 0})
        changes = self.rows(
            "SELECT old_value, new_value, applied_status FROM exchange_symbol_changes ORDER BY old_value"
        )
        self.assertEqual(
            [(item["old_value"], item["applied_status"]) for item in changes],
            [("BETA", "applied"), ("OLDUNK", "unmatched")],
        )
        repeated = self.refresh("nse_symbol_change", SYMBOL_CHANGE, "2026-08-03")
        self.assertEqual(repeated["registry"]["reused"], 2)

    def test_snapshot_ingestion_is_idempotent(self) -> None:
        first = self.refresh("nse_equity_master", NSE_DAY_ONE, "2026-08-01")
        second = self.refresh("nse_equity_master", NSE_DAY_ONE, "2026-08-01")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(second["registry"]["unchanged"], 2)
        self.assertEqual(
            len(self.rows("SELECT * FROM exchange_source_rejected_rows")), 1
        )

    def test_dry_run_writes_nothing(self) -> None:
        payload = refresh_exchange_universe(
            dataset="nse_equity_master",
            file=self.write("dry.csv", NSE_DAY_ONE),
            effective_date="2026-08-01",
            dry_run=True,
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
        )
        self.assertEqual(payload["mode"], "dry_run")
        self.assertIsNone(payload["snapshot_id"])
        self.assertEqual(payload["accepted_row_count"], 2)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.snapshots.exists())


class ResultDiscoveryTests(ExchangeCase):
    def test_filings_queue_only_new_or_advanced_periods(self) -> None:
        self.build_universe()
        payload = self.discover("nse_financial_results", NSE_RESULTS, "2026-08-05")
        self.assertEqual(payload["filings"]["stored"], 3)
        self.assertEqual(payload["filings"]["previous_watermark_utc"], None)
        self.assertEqual(payload["filings"]["watermark_utc"], "2026-08-05T13:00:00+00:00")
        self.assertEqual(payload["enqueue"]["queued"], 2)
        self.assertEqual(payload["enqueue"]["manual_review"], 1)
        queue = self.rows(
            """SELECT origin, company_path_normalized, reporting_scope, result_period,
                      queue_reason, status, candidate_id, exchange_filing_id
               FROM screener_company_queue ORDER BY queue_item_id"""
        )
        self.assertEqual(
            [(item["company_path_normalized"], item["reporting_scope"]) for item in queue],
            [("/company/BETANEW/", "standalone"), ("/company/ALPHA/consolidated/", "consolidated")],
        )
        self.assertTrue(all(item["origin"] == "exchange_filing" for item in queue))
        self.assertTrue(all(item["candidate_id"] is None for item in queue))
        self.assertTrue(all(item["queue_reason"] == "new_company" for item in queue))
        review = self.rows(
            """SELECT decision, decision_reason FROM exchange_screener_enqueues
               WHERE decision='manual_review'"""
        )
        self.assertEqual(review[0]["decision_reason"], "security_not_in_registry")

    def test_reingesting_the_same_feed_creates_no_new_work(self) -> None:
        self.build_universe()
        self.discover("nse_financial_results", NSE_RESULTS, "2026-08-05")
        again = self.discover("nse_financial_results", NSE_RESULTS, "2026-08-06")
        self.assertEqual(again["filings"]["stored"], 0)
        self.assertEqual(again["filings"]["already_stored"], 3)
        self.assertEqual(again["filings"]["after_watermark"], 0)
        self.assertEqual(sum(again["enqueue"].values()), 0)
        self.assertEqual(len(self.rows("SELECT * FROM screener_company_queue")), 2)

    def test_dual_listed_bse_filing_collects_the_nse_company_page(self) -> None:
        self.build_universe()
        payload = self.discover("bse_financial_results", BSE_RESULTS, "2026-08-05")
        self.assertEqual(payload["enqueue"]["queued"], 2)
        decisions = self.rows(
            """SELECT filing.exchange_key, decision.company_path_normalized,
                      decision.path_basis
               FROM exchange_screener_enqueues AS decision
               JOIN exchange_result_filings AS filing USING (filing_id)
               ORDER BY filing.exchange_key"""
        )
        self.assertEqual(
            [(item["exchange_key"], item["company_path_normalized"], item["path_basis"])
             for item in decisions],
            [
                ("500001", "/company/ALPHA/consolidated/", "nse_symbol"),
                ("500002", "/company/500002/consolidated/", "bse_scrip_code"),
            ],
        )

    def test_one_queue_item_when_both_exchanges_report_the_same_period(self) -> None:
        self.build_universe()
        self.discover("nse_financial_results", NSE_RESULTS, "2026-08-05")
        payload = self.discover("bse_financial_results", BSE_RESULTS, "2026-08-05")
        # Alpha's BSE filing resolves to the queue item its NSE filing created.
        self.assertEqual(payload["enqueue"]["reused"], 1)
        self.assertEqual(payload["enqueue"]["queued"], 1)
        alpha = self.rows(
            """SELECT * FROM screener_company_queue
               WHERE company_path_normalized='/company/ALPHA/consolidated/'"""
        )
        self.assertEqual(len(alpha), 1)

    def test_accepted_state_decides_stale_unchanged_and_advanced(self) -> None:
        self.build_universe()
        conn = connect(self.db)
        try:
            init_db(conn)
            with conn:
                conn.execute(
                    """INSERT INTO screener_company_collection_state
                       (company_path_normalized, reporting_scope,
                        latest_discovered_result_period, latest_accepted_result_period,
                        updated_at_utc)
                       VALUES ('/company/ALPHA/consolidated/', 'consolidated',
                               '2026-06', '2026-06', '2026-08-01T00:00:00+00:00')""",
                )
        finally:
            conn.close()
        same = self.discover("nse_financial_results", NSE_RESULTS, "2026-08-05")
        self.assertEqual(same["enqueue"]["already_accepted"], 1)
        older = self.discover(
            "nse_financial_results",
            nse_results(
                (
                    "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,31-MAR-2026,"
                    "Audited,Non-cumulative,Consolidated,01-Aug-2026 10:00:00",
                )
            ),
            "2026-08-06",
        )
        self.assertEqual(older["enqueue"]["stale"], 1)
        advanced = self.discover(
            "nse_financial_results",
            nse_results(
                (
                    "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-SEP-2026,"
                    "Un-Audited,Non-cumulative,Consolidated,05-Nov-2026 18:30:00",
                )
            ),
            "2026-11-05",
        )
        self.assertEqual(advanced["enqueue"]["queued"], 1)
        queued = self.rows(
            """SELECT result_period, queue_reason FROM screener_company_queue
               WHERE company_path_normalized='/company/ALPHA/consolidated/'"""
        )
        self.assertEqual(
            [(item["result_period"], item["queue_reason"]) for item in queued],
            [("2026-09", "result_period_advanced")],
        )

    def test_new_listing_becomes_collectable_after_the_next_master_refresh(self) -> None:
        self.build_universe()
        feed = nse_results(
            (
                "DELTA,Delta Power Limited,Power,Quarterly,30-JUN-2026,"
                "Un-Audited,Non-cumulative,Consolidated,05-Aug-2026 18:30:00",
            )
        )
        first = self.discover("nse_financial_results", feed, "2026-08-05")
        self.assertEqual(first["enqueue"]["manual_review"], 1)
        self.refresh("nse_equity_master", NSE_DAY_TWO, "2026-08-06")
        second = self.discover(
            "nse_financial_results",
            nse_results(
                (
                    "DELTA,Delta Power Limited,Power,Quarterly,30-JUN-2026,"
                    "Un-Audited,Non-cumulative,Consolidated,06-Aug-2026 18:30:00",
                )
            ),
            "2026-08-06",
        )
        self.assertEqual(second["enqueue"]["queued"], 1)

    def test_dry_run_projects_decisions_without_writing(self) -> None:
        self.build_universe()
        payload = discover_exchange_results(
            dataset="nse_financial_results",
            file=self.write("dry-results.csv", NSE_RESULTS),
            effective_date="2026-08-05",
            dry_run=True,
            db_path=self.db,
            snapshot_root=self.snapshots,
            now_utc=self.now,
        )
        self.assertEqual(payload["enqueue"], {
            "queued": 2, "reused": 0, "stale": 0,
            "already_accepted": 0, "manual_review": 1,
        })
        self.assertIsNone(payload["filings"])
        self.assertEqual(self.rows("SELECT * FROM screener_company_queue"), [])
        self.assertEqual(self.rows("SELECT * FROM exchange_result_filings"), [])

    def test_worker_claims_exchange_origin_items(self) -> None:
        self.build_universe()
        self.discover("nse_financial_results", NSE_RESULTS, "2026-08-05")
        conn = connect(self.db)
        try:
            init_db(conn)
            claimed = claim_due_companies(
                conn, now_utc="2026-08-05T00:00:00+00:00", limit=5, worker_id="test"
            )
        finally:
            conn.close()
        self.assertEqual({item.origin for item in claimed}, {"exchange_filing"})
        self.assertTrue(all(item.exchange_filing_id is not None for item in claimed))
        self.assertTrue(all(item.candidate_id is None for item in claimed))
        self.assertEqual(
            claimed[0].to_dict()["company_path_normalized"], "/company/BETANEW/"
        )

    def test_worker_prioritizes_the_latest_published_result_period(self) -> None:
        self.build_universe()
        old = nse_results(
            (
                "ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,31-DEC-2025,"
                "Un-Audited,Non-cumulative,Consolidated,05-Feb-2026 18:30:00",
            )
        )
        current = nse_results(
            (
                "BETANEW,Beta Global Limited,Services,Quarterly,30-JUN-2026,"
                "Un-Audited,Non-cumulative,Non-Consolidated,04-Aug-2026 11:15:00",
            )
        )
        self.discover("nse_financial_results", old, "2026-02-05")
        self.discover("nse_financial_results", current, "2026-08-05")
        conn = connect(self.db)
        try:
            claimed = claim_due_companies(
                conn, now_utc="2026-08-05T00:00:00+00:00", limit=1, worker_id="latest"
            )
        finally:
            conn.close()
        self.assertEqual(claimed[0].result_period, "2026-06")

    def test_screener_lag_stays_retryable_only_for_exchange_origin(self) -> None:
        error = RuntimeError(
            "Screen result period is absent from company quarterly results: "
            "expected=2026-06 actual=['2026-03']"
        )
        self.assertEqual(_failure_policy(error, "exchange_filing"), (True, False))
        self.assertEqual(_failure_policy(error, "screener_screen"), (False, False))

    def test_blocking_rule_codes_make_acceptance_failures_terminal(self) -> None:
        # The policy classifies from the message alone, so a blocking rule that
        # will fail identically on every retry must name itself in the message.
        manifest = ValueError(
            "Parse artifact does not satisfy automatic acceptance rules "
            "(parse_status=ok, validation_status=failed, requested_scope=standalone, "
            "actual_scope=standalone, blocking_rules=[screener_manifest_coverage])"
        )
        for origin in ("exchange_filing", "screener_screen"):
            self.assertEqual(_failure_policy(manifest, origin), (False, False))

    def test_coverage_reports_registry_filings_and_review(self) -> None:
        self.build_universe()
        self.discover("nse_financial_results", NSE_RESULTS, "2026-08-05")
        conn = connect(self.db)
        try:
            init_db(conn)
            coverage = build_exchange_universe_coverage(conn)
            screener = build_screener_discovery_coverage(conn)
        finally:
            conn.close()
        self.assertEqual(coverage["coverage_status"], "partial")
        self.assertEqual(coverage["missing_required_datasets"], [])
        summary = coverage["summary"]
        self.assertEqual(summary["registry_company_count"], 4)
        self.assertEqual(summary["multi_exchange_company_count"], 1)
        self.assertEqual(summary["securities_without_isin"], 1)
        self.assertEqual(summary["filings_by_result_period"], {"2026-06": 3})
        self.assertEqual(summary["enqueue_decision_counts"]["manual_review"], 1)
        self.assertEqual(summary["queue_counts_by_origin_status"], {"exchange_filing_pending": 2})
        self.assertEqual(coverage["manual_review"][0]["exchange_key"], "NOTLISTED")
        self.assertEqual(coverage["watermarks"][0]["last_broadcast_at_utc"], "2026-08-05T13:00:00+00:00")
        # Exchange-origin work stays visible in the shared queue coverage report.
        self.assertEqual(screener["summary"]["queue_origin_counts"], {"exchange_filing": 2})
        self.assertEqual(
            {item["company_name_raw"] for item in screener["queue"]},
            {"Alpha Industries Limited", "Beta Global Limited"},
        )

    def test_resolve_screener_path_rejects_unknown_and_unsafe_keys(self) -> None:
        self.build_universe()
        conn = connect(self.db)
        try:
            init_db(conn)
            missing = resolve_screener_path(
                conn, exchange="NSE", exchange_key="NOPE", reporting_scope="consolidated"
            )
            resolved = resolve_screener_path(
                conn, exchange="BSE", exchange_key="500002", reporting_scope="standalone"
            )
        finally:
            conn.close()
        self.assertEqual(missing["decision"], "manual_review")
        self.assertEqual(resolved["company_path_normalized"], "/company/500002/")


class MigrationTests(unittest.TestCase):
    def test_queue_rebuild_preserves_existing_screen_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            legacy = sqlite3.connect(db_path)
            legacy.row_factory = sqlite3.Row
            migrations = sorted(
                (Path(__file__).resolve().parents[1] / "db" / "migrations").glob("*.sql")
            )
            for migration in migrations:
                if migration.name.startswith("032_"):
                    break
                legacy.executescript(migration.read_text(encoding="utf-8"))
                legacy.execute(
                    "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, '2026-08-01')",
                    (migration.name,),
                )
            legacy.executescript(
                """
                INSERT INTO response_blobs
                  (blob_id, content_sha256, byte_size, local_path,
                   first_observed_at_utc, created_at_utc)
                  VALUES (1, 'abc', 1, '/tmp/a', '2026-08-01', '2026-08-01');
                INSERT INTO scrape_runs(run_id, started_at_utc, code_version, config_json, status)
                  VALUES (1, '2026-08-01', '0', '{}', 'ok');
                INSERT INTO fetch_observations
                  (fetch_id, run_id, source, requested_url, final_url, fetched_at_utc,
                   http_status, auth_mode, blob_id, fetch_status)
                  VALUES (1, 1, 'screener', 'u', 'u', '2026-08-01', 200, 'authenticated', 1, 'ok');
                INSERT INTO screener_screen_runs
                  (screen_run_id, query_role, query_raw, requested_url, fetch_id, blob_id,
                   parser_name, parser_version, observed_at_utc, result_count, page_number,
                   page_count, captured_row_count, coverage_status, validation_status)
                  VALUES (1, 'role', 'q', 'u', 1, 1, 'p', '1', '2026-08-01', 1, 1, 1, 1,
                          'complete', 'passed');
                INSERT INTO screener_result_candidates
                  (candidate_id, screen_run_id, source_position, screen_row_company_id,
                   company_name_raw, company_href_raw, company_path_normalized,
                   reporting_scope_hint, last_result_date_display, result_period,
                   discovered_at_utc)
                  VALUES (1, 1, 1, '11', 'Legacy Ltd', '/company/LEGACY/', '/company/LEGACY/',
                          'standalone', '202606.00', '2026-06', '2026-08-01');
                INSERT INTO screener_company_queue
                  (queue_item_id, candidate_id, company_path_normalized, reporting_scope,
                   result_period, queue_reason, status, created_at_utc, updated_at_utc)
                  VALUES (7, 1, '/company/LEGACY/', 'standalone', '2026-06', 'new_company',
                          'pending', '2026-08-01', '2026-08-01');
                INSERT INTO screener_company_queue_events
                  (queue_item_id, event_at_utc, to_status) VALUES (7, '2026-08-01', 'pending');
                """
            )
            legacy.commit()
            legacy.close()

            conn = connect(db_path)
            try:
                init_db(conn)
                row = conn.execute(
                    "SELECT * FROM screener_company_queue WHERE queue_item_id=7"
                ).fetchone()
                self.assertEqual(row["origin"], "screener_screen")
                self.assertEqual(row["candidate_id"], 1)
                self.assertIsNone(row["exchange_filing_id"])
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM screener_company_queue_events"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("PRAGMA foreign_key_check").fetchall(), []
                )
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
