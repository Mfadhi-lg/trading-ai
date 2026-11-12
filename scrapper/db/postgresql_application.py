"""Long-lived native application sessions with short SQLite-compatible units.

Only parameter binding and Python connection/row behavior are shared. SQL dialect
differences stay in their owning repositories; no SQL conflict, date or policy
translation occurs here. The server's role privileges are the write boundary.
"""
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

from .postgresql import PostgreSQLSettings, _dependencies, _configure, _connection_kwargs
from .postgresql_schema import validate_schema_name, check_compatibility
from .repository_backend import _parameters


class RepositoryRow:
    """The name/index/slice/value-iteration contract of sqlite3.Row."""
    __slots__ = ("_names", "_values")

    def __init__(self, names, values):
        self._names = tuple(names)
        self._values = tuple(values)

    def keys(self):
        return list(self._names)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = next(i for i, name in enumerate(self._names) if name.lower() == key.lower())
            except StopIteration:
                raise IndexError("No item with that key") from None
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        if isinstance(other, RepositoryRow):
            return self._names == other._names and self._values == other._values
        return NotImplemented

    def __hash__(self):
        return hash((self._names, self._values))


def repository_row(cursor):
    names = tuple(column.name for column in cursor.description) if cursor.description else ()
    return lambda values: RepositoryRow(names, values)


def _qmark_count(sql):
    """Inspect binding tokens without interpreting or changing SQL semantics."""
    count = 0
    i = 0
    while i < len(sql):
        if sql[i] in "'\"":
            quote = sql[i]
            i += 1
            while i < len(sql):
                if sql[i] == quote:
                    i += 1
                    if i < len(sql) and sql[i] == quote:
                        i += 1
                        continue
                    break
                i += 1
        elif sql.startswith("--", i):
            i = sql.find("\n", i)
            if i < 0:
                break
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError("Unterminated SQL comment")
            i = end + 2
        else:
            count += sql[i] == "?"
            i += 1
    return count


def _sql_words(sql):
    """Inspect keywords outside SQL literals/comments; never authorize SQL."""
    words = []
    i = 0
    while i < len(sql):
        if sql[i] in "'\"":
            quote = sql[i]
            i += 1
            while i < len(sql):
                if sql[i] == quote:
                    i += 1
                    if i < len(sql) and sql[i] == quote:
                        i += 1
                        continue
                    break
                i += 1
        elif sql.startswith('--', i):
            end = sql.find('\n', i)
            i = len(sql) if end < 0 else end
        elif sql.startswith('/*', i):
            end = sql.find('*/', i + 2)
            if end < 0 or '/*' in sql[i + 2:end]:
                raise ValueError('Unsupported SQL comment')
            i = end + 2
        elif sql[i] == '$':
            raise ValueError('Dollar-quoted SQL is outside the application statement contract')
        elif sql[i].isalpha() or sql[i] == '_':
            end = i + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] == '_'):
                end += 1
            words.append(sql[i:end].upper())
            i = end
        elif sql[i] == ';':
            if sql[i + 1:].strip():
                raise ValueError('Application calls require one SQL statement')
            i += 1
        else:
            i += 1
    return words


def _database_error(error):
    """Preserve application exception families without copying source records."""
    code = error.sqlstate or "unknown"
    if code.startswith("23"):
        messages = {"23503": "FOREIGN KEY constraint failed", "23505": "UNIQUE constraint failed",
                    "23502": "NOT NULL constraint failed", "23514": "CHECK constraint failed"}
        return sqlite3.IntegrityError(messages.get(code, "Integrity constraint failed"))
    if code.startswith("42") or code.startswith("25") or code in {"55P03", "40P01", "40001", "57014"}:
        return sqlite3.OperationalError("PostgreSQL operation rejected (SQLSTATE " + code + ")")
    return sqlite3.DatabaseError("PostgreSQL operation failed (SQLSTATE " + code + ")")


