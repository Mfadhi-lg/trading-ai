"""Explicit document observations using the existing restricted reader boundary."""
from contextlib import contextmanager
import json
from pathlib import Path

from financial_diagnostic_reader import observation as read_observation


@contextmanager
def observation(db, *, baseline_required=False, sqlite_snapshot=False):
    from scrapper.db.database import selected_backend
    from production_ownership import assert_no_conflicting_leases
    from datetime import datetime, timezone
    native = selected_backend() == 'postgresql' and not sqlite_snapshot
    baseline = None
    if baseline_required and not native:
        baseline = json.loads(Path('/activation/production-baseline.json').read_text())
        if Path(db).stat().st_ino != baseline['db_after']['inode']:
            raise RuntimeError('Database identity differs')
    with read_observation(db, sqlite_snapshot=sqlite_snapshot) as (conn, source):
        if baseline is not None:
            from scrapper.db.database import check_schema_compatibility
            conn.execute('BEGIN')
            check_schema_compatibility(conn)
        if baseline is not None and [dict(r) for r in conn.execute(
                'SELECT * FROM schema_migrations ORDER BY version')] != baseline['ledger']:
            raise RuntimeError('Migration ledger differs')
        assert_no_conflicting_leases(conn, now=datetime.now(timezone.utc).isoformat())
        source = {**source, 'observed_at_utc':datetime.now(timezone.utc).isoformat()}
        if native:
            import hashlib, os
            source['target_identifier']='sha256:'+hashlib.sha256(
                os.environ['TRADING_AI_POSTGRESQL_TARGET_ID'].encode()).hexdigest()
        else:
            path = Path(db).resolve(strict=True)
            source.update(snapshot_path=str(path), snapshot_mtime_utc=datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat())
        yield conn, source


def exact_ids(ids, maximum):
    if (not isinstance(ids, list) or not 1 <= len(ids) <= maximum
            or any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError(f'Exact one-to-{maximum} unique positive job identities required')
    return ids


def container_command(fallback, *, evidence, store=None, campaign=None):
    """Use the sealed native image/socket/reader credentials; retain frozen transport."""
    from postgresql_release import target, verify, reader_prefix
    if target() is None:
        return fallback
    verify()
    prefix = reader_prefix()
    # Additional outputs are the only writable mounts. Source and documents
    # remain read-only. This is not a general monitor ownership exemption.
    mounts = ['--mount', f'type=bind,source={evidence},target=/evidence']
    if store is not None:
        mounts += ['--mount', f'type=bind,source={store},target=/documents,readonly']
    if campaign is not None:
        mounts += ['--mount', f'type=bind,source={campaign},target=/campaign,readonly']
    tail = fallback[fallback.index('--entrypoint') + 3:]
    boundary=prefix.index('--entrypoint')
    return ['docker', *prefix[:boundary], *mounts, *prefix[boundary:], *tail]
