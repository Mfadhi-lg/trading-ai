#!/usr/bin/env python3
"""Demonstrate why failed additive migration must not be blindly resumed."""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scrapper.db import database

with tempfile.TemporaryDirectory(prefix='ar011-interruption-', dir='/private/tmp') as directory:
    root = Path(directory)
    migrations = root / 'migrations'
    migrations.mkdir()
    migration = '069_worker_claim_fencing.sql'
    shutil.copyfile(ROOT / 'scrapper/db/migrations' / migration, migrations / migration)
    baseline = root / 'baseline.db'
    conn = sqlite3.connect(baseline)
    for table in ('artifact_download_queue', 'credit_rating_download_queue', 'browser_rating_recovery_jobs'):
        conn.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
    conn.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at_utc TEXT NOT NULL)')
    conn.commit()
    conn.close()
    interrupted = root / 'interrupted.db'
    shutil.copyfile(baseline, interrupted)
    conn = sqlite3.connect(interrupted)
    conn.row_factory = sqlite3.Row
    # Interrupt at the second ALTER; first statement was already successful.
    conn.set_authorizer(lambda action, arg1, arg2, db, trigger:
        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE and arg2 == 'credit_rating_download_queue'
        else sqlite3.SQLITE_OK)
    with patch.object(database, 'MIGRATIONS_PATH', migrations):
        try:
            database.apply_migrations(conn)
            raise AssertionError('failure injection did not fire')
        except sqlite3.DatabaseError as exc:
            injected_error = str(exc)
        conn.set_authorizer(None)
        assert 'claim_token' in [r[1] for r in conn.execute('PRAGMA table_info(artifact_download_queue)')]
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 0
        try:
            database.apply_migrations(conn)
            raise AssertionError('blind resume should fail')
        except sqlite3.OperationalError as exc:
            resume_error = str(exc)
            assert 'duplicate column' in resume_error
    conn.close()
    restored = root / 'restored.db'
    shutil.copyfile(baseline, restored)
    conn = sqlite3.connect(restored)
    conn.row_factory = sqlite3.Row
    with patch.object(database, 'MIGRATIONS_PATH', migrations):
        database.apply_migrations(conn)
        database.check_schema_compatibility(conn)
    assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 1
    conn.close()
    result = {'claim_type': 'CONTROLLED_FIXTURE', 'migration': migration,
              'injected_error': injected_error, 'partial_schema_persists': True,
              'ledger_rows_after_failure': 0, 'blind_resume_error': resume_error,
              'recreated_baseline_apply': 'PASS',
              'recovery_rule': 'Stop all writers; preserve failed state; restore verified pre-change snapshot before retry. No automatic skip/renumber/replay.',
              'production_access': 'none'}
    (Path(__file__).parent / 'recovery-fixture.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