class PostgreSQLApplicationConnection:
    _postgresql_application = True

    def __init__(self, raw, settings, schema, *, read_only=False):
        validate_schema_name(schema)
        from psycopg.pq import TransactionStatus
        if not raw.autocommit or raw.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError("Application connection requires an idle autocommit native session")
        self.raw = raw
        self.settings = settings
        self.schema = schema
        self.read_only = read_only
        self._depth = 0
        self._contexts = []
        self._policy = None
        self.raw.row_factory = repository_row

    @property
    def info(self):
        return self.raw.info

    @property
    def closed(self):
        return self.raw.closed

    @property
    def in_transaction(self):
        from psycopg.pq import TransactionStatus
        return self.raw.info.transaction_status != TransactionStatus.IDLE

    @property
    def row_factory(self):
        return self.raw.row_factory

    @row_factory.setter
    def row_factory(self, factory):
        from psycopg.rows import tuple_row
        self.raw.row_factory = repository_row if factory is sqlite3.Row else (tuple_row if factory is None else factory)

    def begin(self):
        if not self.in_transaction:
            self.raw.execute("BEGIN")

    def _write_lock(self):
        if self.read_only:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        self.begin()
        import psycopg
        try:
            with self.raw.transaction():
                self.raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s, 0))",
                                 (":trading-ai-application-writer",))
        except psycopg.Error as error:
            raise _database_error(error) from None

    @contextmanager
    def transaction(self):
        outer = self._depth == 0
        if outer:
            try:
                self.begin()
                if not self.read_only:
                    self._write_lock()
            except BaseException:
                self.raw.rollback()
                raise
            self._depth += 1
            try:
                yield self
            except BaseException:
                self.raw.rollback()
                raise
            else:
                self._commit()
            finally:
                self._depth -= 1
        else:
            self._depth += 1
            try:
                with self.raw.transaction():
                    yield self
            finally:
                self._depth -= 1

    def __enter__(self):
        context = self.transaction()
        value = context.__enter__()
        self._contexts.append(context)
        return value

    def __exit__(self, *args):
        return self._contexts.pop().__exit__(*args)

    def commit(self):
        if self._depth == 0:
            self._commit()

    def _commit(self):
        import psycopg
        try:
            self.raw.commit()
        except psycopg.Error as error:
            self.raw.rollback()
            raise _database_error(error) from None

    def rollback(self):
        if self._depth == 0:
            self.raw.rollback()

    def close(self):
        self.raw.close()

    def cursor(self, *args, **kwargs):
        raise NotImplementedError('Application repositories use execute/executemany; native cursors require an explicit infrastructure session')

    def execute(self, sql, params=None):
        # Existing portable statements use qmarks; explicit native branches use
        # psycopg parameters. Values always stay separate from SQL text.
        if isinstance(sql, str) and _qmark_count(sql):
            sql = _parameters(sql)
        elif params == () or params == []:
            params = None
        return self.execute_native(sql, params)

    def execute_native(self, sql, params=None):
        import psycopg
        if not isinstance(sql, str):
            sql = sql.as_string(self.raw)
        if isinstance(sql, str):
            words = _sql_words(sql)
            head = ' '.join(words)
            if head.startswith(("PRAGMA", "BEGIN IMMEDIATE", "INSERT OR ", "REPLACE INTO")):
                raise ValueError("SQLite-specific SQL requires an explicit PostgreSQL repository port")
            if set(words) & {'INSERT','UPDATE','DELETE','MERGE','CREATE','ALTER','DROP','TRUNCATE','PG_ADVISORY_XACT_LOCK'}:
                self._write_lock()
        try:
            # A failed statement must not poison an enclosing application unit.
            if self.in_transaction:
                with self.raw.transaction():
                    return self.raw.execute(sql, params)
            return self.raw.execute(sql, params)
        except psycopg.Error as error:
            raise _database_error(error) from None

    def executemany(self, sql, params):
        return self.executemany_native(_parameters(sql), params)

    def executemany_native(self, sql, params):
        import psycopg
        self._write_lock()
        try:
            with self.raw.transaction(), self.raw.cursor() as cursor:
                cursor.executemany(sql, params)
                return cursor
        except psycopg.Error as error:
            raise _database_error(error) from None

    def set_authorizer(self, authorizer):
        if authorizer is None:
            raise ValueError("Native runtime permissions cannot be disabled")
        from .postgresql_permissions import assert_policy
        assert_policy(self.raw, self.schema, authorizer)
        self._policy = authorizer

    def reopen(self, *, read_only=None):
        return open_application(self.settings, schema=self.schema,
                                read_only=self.read_only if read_only is None else read_only,
                                authorizer=self._policy)


