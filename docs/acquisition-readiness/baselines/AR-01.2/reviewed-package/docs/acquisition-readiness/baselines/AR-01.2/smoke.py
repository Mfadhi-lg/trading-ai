"""Runs ONLY inside the candidate image on ephemeral test storage."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, '/app')

manifest = json.loads(Path('/proof/source-manifest.json').read_text())
checked = 0
for item in manifest['files']:
    if not item['path'].startswith('scrapper/'):
        continue
    path = Path('/app') / item['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], item['path']
    checked += 1
from scrapper.db.database import connect, apply_migrations, check_schema_compatibility
database = Path('/fixture/scraper.db')
conn = connect(database)
apply_migrations(conn)
check_schema_compatibility(conn)
assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 75
assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='document_processing_saved'").fetchone()[0] == 1
conn.close()
for module in ['scrapper.cli', 'scrapper.commands.quarter_processing_role',
               'scrapper.commands.browser_rating_recovery']:
    subprocess.run(['python', '-m', module, '--help'], check=True, stdout=subprocess.DEVNULL)
subprocess.run(['python', '-m', 'pip', 'check'], check=True)
subprocess.run(['python', '-m', 'unittest',
    'scrapper.tests.test_database_migration_controls',
    'scrapper.tests.test_c043_schema_compatibility',
    'scrapper.tests.test_runtime_identity.RuntimeIdentityTests',
    'scrapper.tests.test_runtime_identity.RuntimeEntryPointTests.test_cli_help_is_inert_without_environment_identity',
    'scrapper.tests.test_runtime_identity.RuntimeEntryPointTests.test_direct_cli_refuses_before_database_or_directory_creation',
    'scrapper.tests.test_runtime_identity.RuntimeEntryPointTests.test_container_worker_entry_refuses_before_database_creation',
    '-v'], check=True)
from scrapper.runtime_identity import assert_writable_targets
try:
    os.environ['TRADING_AI_ENV'] = 'production'
    assert_writable_targets(db=database, store=Path('/fixture'), documents=Path('/fixture/documents'))
except RuntimeError as exc:
    assert 'authorized exact runtime identity' in str(exc)
else:
    raise AssertionError('unidentified production write was allowed')
finally:
    os.environ['TRADING_AI_ENV'] = 'test'
# Exercise the image's HEALTHCHECK query against disposable state.
c = sqlite3.connect('file:/fixture/scraper.db?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
assert c.execute('SELECT 1').fetchone()[0] == 1
c.close()
print(json.dumps({'status': 'PASS', 'source_files_verified': checked,
    'migration_count': 75, 'processing_trigger': True, 'cli_help': True,
    'pip_check': True, 'production_identity_fails_closed': True,
    'sqlite_health_query': True, 'network': 'none', 'production_mounts': []}))
