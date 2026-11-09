"""Portable binding and SQLite transaction behavior, without optional drivers."""
import sqlite3
import unittest
from contextlib import closing

from scrapper.db.repository_backend import (
    _parameters, commit, execute, executemany, generated_id, is_postgresql,
    statement, transaction,
)


class RepositoryBackendTests(unittest.TestCase):
    def test_only_unquoted_qmarks_bind_and_literal_percent_is_escaped(self):
        self.assertEqual(
            _parameters('SELECT ?, \'why? 50% it\'\'s\', "col?", 7 % 2 -- ? %\n/* ? % */'),
            'SELECT %s, \'why? 50%% it\'\'s\', "col?", 7 %% 2 -- ? %%\n/* ? %% */',
        )

    def test_unsupported_quoting_and_parameter_forms_fail_closed(self):
        for sql in ("SELECT 'open", 'SELECT "open', 'SELECT /* open',
                    'SELECT /* /*nested*/ */ 1', 'SELECT ?1', 'SELECT :named',
                    'SELECT @named', 'SELECT $1', "SELECT E'a\\'?"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                _parameters(sql)

    def test_sqlite_generated_ids_and_transactions_preserve_contract(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            self.assertFalse(is_postgresql(conn))
            execute(conn, 'CREATE TABLE entries(id INTEGER PRIMARY KEY, value TEXT)')
            cursor = execute(conn, 'INSERT INTO entries(value) VALUES (?)',
                             ("exact ? ' % value",), returning='id')
            self.assertEqual(generated_id(cursor, 'id'), 1)
            commit(conn)
            self.assertEqual(statement(conn, 'sqlite', 'postgresql'), 'sqlite')
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                with transaction(conn):
                    executemany(conn, 'INSERT INTO entries(value) VALUES (?)', [('2',), ('3',)])
                    raise RuntimeError('rollback')
            self.assertEqual(conn.execute('SELECT value FROM entries').fetchall(),
                             [("exact ? ' % value",)])
