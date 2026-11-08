"""Offline tests for compressed sources and the dataset registry (C4).

Neither adds behaviour on its own. Together they are what lets a scheduler ask
"what do I fetch for this dataset" without a URL being hard-coded in a Compose
file, and what lets a gzipped report be parsed while the stored evidence stays
byte-identical to what the exchange served.
"""

from __future__ import annotations

import gzip
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from scrapper.core.http import (
    GZIP_MAGIC,
    ZIP_MAGIC,
    _EXCHANGE_MEDIA_TYPES,
    _classify_exchange_response,
    validate_exchange_dataset_url,
)
from scrapper.db.database import connect, init_db
from scrapper.pipeline.exchange_universe import (
    inspect_exchange_dataset,
    refresh_exchange_universe,
    resolve_url,
    thresholds_for_dataset,
)
from scrapper.sources.exchange import datasets as registry
from scrapper.sources.exchange import tabular
from scrapper.sources.exchange.tabular import decompress_source

from scrapper.tests.test_e02_snapshot_guard import (
    ALPHA_ISIN,
    FULL_MASTER,
    AutomationCase,
)


class _Response:
    """The minimum of a requests.Response that classification actually reads."""

    def __init__(self, content: bytes, content_type: str, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"


def gzipped(payload: bytes) -> bytes:
    return gzip.compress(payload)


def zipped(payload: bytes, *, names: tuple[str, ...] = ("master.csv",)) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, payload)
    return buffer.getvalue()


class DecompressionTests(unittest.TestCase):
    def test_plain_bytes_pass_through_unchanged(self) -> None:
        self.assertEqual(decompress_source(FULL_MASTER), (FULL_MASTER, "none"))

    def test_gzip_is_detected_by_magic_bytes_not_by_name(self) -> None:
        self.assertEqual(decompress_source(gzipped(FULL_MASTER)), (FULL_MASTER, "gzip"))

    def test_zip_with_one_member_is_read(self) -> None:
        self.assertEqual(decompress_source(zipped(FULL_MASTER)), (FULL_MASTER, "zip"))

    def test_zip_with_several_members_is_refused_not_guessed(self) -> None:
        archive = zipped(FULL_MASTER, names=("equity.csv", "sme.csv"))
        with self.assertRaises(ValueError) as caught:
            decompress_source(archive)
        self.assertIn("exactly one member", str(caught.exception))

    def test_empty_zip_is_refused_as_a_zip_not_as_stray_bytes(self) -> None:
        with self.assertRaises(ValueError) as caught:
            decompress_source(zipped(FULL_MASTER, names=()))
        self.assertIn("exactly one member", str(caught.exception))

    def test_truncated_gzip_is_a_clear_error(self) -> None:
        with self.assertRaises(ValueError) as caught:
            decompress_source(gzipped(FULL_MASTER)[:12])
        self.assertIn("gzip", str(caught.exception))

    def test_expansion_is_bounded(self) -> None:
        with self.assertRaises(ValueError) as caught:
            decompress_source(gzipped(b"x" * 5000), max_bytes=100)
        self.assertIn("exceeds", str(caught.exception))

    def test_the_http_layer_agrees_with_the_parser_about_magic_bytes(self) -> None:
        # core.http keeps its own copy so it depends on nothing above it; the two
        # must not drift apart.
        self.assertEqual(GZIP_MAGIC, tabular.GZIP_MAGIC)
        self.assertEqual(ZIP_MAGIC, tabular.ZIP_MAGIC)


class ExchangeResponseClassificationTests(unittest.TestCase):
    def test_gzip_media_types_are_allowed(self) -> None:
        for media in ("application/gzip", "application/x-gzip"):
            self.assertIn(media, _EXCHANGE_MEDIA_TYPES)

    def test_binary_content_is_not_sniffed_for_html(self) -> None:
        # Arbitrary compressed bytes decoded as text can begin with anything;
        # the magic bytes have to settle it before the HTML sniff runs.
        response = _Response(gzipped(FULL_MASTER), "application/gzip")
        self.assertEqual(_classify_exchange_response(response), "ok")

    def test_a_zipped_export_is_not_rejected(self) -> None:
        response = _Response(zipped(FULL_MASTER), "application/zip")
        self.assertEqual(_classify_exchange_response(response), "ok")

    def test_an_html_interstitial_is_still_rejected(self) -> None:
        response = _Response(b"<!DOCTYPE html><html>Access Denied", "text/html")
        self.assertEqual(_classify_exchange_response(response), "rejected")

    def test_an_absent_artifact_is_told_apart_from_a_refusal(self) -> None:
        # "There is no file for this day" is the scheduler's only sound evidence
        # of non-publication, and it must not be confused with being turned away
        # or with the endpoint breaking.
        for status, expected in (
            (404, "not_found"), (410, "not_found"),
            (403, "rejected"), (429, "rejected"), (500, "error"),
        ):
            with self.subTest(status=status):
                response = _Response(b"", "text/html", status_code=status)
                self.assertEqual(_classify_exchange_response(response), expected)


