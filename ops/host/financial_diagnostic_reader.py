"""Bounded financial observations; native MVCC or explicit frozen SQLite."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import time

import postgresql_runtime
from sqlite_sidecar_guard import _assert_safe_empty_wal, snapshot
from acquisition_live_reader import wall_clock_budget


@contextmanager
def observation(db, *, sqlite_snapshot=False):
    from scrapper.db.database import selected_backend, check_schema_compatibility
    native = selected_backend() == 'postgresql' and not sqlite_snapshot
    with wall_clock_budget(25), postgresql_runtime.request_deadline(25):
        gate = None
        conn = None
        try:
            if native:
                conn = postgresql_runtime.read_connection(db)
                check_schema_compatibility(conn)
            else:
                # Preserve the installed cold-reader ownership gate. Explicit
                # offline snapshots outside the installation need no gate file.
                lock = Path('/activation/.monitor-readers.lock')
                if not sqlite_snapshot:
                    gate = lock.open('rb')
                    end = time.monotonic() + 5
                    while True:
                        try:
                            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= end:
                                raise RuntimeError('Financial SQLite reader gate busy')
                            time.sleep(.05)
                if sqlite_snapshot:
                    before = {'files': snapshot(db)}
                    if before['files']['main'] is None or any(
                            before['files'][suffix] is not None for suffix in ('-wal', '-shm', '-journal')):
                        raise RuntimeError('Explicit frozen SQLite requires an existing sidecar-free snapshot')
                else:
                    before = _assert_safe_empty_wal(db)
                import sqlite3
                conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA query_only=ON')
                conn.execute('PRAGMA temp_store=MEMORY')
            postgresql_runtime.query_deadline(conn, time.monotonic() + 20)
            yield conn, {'backend': 'postgresql' if native else 'sqlite',
                         'mode': 'native_current_observation' if native else 'frozen_sqlite_snapshot'}
            if not native and snapshot(db) != before['files']:
                raise RuntimeError('Database changed during diagnosis; discard results')
        finally:
            if conn is not None:
                conn.close()
            if gate is not None:
                gate.close()
