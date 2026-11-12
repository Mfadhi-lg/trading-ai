"""Explicit PostgreSQL-native connection API for the migration transition.

Only explicitly ported repositories support sessions from this module.
Importing it does not import the optional driver or open a connection.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PostgreSQLSettings:
    dsn: str = field(repr=False)
    min_size: int = 0
    max_size: int = 4
    connect_timeout: int = 5
    acquire_timeout: float = 10
    statement_timeout_ms: int = 30_000
    lock_timeout_ms: int = 5_000
    idle_transaction_timeout_ms: int = 60_000

    def __post_init__(self):
        try:
            url = urlsplit(self.dsn)
            valid = url.scheme in {"postgresql", "postgres"} and bool(url.path.strip("/"))
            url.port  # Validate malformed ports without exposing the input.
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("DATABASE_URL must be a PostgreSQL URL with a database name")
        if not 0 <= self.min_size <= self.max_size <= 32 or self.max_size == 0:
            raise ValueError("PostgreSQL pool bounds must satisfy 0 <= min <= max <= 32")
        if any(value <= 0 for value in (
            self.connect_timeout, self.acquire_timeout, self.statement_timeout_ms,
            self.lock_timeout_ms, self.idle_transaction_timeout_ms,
        )):
            raise ValueError("PostgreSQL timeouts must be positive")

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        if env.get("TRADING_AI_DB_BACKEND") != "postgresql":
            raise ValueError("Set TRADING_AI_DB_BACKEND=postgresql for the native PostgreSQL API")
        return cls(dsn=env.get("DATABASE_URL", ""))


def _dependencies():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError:
        raise RuntimeError("Install scrapper/requirements.postgresql.txt to use PostgreSQL") from None
    return psycopg, dict_row, ConnectionPool


def _connection_kwargs(settings):
    return {
        "autocommit": True,
        "connect_timeout": settings.connect_timeout,
        "prepare_threshold": None,
    }


def _configure(conn):
    if not 160000 <= conn.info.server_version < 170000:
        raise RuntimeError("The native backend currently requires PostgreSQL 16")
    conn.execute("SET TIME ZONE 'UTC'")


def _transaction_settings(conn, settings, read_only):
    if read_only:
        conn.execute("SET TRANSACTION READ ONLY")
    for name, value in (
        ("statement_timeout", settings.statement_timeout_ms),
        ("lock_timeout", settings.lock_timeout_ms),
        ("idle_in_transaction_session_timeout", settings.idle_transaction_timeout_ms),
    ):
        conn.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


@contextmanager
def connect(settings=None, *, read_only=False):
    """Yield one native mapping-row connection in a managed transaction.

    Successful exit commits; exceptions roll back; every exit closes the session.
    PostgreSQL SQL uses native ``%s`` placeholders, never SQLite ``?`` syntax.
    """
    settings = settings or PostgreSQLSettings.from_env()
    psycopg, dict_row, _ = _dependencies()
    try:
        conn = psycopg.connect(settings.dsn, row_factory=dict_row, **_connection_kwargs(settings))
    except psycopg.Error:
        raise RuntimeError("PostgreSQL connection failed; check configuration and server availability") from None
    try:
        _configure(conn)
        with conn.transaction():
            _transaction_settings(conn, settings, read_only)
            yield conn
    finally:
        conn.close()


class PostgreSQLPool:
    """Bounded, explicitly opened pool; each lease owns a transaction.

    Callers must not manually commit, roll back, or change autocommit on leases.
    Closing the pool releases idle sessions and retires outstanding leases when
    returned. Constructing the pool opens no sessions or background workers.
    """

    def __init__(self, settings=None):
        self.settings = settings or PostgreSQLSettings.from_env()
        psycopg, dict_row, pool_class = _dependencies()

        class SafeConnection(psycopg.Connection):
            @classmethod
            def connect(cls, *args, **kwargs):
                # Pool workers log connection errors. Never log driver-provided
                # diagnostics that may contain connection strings or passwords.
                try:
                    return super().connect(*args, **kwargs)
                except psycopg.Error:
                    raise RuntimeError("PostgreSQL connection failed") from None

        self._pool = pool_class(
            conninfo=self.settings.dsn, connection_class=SafeConnection,
            kwargs={**_connection_kwargs(self.settings), "row_factory": dict_row},
            min_size=self.settings.min_size, max_size=self.settings.max_size,
            timeout=self.settings.acquire_timeout, max_waiting=self.settings.max_size,
            configure=_configure, reset=self._reset, open=False,
            reconnect_timeout=self.settings.acquire_timeout,
        )

    @staticmethod
    def _reset(conn):
        # Drop temporary objects, session settings and advisory locks before reuse.
        conn.execute("DISCARD ALL")

    def open(self):
        try:
            self._pool.open(wait=True, timeout=self.settings.acquire_timeout)
        except Exception:
            self.close()
            raise
        return self

    def close(self):
        self._pool.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def connection(self, *, read_only=False):
        with self._pool.connection(timeout=self.settings.acquire_timeout) as conn:
            # DISCARD ALL resets timezone; restore it on each checkout.
            _configure(conn)
            with conn.transaction():
                _transaction_settings(conn, self.settings, read_only)
                yield conn


@contextmanager
def repository_session(settings=None, *, schema=None, read_only=False):
    """Open a checked application schema for explicitly ported repositories.

    This never initializes or migrates a schema. Commit the short claim session
    before doing external work, then use a separate persistence session. The
    SQLite connection factory and operational authorizers remain unchanged.
    """
    from .postgresql_schema import validate_schema_name, check_compatibility
    schema = schema or os.environ.get("TRADING_AI_POSTGRESQL_SCHEMA", "")
    validate_schema_name(schema)
    with connect(settings, read_only=read_only) as conn:
        from psycopg import sql
        conn.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)))
        check_compatibility(conn, schema)
        yield conn