class DatasetRegistryTests(unittest.TestCase):
    def test_every_default_url_passes_the_fetch_allowlist(self) -> None:
        for config in registry.DATASETS.values():
            if config.default_url is None:
                continue
            with self.subTest(dataset=config.key):
                # A period-addressed template is validated with a real period
                # substituted in, since that is the URL that gets fetched.
                url = registry.default_url_for(
                    config.key,
                    result_period="2026-06" if config.url_needs_result_period else None,
                )
                self.assertEqual(
                    validate_exchange_dataset_url(str(url), config.exchange), url
                )

    def test_the_registry_covers_every_dataset_the_pipeline_accepts(self) -> None:
        from scrapper.pipeline.exchange_universe import ALL_DATASETS

        self.assertEqual(set(registry.DATASET_KEYS), set(ALL_DATASETS))

    def test_row_floors_come_from_the_registry(self) -> None:
        self.assertEqual(
            thresholds_for_dataset("bse_security_master").row_floor,
            registry.dataset_config("bse_security_master").row_floor,
        )

    def test_an_absent_url_falls_back_to_the_registry_default(self) -> None:
        self.assertEqual(
            resolve_url("nse_equity_master", None, result_period=None),
            registry.DATASETS["nse_equity_master"].default_url,
        )

    def test_an_explicit_url_always_wins(self) -> None:
        override = "https://nsearchives.nseindia.com/content/equities/other.csv"
        self.assertEqual(
            resolve_url("nse_equity_master", override, result_period=None), override
        )

    def test_a_period_addressed_feed_refuses_to_guess_its_period(self) -> None:
        with self.assertRaises(ValueError) as caught:
            registry.default_url_for("nse_financial_results")
        self.assertIn("--result-period", str(caught.exception))

    def test_the_period_ended_value_is_the_last_day_of_the_period(self) -> None:
        url = str(registry.default_url_for("nse_financial_results", result_period="2026-02"))
        self.assertIn("period_ended=28-Feb-2026", url)

    def test_the_mii_file_is_deliberately_unwired(self) -> None:
        # C5's parser is written against declared aliases for a report nobody has
        # inspected yet. Wiring a URL would let it run unattended.
        self.assertIsNone(registry.DATASETS["nse_mii_security_file"].default_url)

    def test_an_unknown_dataset_is_refused_by_name(self) -> None:
        with self.assertRaises(ValueError):
            registry.dataset_config("nse_imaginary_master")

    def test_authoritative_scope_is_narrower_than_the_exchange(self) -> None:
        self.assertEqual(
            registry.authoritative_segments("nse_equity_master"), (("NSE", "equity"),)
        )
        self.assertEqual(
            registry.authoritative_segments("nse_mii_security_file"),
            (("NSE", "equity"), ("BSE", "equity")),
        )


class CompressedIngestTests(AutomationCase):
    GUARD_OVERRIDES = {"row_floor": 1}

    def test_a_gzipped_master_is_parsed_and_applied(self) -> None:
        payload = self.refresh(
            "nse_equity_master", gzipped(FULL_MASTER), "2026-08-01"
        )

        self.assertEqual(payload["compression"], "gzip")
        self.assertEqual(payload["validation"]["status"], "passed")
        self.assertEqual(payload["registry"]["added"], 3)

    def test_the_stored_blob_is_the_compressed_bytes_the_exchange_served(self) -> None:
        served = gzipped(FULL_MASTER)
        payload = self.refresh("nse_equity_master", served, "2026-08-01")

        stored = self.rows(
            """SELECT blob.local_path FROM exchange_source_snapshots AS snapshot
               JOIN response_blobs AS blob USING (blob_id)
               WHERE snapshot.snapshot_id=?""",
            payload["snapshot_id"],
        )[0]
        self.assertEqual(Path(stored["local_path"]).read_bytes(), served)
        self.assertTrue(stored["local_path"].endswith(".csv.gz"))

    def test_the_snapshot_records_which_codec_it_arrived_under(self) -> None:
        self.refresh("nse_equity_master", zipped(FULL_MASTER), "2026-08-01")

        self.assertEqual(
            self.rows("SELECT compression FROM exchange_source_snapshots")[0][
                "compression"
            ],
            "zip",
        )

    def test_compression_does_not_change_what_the_registry_sees(self) -> None:
        plain = self.refresh("nse_equity_master", FULL_MASTER, "2026-08-01")
        self.assertEqual(
            plain["headers"],
            self.refresh("nse_sme_master", gzipped(FULL_MASTER), "2026-08-01")["headers"],
        )


class InspectDatasetTests(unittest.TestCase):
    """The step that has to happen before a new report is trusted."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "master.csv.gz"
        self.path.write_bytes(gzipped(FULL_MASTER))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_it_reports_the_headers_and_rows_as_published(self) -> None:
        payload = inspect_exchange_dataset(
            dataset="nse_equity_master", file=self.path, now_utc=lambda: "2026-08-07T00:00:00+00:00"
        )

        self.assertEqual(payload["compression"], "gzip")
        self.assertEqual(payload["row_count"], 3)
        self.assertIn("SYMBOL", payload["headers"])
        self.assertEqual(payload["sample_rows"][0]["SYMBOL"], "ALPHA")

    def test_it_shows_columns_no_alias_knows_about(self) -> None:
        # The whole point: a column the parser cannot bind must still be visible,
        # or there is no way to learn what the exchange actually publishes.
        self.path.write_bytes(b"SURPRISE COLUMN,SYMBOL\nvalue,ALPHA\n")
        payload = inspect_exchange_dataset(
            dataset="nse_equity_master", file=self.path,
            now_utc=lambda: "2026-08-07T00:00:00+00:00",
        )

        self.assertEqual(payload["headers"], ["SURPRISE COLUMN", "SYMBOL"])

    def test_inspection_stores_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as store:
            db = Path(store) / "scraper.db"
            inspect_exchange_dataset(
                dataset="nse_equity_master", file=self.path,
                now_utc=lambda: "2026-08-07T00:00:00+00:00",
            )
            self.assertFalse(db.exists())

    def test_a_dataset_with_no_wired_url_says_so(self) -> None:
        with self.assertRaises(ValueError) as caught:
            inspect_exchange_dataset(dataset="nse_mii_security_file")
        self.assertIn("--file or --url", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
