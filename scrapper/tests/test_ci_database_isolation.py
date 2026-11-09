"""Static contract tests for CI database and storage isolation."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "migration-lineage.yml",
    ROOT / ".github" / "workflows" / "ml-readiness.yml",
    ROOT / ".github" / "workflows" / "postgresql.yml",
)
ISOLATED_PATH_KEYS = (
    "SCRAPER_STORE_PATH",
    "SCRAPER_FILES_PATH",
    "SCRAPER_SNAPSHOTS_PATH",
    "SCRAPER_EXPORTS_PATH",
    "SCRAPER_DB_PATH",
    "SCRAPER_CAMPAIGNS_PATH",
    "SCRAPER_LOGS_PATH",
    "SCRAPER_BROWSER_STATE_PATH",
    "SCRAPER_BACKUPS_PATH",
)


class CiDatabaseIsolationTests(unittest.TestCase):
    def test_database_writing_workflows_use_test_identity_and_job_temp_root(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                content = workflow.read_text(encoding="utf-8")
                self.assertIn("TRADING_AI_ENV: test", content)
                self.assertIn('test_root="$RUNNER_TEMP/trading-ai-test"', content)
                self.assertIn('mkdir -p "$test_root/state" "$test_root/store"', content)
                self.assertIn('echo "TRADING_AI_TEST_ROOT=$test_root"', content)
                for key in ISOLATED_PATH_KEYS:
                    self.assertIn(f'echo "{key}=$test_root/', content)

    def test_ml_readiness_initializes_the_configured_database(self):
        workflow = (ROOT / ".github" / "workflows" / "ml-readiness.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("run: python -m scrapper.cli init-db", workflow)
        self.assertNotIn("--db /tmp/", workflow)

    def test_document_store_defaults_under_the_isolated_snapshot_root(self):
        config = (ROOT / "scrapper" / "config.py").read_text(encoding="utf-8")
        self.assertIn('DOCUMENT_STORE = Path(', config)
        self.assertIn('os.environ.get("SCRAPER_DOCUMENT_STORE_PATH", str(SNAPSHOTS))', config)
        for workflow in WORKFLOWS:
            content = workflow.read_text(encoding="utf-8")
            self.assertIn('echo "SCRAPER_SNAPSHOTS_PATH=$test_root/', content)
            self.assertNotIn("SCRAPER_DOCUMENT_STORE_PATH:", content)


if __name__ == "__main__":
    unittest.main()
