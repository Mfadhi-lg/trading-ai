"""Portable status probes: synthetic files only; runtime commands are mocked."""
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ops.host import runtime_status as status


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        directory = self.root / "scrapper/db/migrations"
        directory.mkdir(parents=True)
        for name in ("001_first.sql", "002_second.sql"):
            (directory / name).write_text("-- fixture\n")
        manifest = self.root / "docs/cleanup/baselines/C22.3/migration-manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"historical_identities": [{"filename": "000_retained.sql"}]}))

    def database(self, names):
        path = self.root / "private # database?.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE schema_migrations (version TEXT)")
            conn.executemany("INSERT INTO schema_migrations VALUES (?)", [(name,) for name in names])
            conn.commit()
        return path

    def test_schema_exact_filenames_and_historical_identity_without_writes(self):
        db = self.database(["001_first.sql", "002_second.sql", "000_retained.sql"])
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        result = status.migrations(self.root, db)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["value"]["matches_expected"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        self.assertNotIn(str(db), json.dumps(result))

    def test_schema_missing_unknown_and_duplicates_are_warning(self):
        db = self.database(["001_first.sql", "001_first.sql", "private-secret"])
        result = status.migrations(self.root, db)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["value"]["pending"], ["002_second.sql"])
        self.assertEqual(result["value"]["unknown_identity_count"], 1)
        self.assertEqual(result["value"]["duplicate_count"], 1)
        self.assertNotIn("private-secret", json.dumps(result))

    def test_missing_database_never_created(self):
        db = self.root / "absent.db"
        self.assertEqual(status.migrations(self.root, db)["status"], "unavailable")
        self.assertFalse(db.exists())

    def test_wal_or_journal_refused_before_connect(self):
        db = self.database(["001_first.sql"])
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(db) + suffix)
                sidecar.write_bytes(b"fixture")
                with patch.object(status.sqlite3, "connect") as connect:
                    self.assertEqual(status.migrations(self.root, db)["status"], "unknown")
                    connect.assert_not_called()
                sidecar.unlink()

    def test_symlink_to_wal_database_is_not_read_as_stale_base(self):
        db = self.database(["001_first.sql", "002_second.sql"])
        alias = self.root / "alias.db"
        alias.symlink_to(db)
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO schema_migrations VALUES ('999_unknown.sql')")
        conn.commit()
        with patch.object(status.sqlite3, "connect") as connect:
            self.assertEqual(status.migrations(self.root, alias)["status"], "unknown")
            connect.assert_not_called()

    def test_inaccessible_resolution_does_not_abort_or_leak_paths(self):
        path = self.root / "private"
        with patch.object(Path, "resolve", side_effect=PermissionError("secret location")):
            results = [status.migrations(self.root, path), status.documents(path, path),
                       status.previous(path, 3600, datetime.now(timezone.utc))]
        for result in results:
            self.assertIn(result["status"], {"unknown", "unavailable"})
            self.assertNotIn("secret location", json.dumps(result))

    def test_missing_worker_service_label_is_unclassified(self):
        with patch.object(status, "run", return_value="trading-ai \n"):
            result = status.workers(True)
            self.assertEqual(result["status"], "warning")
            self.assertEqual(result["value"]["unclassified_count"], 1)

    def test_invalid_ledger_unknown_and_readonly_uri(self):
        db = self.root / "bad.db"
        db.write_bytes(b"not SQLite")
        self.assertEqual(status.migrations(self.root, db)["status"], "unknown")

    def test_storage_missing_sentinel_unmounted_inaccessible_and_unknown(self):
        self.assertEqual(status.documents(None, None)["status"], "unknown")
        self.assertEqual(status.documents(self.root, None)["value"], "unavailable")
        (self.root / status.SENTINEL).write_text("fixture")
        self.assertEqual(status.documents(self.root, None)["status"], "warning")
        with patch.object(status.os.path, "ismount", return_value=False):
            self.assertEqual(status.documents(self.root, self.root)["value"], "unmounted")
        with patch.object(status.os.path, "ismount", return_value=True):
            self.assertEqual(status.documents(self.root, self.root)["status"], "verified")
        with patch.object(status.os, "scandir", side_effect=PermissionError("secret path")):
            result = status.documents(self.root, None)
            self.assertEqual(result["value"], "inaccessible")
            self.assertNotIn("secret path", json.dumps(result))

    def test_old_recent_future_and_malformed_reports_never_become_live(self):
        now = datetime.now(timezone.utc)
        path = self.root / "report.json"
        for age in (7200, 30):
            path.write_text(json.dumps({"report_version": 1, "generated_at_utc": (now - timedelta(seconds=age)).isoformat()}))
            result = status.previous(path, 3600, now)
            self.assertEqual(result["status"], "warning")
            self.assertEqual(result["value"]["stale"], age > 3600)
        for payload in ([], {}, {"report_version": 1, "generated_at_utc": (now + timedelta(seconds=30)).isoformat()}):
            path.write_text(json.dumps(payload))
            self.assertEqual(status.previous(path, 3600, now)["status"], "unknown")

    def test_missing_runtime_access_unknown_no_error_details(self):
        with patch.object(status, "run", side_effect=OSError("credentials: secret")):
            for result in (status.container("test", "a" * 40), status.dispatcher(True), status.workers(True)):
                self.assertEqual(result["status"], "unknown")
                self.assertNotIn("secret", json.dumps(result))

    def test_live_container_metadata_compare_and_redaction(self):
        with patch.object(status, "run", return_value='true "' + 'a' * 40 + '"') as probe:
            result = status.container("test", "b" * 40)
            self.assertFalse(result["value"]["matches_intended"])
            self.assertEqual(result["status"], "warning")
            self.assertNotIn(".Config.Env", str(probe.call_args))
        with patch.object(status, "run", return_value='false "' + 'a' * 40 + '"'):
            self.assertEqual(status.container("test", "a" * 40)["status"], "unknown")

    def test_dispatcher_only_uses_loaded_arguments(self):
        with patch.object(status, "run", return_value='arguments = {\n /private/runtime/ops/activation/automatic_acquisition.py\n --tick\n}\n'):
            self.assertEqual(status.dispatcher(True)["value"], "bounded_dispatcher_loaded")
        with patch.object(status, "run", return_value='environment = {\n /private/runtime/ops/activation/automatic_acquisition.py\n --tick\n}\n'):
            self.assertEqual(status.dispatcher(True)["status"], "warning")

    def test_legacy_worker_inventory_scoped_to_projects(self):
        with patch.object(status, "run", return_value="trading-ai worker\ntrading-ai-phase4-routine document-download-worker\nother-project scheduler\ntrading-ai mystery\n"):
            result = status.workers(True)
            self.assertEqual(result["value"]["running_retired_roles"], ["worker"])
            self.assertEqual(result["value"]["unclassified_count"], 1)
            self.assertEqual(result["status"], "warning")

    def test_default_report_has_metadata_no_runtime_probes(self):
        with patch.object(status, "ROOT", self.root), patch.object(status, "run", return_value="a" * 40) as probe:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(status.main([]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(all(call.args[0][0] == "git" for call in probe.call_args_list))
            for item in report["claims"].values():
                self.assertIn(item["status"], {"verified", "warning", "unavailable", "unknown"})
                for key in ("source", "method", "observed_at_utc"):
                    self.assertTrue(item[key])
            for key in ("deployed_revision", "schema", "document_store", "dispatcher", "workers", "last_observation"):
                self.assertEqual(report["claims"][key]["status"], "unknown")

    def test_repository_dirty_and_revision_mismatch(self):
        with patch.object(status, "run", side_effect=["a" * 40, "b" * 40, " M file"]):
            result = status.repository(self.root, "main")
            self.assertEqual(result["status"], "warning")
            self.assertTrue(result["value"]["dirty"])


if __name__ == "__main__":
    unittest.main()
