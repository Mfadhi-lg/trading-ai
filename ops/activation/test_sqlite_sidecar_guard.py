"""Actual SQLite WAL fixtures and lock conflicts; disposable files only."""
import importlib.util
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('sqlite_sidecar_guard',Path(__file__).with_name('sqlite_sidecar_guard.py'))
guard=importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


@unittest.skipUnless(sys.platform=='linux','Production VFS/lock layout is Linux; run these in the pinned image')
class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, *, pending=False):
        folder=Path(tempfile.mkdtemp(dir=self.root))
        original=folder/'original.db'
        c=sqlite3.connect(original)
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA wal_autocheckpoint=0')
        c.execute('CREATE TABLE records(value INTEGER)')
        c.execute('INSERT INTO records VALUES (1)'); c.commit()
        self.assertEqual(c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone(),(0,0,0))
        if pending:
            c.execute('INSERT INTO records VALUES (2)'); c.commit()
        copied=folder/'snapshot.db'
        # A stable disposable snapshot, including its WAL, while no writes occur.
        for suffix in ('','-wal','-shm'):
            shutil.copy2(Path(str(original)+suffix),Path(str(copied)+suffix))
        c.close()  # SQLite alone cleans the original fixture sidecars.
        return copied

    def test_clean_database_without_sidecars(self):
        db=self.root/'clean.db'
        c=sqlite3.connect(db); c.execute('CREATE TABLE t(x)'); c.commit(); c.close()
        self.assertEqual(guard.assert_safe_empty_wal(db)['status'],'SAFE_EMPTY_WAL')

    def test_clean_wal_database_without_retained_sidecars(self):
        db=self.root/'wal.db'
        c=sqlite3.connect(db); c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE t(x)'); c.commit(); c.close()
        self.assertEqual(guard.assert_safe_empty_wal(db)['status'],'SAFE_EMPTY_WAL')

    def test_stable_empty_wal_valid_shm_passes_without_modifications(self):
        db=self.fixture()
        self.assertEqual(Path(str(db)+'-wal').stat().st_size,0)
        self.assertEqual(Path(str(db)+'-shm').stat().st_size,32768)
        before=guard.snapshot(db)
        result=guard.assert_safe_empty_wal(db)
        self.assertEqual(result['wal_index']['mxFrame'],0)
        self.assertFalse(result['checkpoint_required'])
        self.assertEqual(before,guard.snapshot(db))

    def test_uncheckpointed_committed_rows_are_preserved_and_blocked(self):
        db=self.fixture(pending=True)
        before=guard.snapshot(db)
        with self.assertRaisesRegex(RuntimeError,'Nonempty WAL'):
            guard.assert_safe_empty_wal(db)
        c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
        self.assertEqual(c.execute('SELECT value FROM records ORDER BY value').fetchall(),[(1,),(2,)])
        c.close()
        self.assertEqual(before['main'],guard.snapshot(db)['main'])
        self.assertTrue(Path(str(db)+'-wal').exists())

    def test_header_only_nonempty_wal_does_not_pass_as_empty(self):
        db=self.fixture()
        Path(str(db)+'-wal').write_bytes(b'x'*32)
        with self.assertRaisesRegex(RuntimeError,'Nonempty WAL'):
            guard.assert_safe_empty_wal(db)

    def test_malformed_shm_size_stops(self):
        db=self.fixture(); Path(str(db)+'-shm').write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'SHM size'):
            guard.assert_safe_empty_wal(db)

    def test_mismatched_index_header_stops(self):
        db=self.fixture(); p=Path(str(db)+'-shm'); raw=bytearray(p.read_bytes()); raw[48]^=1; p.write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'inconsistent'):
            guard.assert_safe_empty_wal(db)

    def test_invalid_index_checksum_stops(self):
        db=self.fixture(); p=Path(str(db)+'-shm'); raw=bytearray(p.read_bytes())
        raw[8]^=1; raw[56]^=1; p.write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'invalid'):
            guard.assert_safe_empty_wal(db)

    def test_rollback_journal_needs_recovery_assessment(self):
        db=self.fixture(); Path(str(db)+'-journal').write_bytes(b'journal')
        with self.assertRaisesRegex(RuntimeError,'Rollback journal'):
            guard.assert_safe_empty_wal(db)

    def test_symbolic_link_sidecar_is_not_accepted(self):
        db=self.fixture(); p=Path(str(db)+'-journal'); p.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(RuntimeError,'regular file'):
            guard.assert_safe_empty_wal(db)

    def test_state_drift_during_check_stops(self):
        db=self.fixture(); before=guard.snapshot(db); changed={**before,'main':{**before['main'],'mtime_ns':1}}
        with patch.object(guard,'snapshot',side_effect=[before,changed]):
            with self.assertRaisesRegex(RuntimeError,'changed'):
                guard.assert_safe_empty_wal(db)

    def test_active_empty_wal_writer_or_reader_is_detected(self):
        for role in ('reader','writer'):
            with self.subTest(role=role):
                db=self.fixture()
                code="""import sqlite3,sys
p=sys.argv[1]; role=sys.argv[2]
c=sqlite3.connect('file:'+p+'?mode=ro',uri=True) if role=='reader' else sqlite3.connect(p)
c.execute('SELECT * FROM records').fetchall()
if role=='writer': c.execute('BEGIN IMMEDIATE')
print('ready',flush=True)
sys.stdin.readline()
c.rollback(); c.close()
"""
                process=subprocess.Popen([sys.executable,'-c',code,str(db),role],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(),'ready')
                    with self.assertRaisesRegex(RuntimeError,'Active SQLite'):
                        guard.assert_safe_empty_wal(db)
                finally:
                    process.stdin.write('close\n'); process.stdin.flush(); process.wait(timeout=10)
                    process.stdin.close(); process.stdout.close()


if __name__=='__main__':
    unittest.main()
