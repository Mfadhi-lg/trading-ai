"""Explicit, transactional PostgreSQL lineage independent of SQLite migrations."""
import hashlib
import json
from pathlib import Path
import re

MIGRATIONS = Path(__file__).with_name("postgresql_migrations")


def validate_schema_name(schema):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) or schema == "public" or schema.startswith(("pg_", "information_schema")):
        raise ValueError("Use a dedicated application schema with a lowercase identifier")


def manifest():
    return json.loads((MIGRATIONS / "manifest.json").read_text())


def source_profile():
    """The new source lineage is separate from the frozen PG001 baseline."""
    return json.loads((MIGRATIONS / "sqlite_077_profile.json").read_text())


def application_objects():
    return source_profile()["objects"]


def migration_files():
    expected = manifest()["migrations"]
    found = {path.name: path for path in MIGRATIONS.glob("*.sql")}
    if set(found) != set(expected):
        raise RuntimeError("PostgreSQL migration file inventory differs from manifest")
    for name, path in sorted(found.items()):
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected[name]:
            raise RuntimeError(f"PostgreSQL migration checksum mismatch: {name}")
        yield name, expected[name], content.decode()


def schema_fingerprint(conn, schema, *, format_version=1):
    """Hash live structure, including constraints and enabled trigger functions.

    Excludes rows, sequence positions and the lineage table. The signature is
    recorded only after a reviewed migration; normal checks never refresh it.
    """
    queries = (
        "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname<>'_postgresql_migrations' AND c.relname<>'_postgresql_migrations_pkey' ORDER BY c.relname",
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,a.attcollation,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname=%s AND c.relkind IN ('r','v') AND c.relname<>'_postgresql_migrations' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "SELECT t.relname,c.conname,c.contype,c.convalidated,c.condeferrable,c.condeferred,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname<>'_postgresql_migrations' ORDER BY t.relname,c.conname",
        "SELECT t.relname,i.relname,x.indisvalid,x.indisready,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname<>'_postgresql_migrations' ORDER BY t.relname,i.relname",
        "SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,t.tgname",
        "SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY p.proname,p.oid",
        "SELECT c.relname,pg_get_viewdef(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='v' ORDER BY c.relname",
        "SELECT c.relname,s.seqtypid,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle,t.relname,a.attname,d.deptype FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_depend d ON d.objid=c.oid AND d.deptype IN ('a','i') LEFT JOIN pg_class t ON t.oid=d.refobjid LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid WHERE n.nspname=%s ORDER BY c.relname",
    )
    if format_version not in (1,2):
        raise ValueError("Unsupported schema fingerprint format")
    if format_version == 2:
        # Only server-generated RI trigger identifiers are allocation-dependent.
        # Pin their semantic constraint identity, event/function definition,
        # enabled state and deferrability; user trigger names remain exact.
        portable_triggers = """SELECT relation.relname,
            CASE WHEN trigger.tgisinternal AND constraint_info.contype='f'
                AND trigger.tgname ~ '^RI_ConstraintTrigger_[ac]_[0-9]+$' THEN constraint_relation.relname || ':' || constraint_info.conname
                || ':' || function.proname || ':' || trigger.tgtype::text ELSE trigger.tgname::text END AS stable_name,
            trigger.tgenabled,
            CASE WHEN trigger.tgisinternal AND constraint_info.contype='f'
                AND trigger.tgname ~ '^RI_ConstraintTrigger_[ac]_[0-9]+$' THEN regexp_replace(pg_get_triggerdef(trigger.oid),
                '^CREATE CONSTRAINT TRIGGER "RI_ConstraintTrigger_[ac]_[0-9]+" ',
                'CREATE CONSTRAINT TRIGGER "<internal-ri>" ')
                ELSE pg_get_triggerdef(trigger.oid) END,
            constraint_namespace.nspname,constraint_relation.relname,constraint_info.conname,
            function_namespace.nspname,trigger.tgisinternal,
            trigger.tgdeferrable,trigger.tginitdeferred
            FROM pg_trigger trigger JOIN pg_class relation ON relation.oid=trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
            JOIN pg_proc function ON function.oid=trigger.tgfoid
            JOIN pg_namespace function_namespace ON function_namespace.oid=function.pronamespace
            LEFT JOIN pg_constraint constraint_info ON constraint_info.oid=trigger.tgconstraint
            LEFT JOIN pg_class constraint_relation ON constraint_relation.oid=constraint_info.conrelid
            LEFT JOIN pg_namespace constraint_namespace ON constraint_namespace.oid=constraint_info.connamespace
            WHERE namespace.nspname=%s
            ORDER BY relation.relname,stable_name,constraint_relation.relname,constraint_info.conname"""
        queries = (*queries[:4], portable_triggers, *queries[5:])
    from psycopg.rows import tuple_row
    validate_schema_name(schema)
    previous_path = conn.execute("SELECT current_setting('search_path') AS path").fetchone()["path"]
    conn.execute("SELECT set_config('search_path', %s, true)", (f'"{schema}", pg_catalog, pg_temp',))
    structure = []
    try:
        with conn.cursor(row_factory=tuple_row) as cursor:
            for index, query in enumerate(queries):
                rows=cursor.execute(query, (schema,)).fetchall()
                if format_version == 2 and index == 4:
                    # Canonical full-row ordering also avoids locale-dependent
                    # ties. Preserve every row and enforcement field exactly.
                    rows.sort(key=lambda row: json.dumps(row,ensure_ascii=True,separators=(",", ":")))
                structure.append(rows)
    finally:
        conn.execute("SELECT set_config('search_path', %s, true)", (previous_path,))
    return hashlib.sha256(json.dumps(structure, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()



def _fingerprint_format(version):
    return 2 if version >= "005_portable_schema_fingerprint.sql" else 1


def migrate(conn, schema):
    """Apply reviewed files only on explicit invocation; caller commits/rolls back.

    Requires a managed transaction (see postgresql.connect). Creates only the
    dedicated schema, never changes database/role settings or SQLite lineage.
    """
    from psycopg import sql
    validate_schema_name(schema)
    files = list(migration_files())  # Validate all bytes before executing DDL.
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("trading-ai-schema:" + schema,))
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)))
        conn.execute("""CREATE TABLE IF NOT EXISTS _postgresql_migrations (
            version text PRIMARY KEY, sha256 text NOT NULL, schema_sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        applied = {row["version"]: row["sha256"] for row in conn.execute("SELECT version, sha256 FROM _postgresql_migrations")}
        expected = {name: checksum for name, checksum, _ in files}
        if any(expected.get(name) != checksum for name, checksum in applied.items()):
            raise RuntimeError("Unknown or changed PostgreSQL migration lineage")
        if applied:
            check_compatibility(conn, schema, allow_pending=True)
        for name, checksum, content in files:
            if name in applied:
                continue
            conn.execute(content, prepare=False)
            signature = schema_fingerprint(conn, schema, format_version=_fingerprint_format(name))
            conn.execute("INSERT INTO _postgresql_migrations(version, sha256, schema_sha256) VALUES (%s, %s, %s)", (name, checksum, signature))


def check_compatibility(conn, schema, *, allow_pending=False):
    """Read-only checksum/identity check; never applies migrations."""
    from psycopg import sql
    validate_schema_name(schema)
    expected = {name: checksum for name, checksum, _ in migration_files()}
    rows = conn.execute(sql.SQL("SELECT version, sha256, schema_sha256 FROM {}._postgresql_migrations ORDER BY version").format(sql.Identifier(schema))).fetchall()
    compared = dict(sorted(expected.items())[:len(rows)]) if allow_pending else expected
    if {row["version"]: row["sha256"] for row in rows} != compared:
        raise RuntimeError("PostgreSQL schema is incompatible; explicit migration review required")
    if not rows or schema_fingerprint(conn, schema, format_version=_fingerprint_format(rows[-1]["version"])) != rows[-1]["schema_sha256"]:
        raise RuntimeError("PostgreSQL schema structure drifted from its reviewed migration")
