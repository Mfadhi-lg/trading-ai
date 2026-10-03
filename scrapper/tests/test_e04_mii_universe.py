"""Offline tests for the combined MII security file (C5).

The file carries NSE-listed and BSE-exclusive securities in one table, so every
row has to say which it is; nothing is allowed to default to an exchange. It is
also a *partial* master — it lists neither exchange's SME segment — which is why
absence sweeps are scoped to what a file is authoritative for rather than to
whatever it happens to contain.
"""

from __future__ import annotations

import unittest

from scrapper.sources.exchange.datasets import authoritative_segments
from scrapper.sources.exchange.security_master import (
    classify_instrument,
    parse_security_master,
)

from .test_e02_snapshot_guard import (
    ALPHA_ISIN,
    BETA_ISIN,
    DELTA_ISIN,
    GAMMA_ISIN,
    AutomationCase,
    nse_master,
)


MII_DATASET = "nse_mii_security_file"
MII_HEADER = (
    "SYMBOL,SERIES,SECURITY NAME,ISIN,SCRIP CODE,EXCHANGE,INSTRUMENT,FACE VALUE"
)
# ALPHA is listed on NSE and also has a BSE code: dual-listed, keyed by symbol.
MII_NSE_ROW = f"ALPHA,EQ,Alpha Industries Limited,{ALPHA_ISIN},500001,NSE,Equity,10"
# Beta trades only on BSE, so it is keyed by its six-digit scrip code.
MII_BSE_ROW = f",,Beta Services Limited,{BETA_ISIN},500002,BSE,Equity,1"
MII_ETF_ROW = f"NIFTYETF,EQ,Nifty Exchange Traded Fund,{GAMMA_ISIN},,NSE,ETF,1"
MII_BROKEN_ROW = "BROKEN,EQ,Broken Row Limited,INE000000000,,NSE,Equity,1"
MII_ORPHAN_ROW = f",,Orphan Limited,{DELTA_ISIN},,,Equity,1"


def mii_file(rows: tuple[str, ...], *, header: str = MII_HEADER) -> bytes:
    return ("\n".join((header, *rows)) + "\n").encode()


FULL_MII = mii_file(
    (MII_NSE_ROW, MII_BSE_ROW, MII_ETF_ROW, MII_BROKEN_ROW, MII_ORPHAN_ROW)
)


class InstrumentClassificationTests(unittest.TestCase):
    def test_equity_is_recognised_from_either_column(self) -> None:
        self.assertEqual(classify_instrument("EQ", ""), "equity")
        self.assertEqual(classify_instrument("", "Equity Shares"), "equity")

    def test_each_excluded_class_is_named_not_lumped_together(self) -> None:
        for instrument, expected in (
            ("Exchange Traded Fund", "etf"),
            ("ETF", "etf"),
            ("Infrastructure Investment Trust", "invit"),
            ("REIT", "reit"),
            ("Preference Shares", "preference_share"),
            ("Warrant", "warrant"),
            ("Rights Entitlement", "rights_entitlement"),
            ("Non Convertible Debenture", "debt"),
            ("Government Security", "government_security"),
        ):
            with self.subTest(instrument=instrument):
                self.assertEqual(classify_instrument("", instrument), expected)

    def test_a_non_equity_marker_beats_an_equity_series(self) -> None:
        # "EQ" alongside preference text is a preference share, not an equity.
        self.assertEqual(classify_instrument("EQ", "Equity Preference Shares"),
                         "preference_share")

    def test_a_non_equity_series_beats_equity_instrument_text(self) -> None:
        self.assertEqual(classify_instrument("GB", "Equity"), "government_security")

    def test_an_unknown_class_is_never_assumed_to_be_equity(self) -> None:
        self.assertEqual(classify_instrument("ZZ", "Something New"), "unrecognised")


class CombinedFileParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_security_master(FULL_MII, MII_DATASET)

    def test_rows_are_classified_by_their_own_columns(self) -> None:
        self.assertEqual(
            [(item.exchange, item.exchange_key) for item in self.parsed.records],
            [("NSE", "ALPHA"), ("BSE", "500002")],
        )

    def test_a_dual_listed_row_is_keyed_by_symbol_not_by_scrip_code(self) -> None:
        alpha = self.parsed.records[0]
        self.assertEqual(alpha.nse_symbol, "ALPHA")
        self.assertIsNone(alpha.bse_scrip_code)
        # The BSE code the file states is retained as evidence, never as a key.
        self.assertEqual(alpha.attributes["scrip_code"], "500001")

    def test_an_etf_is_excluded_and_counted_not_rejected(self) -> None:
        self.assertEqual(self.parsed.excluded_row_count, 1)
        self.assertEqual(self.parsed.excluded[0].instrument_class, "etf")
        self.assertNotIn(
            "etf", [item.reason_code for item in self.parsed.rejected]
        )

    def test_a_malformed_row_is_still_a_rejected_row(self) -> None:
        self.assertIn("invalid_isin", [item.reason_code for item in self.parsed.rejected])

    def test_an_unclassifiable_row_is_rejected_rather_than_defaulted(self) -> None:
        self.assertIn(
            "unclassified_exchange",
            [item.reason_code for item in self.parsed.rejected],
        )

    def test_an_unknown_shape_hard_fails_instead_of_reading_by_position(self) -> None:
        # No symbol and no scrip-code column: nothing in the file can be keyed.
        with self.assertRaises(ValueError) as caught:
            parse_security_master(
                b"SECURITY NAME,ISIN,SERIES\nAlpha Limited," + ALPHA_ISIN.encode() + b",EQ\n",
                MII_DATASET,
            )
        self.assertIn("symbol", str(caught.exception))

    def test_a_file_with_no_way_to_classify_instruments_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            parse_security_master(
                b"SYMBOL,SECURITY NAME,ISIN\nALPHA,Alpha Limited," + ALPHA_ISIN.encode() + b"\n",
                MII_DATASET,
            )
        self.assertIn("instrument", str(caught.exception))

    def test_an_nse_symbol_and_a_bse_code_are_not_duplicates_of_each_other(self) -> None:
        # Both exchanges' keys share one namespace only if the parser is careless.
        parsed = parse_security_master(
            mii_file((MII_NSE_ROW, MII_BSE_ROW)), MII_DATASET
        )
        self.assertEqual(parsed.accepted_row_count, 2)
        self.assertEqual(parsed.rejected_row_count, 0)

    def test_the_exchange_column_decides_before_the_key_shapes_do(self) -> None:
        # A row that carries both a symbol and a code but says BSE is BSE-only.
        parsed = parse_security_master(
            mii_file((f"BETA,EQ,Beta Services Limited,{BETA_ISIN},500002,BSE,Equity,1",)),
            MII_DATASET,
        )
        self.assertEqual(parsed.records[0].exchange, "BSE")
        self.assertEqual(parsed.records[0].exchange_key, "500002")


