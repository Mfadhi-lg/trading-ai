"""Explicit current-native or checksummed frozen research input, never fallback."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import time


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def observation(db=None, *, frozen_sha256=None, observed_at=None):
    """Read one verified MVCC observation or one explicitly identified cold file.

    frozen_sha256 and observed_at are required together with an explicit db.
    Observation time describes input capture, never historical event availability.
    """
    from scrapper.db.database import selected_backend, connect
    conn = None
    frozen = frozen_sha256 is not None
    try:
        if frozen:
            if db is None or not observed_at:
                raise ValueError('Frozen input requires path, SHA256 and observation time')
            stamp = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Frozen observation time requires a timezone')
            path = Path(db).resolve()
            def verify():
                if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
                    raise ValueError('Frozen input must be a sidecar-free cold snapshot')
                if file_sha256(path) != frozen_sha256:
                    raise ValueError('Frozen input SHA256 mismatch')
            verify()
            conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            deadline = time.monotonic() + 20
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            versions = [r[0] for r in conn.execute('SELECT version FROM schema_migrations ORDER BY version')]
            source = dict(mode='frozen_sqlite_snapshot', sha256=frozen_sha256,
                          schema_versions=versions, observed_at_utc=stamp.astimezone(timezone.utc).isoformat())
        else:
            if observed_at is not None or db is not None:
                raise ValueError('A SQLite path requires explicit frozen SHA256 and observation time')
            if selected_backend() != 'postgresql':
                raise RuntimeError('Current research input requires explicit PostgreSQL configuration; no retained SQLite fallback')
            conn = connect(read_only=True)
            conn.raw.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.raw.execute("SELECT set_config('statement_timeout','20000',true)")
            versions = [r[0] for r in conn.execute('SELECT version FROM _postgresql_migrations ORDER BY version')]
            source = dict(mode='native_current_observation', schema_versions=versions,
                          target_id=os.environ['TRADING_AI_POSTGRESQL_TARGET_ID'], schema=conn.schema,
                          observed_at_utc=conn.execute('SELECT CURRENT_TIMESTAMP::text').fetchone()[0])
        source['historical_availability'] = 'NOT_INFERRED_FROM_OBSERVATION_TIME; retrospective membership is not point-in-time'
        yield conn, source
        if frozen:
            verify()
    finally:
        if conn is not None:
            conn.close()
