"""Small execution helpers for explicitly ported repository statements.

These helpers bind parameters; they do not translate SQL dialects, route the
application backend, emulate SQLite connections, or run migrations. Callers
must choose native SQL explicitly when semantics differ. PostgreSQL callers own
the managed transaction supplied by ``postgresql.connect`` or a pool lease.
"""
from contextlib import contextmanager
import re
import sys
from functools import wraps


def is_postgresql(conn):
    # Optional dependency stays lazy; SQLite installations need no driver.
    driver = sys.modules.get("psycopg")
    return getattr(type(conn), "_postgresql_application", False) is True or (
        driver is not None and isinstance(conn, driver.Connection))


def statement(conn, sqlite_sql, postgresql_sql):
    return postgresql_sql if is_postgresql(conn) else sqlite_sql


def require_transaction(conn):
    if is_postgresql(conn):
        if getattr(conn, "_postgresql_application", False):
            conn._write_lock()
            return
        from psycopg.pq import TransactionStatus
        if conn.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Repository requires a managed PostgreSQL transaction")


def _parameters(sql):
    """Bind qmarks outside quoted SQL/comments, escaping psycopg literal %.

    This deliberately accepts the portable SQL subset used by reviewed callers.
    Dollar quotes, backslash-escaped strings and numbered/named SQLite binds
    are rejected rather than guessed. It never interpolates parameter values.
    """
    result = []
    i = 0
    while i < len(sql):
        char = sql[i]
        if char in "'\"":
            quote = char
            start = i
            i += 1
            while i < len(sql):
                if sql[i] == "\\":
                    raise ValueError("Backslash SQL quoting is outside the portable statement contract")
                if sql[i] == quote:
                    i += 1
                    if i < len(sql) and sql[i] == quote:
                        i += 1
                        continue
                    break
                i += 1
            else:
                raise ValueError("Unterminated SQL quote")
            result.append(sql[start:i].replace("%", "%%"))
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = len(sql) if end < 0 else end
            result.append(sql[i:end].replace("%", "%%"))
            i = end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0 or "/*" in sql[i + 2:end]:
                raise ValueError("Unsupported SQL block comment")
            end += 2
            result.append(sql[i:end].replace("%", "%%"))
            i = end
        elif char == "?":
            if i + 1 < len(sql) and sql[i + 1].isdigit():
                raise ValueError("Numbered SQL parameters are unsupported")
            result.append("%s")
            i += 1
        elif sql.startswith("::", i):
            result.append("::")
            i += 2
        elif char == "$" or (char in ":@" and i + 1 < len(sql) and sql[i + 1].isalpha()):
            raise ValueError("Use portable positional qmark parameters")
        else:
            result.append("%%" if char == "%" else char)
            i += 1
    return "".join(result)


def execute(conn, sql, params=(), *, returning=None):
    if not is_postgresql(conn):
        return conn.execute(sql, params)
    if not getattr(conn, "_postgresql_application", False):
        require_transaction(conn)
    if returning:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", returning):
            raise ValueError("Generated ID must be an explicit column identifier")
        sql = sql.rstrip().removesuffix(";") + ' RETURNING "' + returning + '"'
    if getattr(conn, "_postgresql_application", False):
        return conn.execute_native(_parameters(sql), params)
    return conn.execute(_parameters(sql), params)


def executemany(conn, sql, params):
    if not is_postgresql(conn):
        return conn.executemany(sql, params)
    if getattr(conn, "_postgresql_application", False):
        return conn.executemany_native(_parameters(sql), params)
    require_transaction(conn)
    with conn.cursor() as cursor:
        cursor.executemany(_parameters(sql), params)


def generated_id(cursor, key):
    if hasattr(cursor, "lastrowid"):
        return int(cursor.lastrowid)
    return int(cursor.fetchone()[key])


def commit(conn):
    if not is_postgresql(conn) or getattr(conn, "_postgresql_application", False):
        conn.commit()


def rollback(conn):
    if not is_postgresql(conn) or getattr(conn, "_postgresql_application", False):
        conn.rollback()


@contextmanager
def transaction(conn):
    if is_postgresql(conn):
        if not getattr(conn, "_postgresql_application", False):
            require_transaction(conn)
        with conn.transaction():
            yield
    else:
        with conn:
            yield


def lock_write(conn):
    """Reserve a short application write unit, matching SQLite writer semantics.

    One key covers all repository families to avoid inverted family lock orders.
    Reads and external work remain concurrent. Workload-specific row-lock
    optimizations require separate measured verification.
    """
    if is_postgresql(conn):
        require_transaction(conn)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s, 0))",
                     (":trading-ai-application-writer",))


def begin_immediate(conn, *, lock="application-write"):
    if is_postgresql(conn):
        lock_write(conn)
    else:
        conn.execute("BEGIN IMMEDIATE")


def atomic_repository(lock_name):
    """Serialize a complete native decision; retain SQLite's existing boundaries."""
    def decorate(function):
        @wraps(function)
        def wrapped(conn, *args, **kwargs):
            if not is_postgresql(conn):
                return function(conn, *args, **kwargs)
            if getattr(conn, "read_only", False):
                # Read APIs remain usable through a server-enforced reader.
                # A mutating callback still fails at the actual write boundary.
                return function(conn, *args, **kwargs)
            with transaction(conn):
                lock_write(conn)
                return function(conn, *args, **kwargs)
        return wrapped
    return decorate