class CombinedFileRegistryTests(AutomationCase):
    # These fixtures are a handful of rows, two of which exist to be rejected, so
    # the size- and rate-relative rules are relaxed; none of these tests is about
    # them and they have their own coverage in test_e02.
    GUARD_OVERRIDES = {
        "row_floor": 1, "row_count_drop_ratio": None, "rejected_row_rate": 1.0,
    }

    def test_it_creates_one_company_for_each_exchange_it_covers(self) -> None:
        payload = self.refresh(MII_DATASET, FULL_MII, "2026-08-01")

        self.assertEqual(payload["validation"]["status"], "passed")
        self.assertEqual(payload["registry"]["added"], 2)
        self.assertEqual(
            [
                (row["exchange"], row["exchange_key"])
                for row in self.rows(
                    "SELECT exchange, exchange_key FROM exchange_securities"
                    " ORDER BY exchange, exchange_key"
                )
            ],
            [("BSE", "500002"), ("NSE", "ALPHA")],
        )

    def test_a_dual_listed_isin_stays_one_registry_company(self) -> None:
        self.refresh("nse_equity_master", nse_master((
            f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10",
        )), "2026-08-01")
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-02")

        companies = self.rows(
            "SELECT isin FROM exchange_company_registry WHERE isin=?", ALPHA_ISIN
        )
        self.assertEqual(len(companies), 1)
        self.assertEqual(
            len(self.rows(
                """SELECT link_id FROM exchange_company_links AS link
                   JOIN exchange_securities USING (exchange_security_id)
                   WHERE exchange_key='ALPHA' AND link.is_current=1"""
            )),
            1,
        )

    def test_the_excluded_rows_are_kept_as_evidence(self) -> None:
        payload = self.refresh(MII_DATASET, FULL_MII, "2026-08-01")

        self.assertEqual(payload["excluded_rows_by_class"], {"etf": 1})
        stored = self.rows(
            "SELECT instrument_class FROM exchange_source_excluded_rows"
            " WHERE snapshot_id=?",
            payload["snapshot_id"],
        )
        self.assertEqual([row["instrument_class"] for row in stored], ["etf"])
        self.assertEqual(
            self.rows(
                "SELECT excluded_row_count FROM exchange_source_snapshots WHERE snapshot_id=?",
                payload["snapshot_id"],
            )[0]["excluded_row_count"],
            1,
        )

    def test_a_partial_master_never_sweeps_a_segment_it_does_not_list(self) -> None:
        # An SME security exists and the MII file lists no SME securities. That
        # is not evidence the SME security has gone.
        self.refresh("nse_sme_master", nse_master((
            f"GAMMA,Gamma Textiles Limited,SM,01-JAN-2010,10,1,{GAMMA_ISIN},10",
        )), "2026-08-01")
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-02")

        gamma = self.security("GAMMA")
        self.assertEqual(gamma["segment"], "sme")
        self.assertEqual(gamma["absence_streak"], 0)
        self.assertEqual(gamma["is_present"], 1)
        self.assertNotIn("absence_observed", self.event_types("GAMMA"))

    def test_the_nse_equity_master_does_not_sweep_sme_securities_either(self) -> None:
        # The same correctness fix, on the split that already shipped.
        self.refresh("nse_sme_master", nse_master((
            f"GAMMA,Gamma Textiles Limited,SM,01-JAN-2010,10,1,{GAMMA_ISIN},10",
        )), "2026-08-01")
        self.refresh("nse_equity_master", nse_master((
            f"ALPHA,Alpha Industries Limited,EQ,20-JAN-1995,10,1,{ALPHA_ISIN},10",
        )), "2026-08-02")

        self.assertEqual(self.security("GAMMA")["absence_streak"], 0)

    def test_it_still_sweeps_the_segments_it_is_authoritative_for(self) -> None:
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-01")
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW,)), "2026-08-02")

        beta = self.security("500002")
        self.assertEqual(beta["absence_streak"], 1)
        self.assertEqual(beta["is_present"], 1)

    def test_the_guard_measures_each_authoritative_scope_separately(self) -> None:
        payload = self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)),
                               "2026-08-01")

        self.assertEqual(
            [
                (item["exchange"], item["segment"], item["accepted_row_count"])
                for item in payload["validation"]["metrics"]["scope_metrics"]
            ],
            [("NSE", "equity", 1), ("BSE", "equity", 1)],
        )

    def test_the_declared_scope_matches_what_the_parser_produces(self) -> None:
        parsed_segments = set(parse_security_master(FULL_MII, MII_DATASET).segments)
        self.assertTrue(parsed_segments <= set(authoritative_segments(MII_DATASET)))


class ScopeGuardTests(AutomationCase):
    """A file may not lose one authoritative scope inside a healthy total.

    The whole-file rules see 3000 rows become 2800 and pass; the sweep runs
    per scope, so the guard has to look there too.
    """

    GUARD_OVERRIDES = {"row_floor": 1, "rejected_row_rate": 1.0}

    NEW_NSE_ROW = f"DELTA,EQ,Delta Foods Limited,{DELTA_ISIN},,NSE,Equity,10"

    def test_a_scope_that_collapses_is_rejected_though_the_total_holds(self) -> None:
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-01")

        # Two rows again — NSE gained one, BSE-exclusive lost its only one.
        payload = self.refresh(
            MII_DATASET, mii_file((MII_NSE_ROW, self.NEW_NSE_ROW)), "2026-08-02"
        )

        codes = [item["code"] for item in payload["validation"]["findings"]]
        self.assertEqual(payload["validation"]["status"], "failed")
        self.assertNotIn("row_count_collapse", codes)
        self.assertIn("scope_row_count_collapse", codes)

    def test_the_rejected_file_leaves_the_lost_scope_untouched(self) -> None:
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-01")
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, self.NEW_NSE_ROW)), "2026-08-02")

        beta = self.security("500002")
        # Not swept, not counted absent, and the new NSE row never landed.
        self.assertEqual(beta["absence_streak"], 0)
        self.assertEqual(beta["is_present"], 1)
        self.assertEqual(self.rows("SELECT 1 FROM exchange_securities WHERE exchange_key='DELTA'"), [])

    def test_a_scope_growing_and_another_holding_still_passes(self) -> None:
        self.refresh(MII_DATASET, mii_file((MII_NSE_ROW, MII_BSE_ROW)), "2026-08-01")

        payload = self.refresh(
            MII_DATASET,
            mii_file((MII_NSE_ROW, self.NEW_NSE_ROW, MII_BSE_ROW)),
            "2026-08-02",
        )
        self.assertEqual(payload["validation"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
