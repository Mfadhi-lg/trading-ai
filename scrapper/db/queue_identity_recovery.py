"""Prepare a NEW rehearsal copy with hash-authorized historical identities.

Never repairs the input, creates queue jobs, drops history or activates runtime.
A prepared copy still requires the normal verified export/import/restore gates.
"""
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .postgresql_schema import source_profile
from .postgresql_transfer import (_check_source_catalog, file_sha256, read_snapshot,
                                  sqlite_identifier, table_digest)

HISTORICAL_FK_SHA256 = "38fa5d8cb8f1ff8ea7b4fdca6046c3ab7e3414ff508306c93fcc512f55876439"
MIGRATION = "077_screener_queue_identities.sql"
PRESERVED_TABLES = ("screener_company_queue", "screener_company_queue_events",
                    "exchange_screener_enqueues", "schema_migrations")


def violation_sha256(rows):
    return hashlib.sha256(json.dumps(sorted(rows), separators=(",", ":")).encode()).hexdigest()


def _preserved(conn):
    return {name: table_digest(conn, name, list(conn.execute(
        "PRAGMA table_info(" + sqlite_identifier(name) + ")")), 1000)
        for name in PRESERVED_TABLES}


def prepare(source, target, *, expected_source_sha256,
            approved_orphan_sha256=HISTORICAL_FK_SHA256, progress=None):
    """Controlled API; caller explicitly authorizes a known violation-set hash."""
    target = Path(target)
    with read_snapshot(source) as original:
        if original.execute("PRAGMA journal_mode").fetchone()[0] != "delete" or any(
                Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("Recovery requires a standalone retained snapshot")
        # Pin before hashing; the rollback-journal reader prevents committed changes.
        profile = _check_source_catalog(original)
        if "screener_queue_identities" in profile["objects"]["table"]:
            raise RuntimeError("Source already has queue identities; use ordinary verification")
        if file_sha256(source) != expected_source_sha256:
            raise RuntimeError("Source snapshot checksum differs from authorization")
        violations = original.execute("PRAGMA foreign_key_check").fetchall()
        if not violations or violation_sha256(violations) != approved_orphan_sha256:
            raise RuntimeError("Foreign key identities differ from the approved historical set")
        if any(row[0] not in PRESERVED_TABLES[1:3] or row[2] != "screener_company_queue" for row in violations):
            raise RuntimeError("Recovery is limited to historical queue references")
        # Explicit aliases avoid correlated name shadowing in the missing-ID query.
        missing = [row[0] for row in original.execute("""SELECT refs.queue_item_id FROM (
            SELECT queue_item_id FROM screener_company_queue_events
            UNION SELECT queue_item_id FROM exchange_screener_enqueues WHERE queue_item_id IS NOT NULL) refs
            WHERE NOT EXISTS(SELECT 1 FROM screener_company_queue q WHERE q.queue_item_id=refs.queue_item_id)
            """)]
        before = _preserved(original)
        if progress:
            progress({"phase": "historical_identity_authorization", "violations": len(violations), "missing_ids": len(missing)})
        latest = source_profile()
        migration = Path(__file__).with_name("migrations") / MIGRATION
        content = migration.read_bytes()
        if hashlib.sha256(content).hexdigest() != latest["sqlite_sources"][MIGRATION]:
            raise RuntimeError("Recovery migration checksum mismatch")
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(target)) as candidate:
            original.backup(candidate, pages=256)
        if file_sha256(source) != expected_source_sha256:
            raise RuntimeError("Source snapshot changed during preparation")
    with closing(sqlite3.connect(target)) as candidate:
        candidate.execute("PRAGMA journal_mode=DELETE")
        # Use the exact DDL from the reviewed migration; only seed authorized
        # identities before its immutable registration guards are installed.
        ddl = content.decode().split("CREATE TABLE IF NOT EXISTS screener_queue_identities", 1)[1].split(";", 1)[0]
        candidate.execute("CREATE TABLE screener_queue_identities" + ddl)
        candidate.executemany("INSERT INTO screener_queue_identities VALUES(?,'historical_reference',?)",
                              ((key, approved_orphan_sha256) for key in missing))
        candidate.commit()
        allowed = set(PRESERVED_TABLES) | {name + "_077" for name in PRESERVED_TABLES[:3]} | {
            "screener_queue_identities", "sqlite_master", "sqlite_sequence", "sqlite_stat1", "sqlite_stat4", "sqlite_temp_master", "queue_identity_migration_guard"}
        def guard(action, table, column, database, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and table not in allowed:
                if progress:
                    progress({"phase": "denied_migration_write", "table": table})
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        candidate.set_authorizer(guard)
        try:
            candidate.executescript(content.decode())
        except BaseException:
            candidate.rollback()
            raise
        finally:
            candidate.set_authorizer(None)
            candidate.execute("PRAGMA foreign_keys=ON")
        after = _preserved(candidate)
        if after != before:
            raise RuntimeError("Original queue, event, enqueue or migration rows changed")
        if candidate.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Prepared copy has foreign key violations")
        _check_source_catalog(candidate)
        candidate.execute("INSERT INTO schema_migrations VALUES (?,?)", (MIGRATION, datetime.now(timezone.utc).isoformat()))
        candidate.commit()
        historical = candidate.execute("SELECT count(*) FROM screener_queue_identities WHERE identity_basis='historical_reference'").fetchone()[0]
    return {"status": "prepared_rehearsal_copy", "verified_export": False,
            "source_sha256": expected_source_sha256, "candidate_sha256": file_sha256(target),
            "approved_orphan_sha256": approved_orphan_sha256, "historical_identities": historical,
            "preserved_original_tables": before, "foreign_keys_valid": True,
            "other_record_writes_denied": True, "new_queue_jobs": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--allow-rehearsal-write', required=True, action='store_true')
    args = parser.parse_args(argv)
    with args.report.open('x') as handle:
        args.report.chmod(0o600)
        try:
            result = prepare(args.source, args.target, expected_source_sha256=args.source_sha256,
                             progress=lambda event: print(json.dumps(event), flush=True))
            code = 0
        except Exception as error:
            result = {'status': 'failed', 'error_type': type(error).__name__}
            code = 1
        json.dump(result, handle, indent=2)
        handle.write('\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
