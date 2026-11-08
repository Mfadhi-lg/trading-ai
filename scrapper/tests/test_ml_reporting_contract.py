from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scrapper.core.reporting_scope import preferred_scope, require_scope
from scrapper.db.database import connect, init_db
from scrapper.pipeline.ml_contracts import MLContractError, build_screener_ml_features
from scrapper.sources.screener.urls import company_url


class StandaloneFinancialContractTests(unittest.TestCase):
    def test_consolidated_url_and_selection_are_rejected(self) -> None:
        self.assertEqual(company_url("ALPHA"), "https://www.screener.in/company/ALPHA/")
        with self.assertRaises(ValueError):
            company_url("ALPHA", "consolidated")
        with self.assertRaises(ValueError):
            require_scope("consolidated")
        with self.assertRaises(ValueError):
            preferred_scope({"standalone", "consolidated"})

    def test_ml_boundary_rejects_mixed_financial_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "fresh.sqlite")
            try:
                init_db(conn, apply_migrations=True)
                with conn:
                    # Simulate a legacy/corrupt database written before the
                    # database guard existed; the ML boundary must still fail.
                    conn.execute("DROP TRIGGER trg_financial_observations_standalone_only")
                    conn.execute("DROP TRIGGER trg_financial_observations_standalone_only_update")
                    company_id = conn.execute(
                        "INSERT INTO companies(current_legal_name) VALUES ('Alpha')"
                    ).lastrowid
                    blob_id = conn.execute(
                        "INSERT INTO response_blobs(content_sha256,byte_size,local_path,first_observed_at_utc,created_at_utc) VALUES ('a',1,'/a','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
                    ).lastrowid
                    parse_id = conn.execute(
                        "INSERT INTO parse_artifacts(blob_id,parser_name,parser_version,manifest_version,parsed_at_utc,parse_status,validation_status) VALUES (?, 'p','1','1','2026-01-01T00:00:00+00:00','ok','passed')", (blob_id,)
                    ).lastrowid
                    conn.execute("INSERT INTO accepted_page_versions(company_id,source,reporting_scope,resolved_source_period,parse_id,accepted_at_utc,accepted_by,acceptance_mode) VALUES (?, 'screener','standalone','2026-06',?,'2026-01-01T00:00:00+00:00','system','automatic')", (company_id, parse_id))
                    conn.execute("INSERT INTO financial_observations(parse_id,reporting_scope,section_code,line_item_raw,row_position,period_label_raw,period_type,column_position,first_observed_at_utc,available_at_utc,availability_basis,display_value) VALUES (?, 'consolidated','pnl','Sales',0,'Jun 2026','quarterly',0,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','first_observed','1')", (parse_id,))
                with self.assertRaises(MLContractError):
                    build_screener_ml_features(conn)
            finally:
                conn.close()

    def test_database_backstop_rejects_future_consolidated_financial_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "fresh.sqlite")
            try:
                init_db(conn, apply_migrations=True)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO financial_observations(parse_id,reporting_scope,section_code,line_item_raw,row_position,period_label_raw,period_type,column_position,first_observed_at_utc,available_at_utc,availability_basis,display_value) VALUES (1,'consolidated','pnl','Sales',0,'Jun','quarterly',0,'x','x','first_observed','1')"
                    )
            finally:
                conn.close()
