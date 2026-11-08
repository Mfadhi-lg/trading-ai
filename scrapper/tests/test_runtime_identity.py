from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scrapper.runtime_identity import IDENTITY_MARKER, assert_writable_targets


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / "state" / "scraper.db"
        self.store = self.root / "store"
        self.documents = self.root / "documents"

    def tearDown(self):
        self.temp.cleanup()

    def call(self, **env):
        values = {
            "TRADING_AI_ENV": "test",
            "TRADING_AI_RUNTIME_IDENTITY_FILE": "",
            "TRADING_AI_CLONE_IDENTITY_FILE": "",
        }
        values.update(env)
        with patch.dict(os.environ, values, clear=False):
            return assert_writable_targets(
                db=self.db, store=self.store, documents=self.documents
            )

    def test_environment_is_required_before_any_target_creation(self):
        with patch.dict(os.environ, {
                "TRADING_AI_ENV": "",
                "TRADING_AI_RUNTIME_IDENTITY_FILE": "",
                "TRADING_AI_CLONE_IDENTITY_FILE": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "TRADING_AI_ENV"):
                assert_writable_targets(db=self.db, store=self.store, documents=self.documents)
        self.assertFalse(self.root.joinpath("state").exists())
        self.assertFalse(self.store.exists())

    def test_test_environment_allows_isolated_targets_and_logs_public_identity(self):
        output = StringIO()
        with redirect_stderr(output):
            self.call(TRADING_AI_GIT_COMMIT="abc123", TRADING_AI_IMAGE_ID="sha256:123")
        record = json.loads(output.getvalue())
        self.assertEqual(record["environment"], "test")
        self.assertEqual(record["git_commit"], "abc123")
        self.assertEqual(record["image_id"], "sha256:123")
        self.assertNotIn("secret", output.getvalue().lower())

    def test_development_rejects_known_production_target(self):
        with self.assertRaisesRegex(RuntimeError, "targets production path"):
            with patch.dict(os.environ, {"TRADING_AI_ENV": "development"}, clear=False):
                assert_writable_targets(
                    db=Path("/data/scraper.db"), store=self.store,
                    documents=self.documents,
                )

    def test_production_requires_exact_manifest_release_and_sentinels(self):
        self.store.mkdir()
        self.documents.mkdir()
        release = "release-20261002"
        for role, target in (("store", self.store), ("documents", self.documents)):
            (target / IDENTITY_MARKER).write_text(json.dumps({
                "environment": "production", "role": role,
                "path": str(target), "release_id": release,
            }))
        identity = self.root / "identity.json"
        identity.write_text(json.dumps({
            "environment": "production", "authorized": True,
            "release_id": release, "git_commit": "abc123", "image_id": "sha256:123",
            "targets": {"db": str(self.db), "store": str(self.store),
                        "documents": str(self.documents)},
        }))
        with patch.dict(os.environ, {
                "TRADING_AI_ENV": "production",
                "TRADING_AI_RUNTIME_IDENTITY_FILE": str(identity)}, clear=False):
            assert_writable_targets(db=self.db, store=self.store, documents=self.documents)
            with self.assertRaisesRegex(RuntimeError, "exact runtime identity"):
                assert_writable_targets(
                    db=self.root / "other.db", store=self.store, documents=self.documents
                )

    def test_copied_production_marker_needs_destination_bound_clone_identity(self):
        self.store.mkdir()
        marker = self.store / IDENTITY_MARKER
        marker.write_text(json.dumps({"environment": "production"}))
        with self.assertRaisesRegex(RuntimeError, "unverified production marker"):
            self.call()
        clone = self.root / "clone.json"
        clone.write_text(json.dumps({
            "environment": "test", "clone_id": "restored-fixture-1",
            "root": str(self.root),
            "source_marker_sha256": hashlib.sha256(marker.read_bytes()).hexdigest(),
        }))
        self.call(TRADING_AI_CLONE_IDENTITY_FILE=str(clone))


class RuntimeEntryPointTests(unittest.TestCase):
    def run_module(self, *args, environment=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ.copy()
            env.update({
                "PYTHONDONTWRITEBYTECODE": "1",
                "TRADING_AI_ENV": environment,
                "SCRAPER_STORE_PATH": str(root / "store"),
                "SCRAPER_DB_PATH": str(root / "state" / "scraper.db"),
                "SCRAPER_DOCUMENT_STORE_PATH": str(root / "documents"),
            })
            result = subprocess.run(
                [sys.executable, *args], cwd=Path(__file__).resolve().parents[2],
                env=env, text=True, capture_output=True, timeout=30,
            )
            return result, root.joinpath("state", "scraper.db").exists(), root.joinpath("store").exists()

    def test_cli_help_is_inert_without_environment_identity(self):
        result, db_exists, store_exists = self.run_module("-m", "scrapper.cli", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(db_exists)
        self.assertFalse(store_exists)

    def test_direct_cli_refuses_before_database_or_directory_creation(self):
        result, db_exists, store_exists = self.run_module(
            "-m", "scrapper.cli", "init-db"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRADING_AI_ENV", result.stdout + result.stderr)
        self.assertFalse(db_exists)
        self.assertFalse(store_exists)

    def test_container_worker_entry_refuses_before_database_creation(self):
        result, db_exists, store_exists = self.run_module(
            "-m", "scrapper.cli", "run-document-download-worker", "--all-scopes",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRADING_AI_ENV", result.stdout + result.stderr)
        self.assertFalse(db_exists)
        self.assertFalse(store_exists)

    def test_host_browser_start_refuses_before_runtime_directory_creation(self):
        result, db_exists, store_exists = self.run_module(
            "-m", "scrapper.commands.browser_rating_host", "start"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRADING_AI_ENV", result.stdout + result.stderr)
        self.assertFalse(db_exists)
        self.assertFalse(store_exists)


if __name__ == "__main__":
    unittest.main()
