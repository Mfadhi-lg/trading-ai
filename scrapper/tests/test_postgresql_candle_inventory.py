"""Synthetic separate-store transfer and native restore proof; no drive access."""
import ast
from contextlib import closing
from dataclasses import replace
import gzip
import hashlib
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest
from urllib.parse import urlsplit, urlunsplit
import uuid

from scrapper.db import candle_inventory_transfer as inventory
from scrapper.db.postgresql import connect
from scrapper.tests.test_postgresql_backup import client, disposable_settings, portable_ddl


def fixture(root):
    source = root / 'source.sqlite3'
    files = root / 'files'
    files.mkdir()
    raw = b'{"price": "000.00000000000000000001"}'
    (files / 'raw.gz').write_bytes(gzip.compress(raw, mtime=0))
    (files / 'bar.parquet').write_bytes(b'synthetic opaque parquet evidence')
    (files / 'receipt.json').write_bytes(b'{"retained":true}\n')
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript(inventory.DDL)
        for i, status in enumerate(('SUCCESS', 'RAW_SAVED', 'IN_FLIGHT', 'STOPPED', 'ISOLATED_FAILURE', 'QUARANTINED_RESPONSE')):
            conn.execute('INSERT INTO requests(request_id,status,attempts,elapsed_seconds,raw_path,raw_sha256) VALUES (?,?,?,?,?,?)',
                         (str(i), status, 0 if i == 0 else None, -0.0 if i == 0 else 0.1, 'raw.gz' if i == 0 else None, hashlib.sha256(raw).hexdigest() if i == 0 else None))
        conn.execute('INSERT INTO datasets(dataset_id,request_id,parquet_path,parquet_sha256,bytes,completeness) VALUES (?,?,?,?,?,?)',
                     ('d', '0', 'bar.parquet', inventory.file_sha256(files / 'bar.parquet'), (files / 'bar.parquet').stat().st_size, 'SUPERSEDED; retained'))
        conn.execute("INSERT INTO revisions VALUES ('0','old','new','2026-11-15T00:00:00+05:30')")
        conn.execute("INSERT INTO candle_observations(instrument_key,timeframe,definition,start,observed_at,dataset_id) VALUES ('NSE|x','1d','native','2026-11-01','2026-11-15','d')")
        conn.commit()
    return source, files