def mark_schema_identity(conn, schema, *, environment, target_id=None):
    """Explicit setup only: mark a newly reviewed isolated database target."""
    from psycopg import sql
    validate_schema_name(schema)
    if environment not in {"development", "test", "production"}:
        raise ValueError("A supported application environment is required")
    target_id = str(uuid.UUID(target_id)) if target_id else str(uuid.uuid4())
    marker = json.dumps({"application": "trading-ai", "environment": environment, "schema": schema,
                         "target_id": target_id}, sort_keys=True)
    conn.execute(sql.SQL("COMMENT ON SCHEMA {} IS {}").format(sql.Identifier(schema), sql.Literal(marker)))
    return target_id


def open_application(settings=None, *, schema=None, read_only=False, authorizer=None):
    """Open an explicitly configured, pre-provisioned non-owner runtime login."""
    settings = settings or PostgreSQLSettings.from_env()
    schema = schema or os.environ.get("TRADING_AI_POSTGRESQL_SCHEMA", "")
    validate_schema_name(schema)
    environment = os.environ.get("TRADING_AI_ENV", "").strip().lower()
    if environment not in {"development", "test", "production"}:
        raise RuntimeError("TRADING_AI_ENV is required for PostgreSQL application connections")
    psycopg, dict_row, _ = _dependencies()
    try:
        raw = psycopg.connect(settings.dsn, row_factory=dict_row, **_connection_kwargs(settings))
    except psycopg.Error:
        raise RuntimeError("PostgreSQL connection failed; check runtime configuration") from None
    try:
        from psycopg import sql
        from .postgresql_permissions import assert_safe_login
        _configure(raw)
        marker = raw.execute("SELECT obj_description(oid, 'pg_namespace') AS identity FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()
        try:
            identity = json.loads(marker["identity"] or "") if marker else None
        except (ValueError, TypeError):
            identity = None
        target_id = os.environ.get("TRADING_AI_POSTGRESQL_TARGET_ID", "")
        try:
            target_id = str(uuid.UUID(target_id))
        except ValueError:
            raise RuntimeError("TRADING_AI_POSTGRESQL_TARGET_ID must identify the explicitly provisioned database") from None
        if identity != {"application": "trading-ai", "environment": environment, "schema": schema,
                        "target_id": target_id}:
            raise RuntimeError("PostgreSQL schema environment identity is missing or mismatched")
        if environment == "production":
            try:
                release = json.loads(Path(os.environ["TRADING_AI_RUNTIME_IDENTITY_FILE"]).read_text())
            except (KeyError, OSError, ValueError):
                raise RuntimeError("PostgreSQL production access requires an exact authorized runtime identity") from None
            expected = {"database": raw.info.dbname, "schema": schema,
                        "role": raw.info.user, "target_id": target_id}
            if (release.get("environment") != "production" or release.get("authorized") is not True
                    or release.get("postgresql") != expected):
                raise RuntimeError("PostgreSQL production target does not match the authorized release")
        assert_safe_login(raw, schema)
        raw.execute(sql.SQL("SET search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)))
        with raw.transaction():
            check_compatibility(raw, schema)
        for name, value in (("statement_timeout", settings.statement_timeout_ms),
                            ("lock_timeout", settings.lock_timeout_ms),
                            ("idle_in_transaction_session_timeout", settings.idle_transaction_timeout_ms)):
            raw.execute("SELECT set_config(%s,%s,false)", (name, str(value)))
        if read_only:
            raw.execute("SET default_transaction_read_only=on")
        conn = PostgreSQLApplicationConnection(raw, settings, schema, read_only=read_only)
        if authorizer is not None:
            conn.set_authorizer(authorizer)
        return conn
    except BaseException:
        raw.close()
        raise
