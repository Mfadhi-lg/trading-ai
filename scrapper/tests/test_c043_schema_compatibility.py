"""Reconcile browser and A2 histories without replay or loss of access evidence."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scrapper.db import database
from scrapper.config import MIGRATIONS_PATH

NEW = '068_exchange_source_access_compatibility.sql'
HISTORICAL = '065_exchange_source_access.sql'

class SchemaCompatibilityTests(unittest.TestCase):
    def test_existing_a2_evidence_and_browser_schema_survive_explicit_upgrade(self):
        with tempfile.TemporaryDirectory() as root:
            migrations = Path(root)/'migrations'
            migrations.mkdir()
            for source in MIGRATIONS_PATH.glob('*.sql'):
                if source.name != NEW:
                    (migrations/source.name).write_bytes(source.read_bytes())
            conn = sqlite3.connect(Path(root)/'clone.db')
            conn.row_factory = sqlite3.Row
            with patch.object(database, 'MIGRATIONS_PATH', migrations):
                database.apply_migrations(conn)
            # Historically applied A2 layout on a browser-capable baseline.
            sql = (MIGRATIONS_PATH/NEW).read_text()
            conn.executescript(sql)
            conn.execute('INSERT INTO schema_migrations VALUES (?, ?)',(HISTORICAL,'historical'))
            conn.executemany('INSERT INTO exchange_source_access_events(dataset,action,event_at_utc,evidence) VALUES (?,?,?,?)',[
                ('bse_security_master','BLOCKED','2026-08-01','403'),
                ('bse_security_master','RESUMED','2026-08-02','approved')])
            conn.commit()
            events = [tuple(r) for r in conn.execute('SELECT * FROM exchange_source_access_events')]
            browser = [tuple(r) for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE '%browser%' ORDER BY name")]
            with patch.object(database, 'MIGRATIONS_PATH', MIGRATIONS_PATH):
                with self.assertRaisesRegex(RuntimeError,NEW): database.init_db(conn)
                database.init_db(conn,apply_migrations=True)
                database.init_db(conn)
                ledger = [tuple(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
                database.apply_migrations(conn)
                self.assertEqual(ledger,[tuple(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')])
            self.assertEqual(events,[tuple(r) for r in conn.execute('SELECT * FROM exchange_source_access_events')])
            self.assertEqual(browser,[tuple(r) for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE '%browser%' ORDER BY name")])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations WHERE version IN (?,?)',(HISTORICAL,'065_operator_browser_rating_recovery.sql')).fetchone()[0],2)
            conn.close()

    def test_fresh_schema_has_new_identity_and_never_fabricates_historical_065(self):
        conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
        database.apply_migrations(conn)
        versions={r[0] for r in conn.execute('SELECT version FROM schema_migrations')}
        self.assertIn(NEW,versions); self.assertNotIn(HISTORICAL,versions)
        self.assertIn('065_operator_browser_rating_recovery.sql',versions)
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        conn.close()

    def test_cli_preserves_current_browser_and_scrape_defaults(self):
        from scrapper.cli import _parser
        parser = _parser()
        args = parser.parse_args(['scrape-one','--source','screener','--symbol','ABC'])
        self.assertEqual(args.scope, 'standalone')
        resume = parser.parse_args(['exchange-scheduler','--resume-access','bse_security_master','--resume-evidence','approved'])
        self.assertEqual(resume.resume_evidence, 'approved')
        self.assertIn('screener-period-coverage', parser._subparsers._group_actions[0].choices)

    def test_status_uses_read_only_connection_and_has_no_schema_write(self):
        from scrapper.pipeline import scheduler
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'status.db'
            conn=sqlite3.connect(path); conn.row_factory=sqlite3.Row
            database.apply_migrations(conn); conn.close()
            before=path.read_bytes()
            with patch.object(scheduler, 'connect', wraps=database.connect) as connect:
                result = scheduler.day_status('2026-08-06',db_path=path)
            connect.assert_called_once_with(path,read_only=True)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(result['current_access_holds'],[])