class InventorySourceTests(unittest.TestCase):
    def test_baseline_matches_actual_archive_bootstrap(self):
        from experiments.historical_candle_archive_v1 import storage
        tree = ast.parse(Path(storage.__file__).read_text())
        ddl = next(n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'executescript')
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript(ddl)
            self.assertEqual(set(inventory.source_tables(conn)), {'requests', 'datasets', 'revisions', 'candle_observations'})

    def test_snapshot_references_drift_corruption_and_immutability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, files = fixture(root)
            before = inventory.file_sha256(source), inventory.file_inventory(files)
            snapshot = root / 'snapshot.sqlite3'
            report = inventory.export_snapshot(source, snapshot, files, batch_size=1)
            self.assertEqual(report['tables']['requests']['rows'], 6)
            self.assertEqual(report['unresolved_lineage']['revision_previous_not_in_requests'], 1)
            self.assertEqual(before, (inventory.file_sha256(source), inventory.file_inventory(files)))
            with self.assertRaises(FileExistsError):
                inventory.export_snapshot(source, snapshot, files)
            with closing(sqlite3.connect(snapshot)) as conn:
                conn.execute('CREATE TABLE unexpected(id INTEGER)')
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
                    inventory.source_tables(conn)
            with closing(sqlite3.connect(source)) as conn:
                conn.execute('INSERT INTO revisions VALUES (NULL,NULL,NULL,NULL)')
                with self.assertRaisesRegex(RuntimeError, 'NULL inventory key'):
                    inventory.source_tables(conn)
            (files / 'bar.parquet').write_bytes(b'corrupt')
            with inventory.read_snapshot(root / 'source.sqlite3') as conn:
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    inventory.reference_check(conn, files, inventory.file_inventory(files))

    def test_wal_backup_and_standalone_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, files = fixture(root)
            with closing(sqlite3.connect(source)) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute("UPDATE requests SET attempts=2 WHERE request_id='2'")
                writer.commit()
                before = inventory.file_sha256(source)
                snapshot = root / 'snapshot.sqlite3'
                inventory.export_snapshot(source, snapshot, files)
                self.assertEqual(before, inventory.file_sha256(source))
                with inventory.read_snapshot(snapshot) as conn:
                    self.assertEqual(conn.execute("SELECT attempts FROM requests WHERE request_id='2'").fetchone(), (2,))
                with self.assertRaisesRegex(RuntimeError, 'Standalone'):
                    inventory.transfer(None, source, 'test', files, expected_sha256=before)

    def test_authorization_and_bounds_fail_before_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            source, files = fixture(Path(directory))
            for batch in (0, 100001, True):
                with self.assertRaises(ValueError):
                    inventory.transfer(None, source, 'test', files, expected_sha256='wrong', batch_size=batch)
            with self.assertRaisesRegex(RuntimeError, 'Unauthorized'):
                inventory.transfer(None, source, 'test', files, expected_sha256='wrong')


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'), 'requires disposable PostgreSQL 16')
class InventoryNativeTests(unittest.TestCase):
    def test_transfer_retry_interruption_corruption_backup_restore(self):
        import psycopg
        from psycopg import sql
        settings = disposable_settings(os.environ)
        schema = 'candle_test_' + uuid.uuid4().hex
        database = 'candle_restore_' + uuid.uuid4().hex
        restored = replace(settings, dsn=urlunsplit(urlsplit(settings.dsn)._replace(path='/' + database)))
        with psycopg.connect(settings.dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, files = fixture(root)
                before = inventory.file_sha256(source), inventory.file_inventory(files)
                snapshot = root / 'snapshot.sqlite3'
                report = inventory.export_snapshot(source, snapshot, files, batch_size=1)
                with connect(settings) as conn:
                    inventory.migrate(conn, schema)
                def run(**kwargs):
                    return inventory.transfer(settings, snapshot, schema, files, expected_sha256=report['snapshot_sha256'], batch_size=1, **kwargs)
                def interrupt(*args):
                    raise RuntimeError('injected interruption')
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    run(progress=interrupt)
                with connect(settings) as conn:
                    for name in report['tables']:
                        self.assertEqual(conn.execute(sql.SQL('SELECT count(*) AS n FROM {}.{}').format(sql.Identifier(schema), sql.Identifier(name))).fetchone()['n'], 0)
                # File corruption must fail before destination rows are committed.
                retained = (files / 'bar.parquet').read_bytes()
                (files / 'bar.parquet').write_bytes(b'bad')
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    run()
                (files / 'bar.parquet').write_bytes(retained)
                # A source file change during an import rolls back the target.
                receipt = (files / 'receipt.json').read_bytes()
                def alter_file(*args):
                    (files / 'receipt.json').write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'archive changed'):
                    run(progress=alter_file)
                (files / 'receipt.json').write_bytes(receipt)
                with connect(settings) as conn:
                    self.assertEqual(conn.execute(sql.SQL('SELECT count(*) AS n FROM {}.datasets').format(sql.Identifier(schema))).fetchone()['n'], 0)
                expected = run()
                self.assertEqual(expected, run())
                self.assertEqual(before, (inventory.file_sha256(source), inventory.file_inventory(files)))
                archive = root / 'native.dump'
                client('pg_dump', settings, '--format=custom', '--no-owner', '--no-privileges', '--schema=' + schema, '--file=' + str(archive))
                client('pg_restore', restored, '--dbname=' + database, '--exit-on-error', '--no-owner', '--no-privileges', str(archive))
                args = ('--schema-only', '--no-owner', '--no-privileges', '--schema=' + schema)
                self.assertEqual(portable_ddl(client('pg_dump', settings, *args)), portable_ddl(client('pg_dump', restored, *args)))
                restored_files = root / 'restored-files'
                shutil.copytree(files, restored_files)
                self.assertEqual(expected, inventory.transfer(restored, snapshot, schema, restored_files, expected_sha256=report['snapshot_sha256']))
                with connect(settings) as conn:
                    conn.execute(sql.SQL('ALTER TABLE {}.requests ENABLE ROW LEVEL SECURITY').format(sql.Identifier(schema)))
                with self.assertRaisesRegex(RuntimeError, 'schema drift'):
                    run()
                with connect(settings) as conn:
                    conn.execute(sql.SQL('ALTER TABLE {}.requests DISABLE ROW LEVEL SECURITY').format(sql.Identifier(schema)))
                with connect(restored) as conn:
                    conn.execute(sql.SQL("UPDATE {}.requests SET status='SUCCESS' WHERE request_id='2'").format(sql.Identifier(schema)))
                with self.assertRaisesRegex(RuntimeError, 'content mismatch'):
                    inventory.transfer(restored, snapshot, schema, files, expected_sha256=report['snapshot_sha256'])
                with connect(settings) as conn:
                    conn.execute(sql.SQL('DROP INDEX {}.request_gaps').format(sql.Identifier(schema)))
                with self.assertRaisesRegex(RuntimeError, 'schema drift'):
                    run()
                self.assertEqual(before, (inventory.file_sha256(source), inventory.file_inventory(files)))
        finally:
            with connect(settings) as conn:
                conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
            with psycopg.connect(settings.dsn, autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
