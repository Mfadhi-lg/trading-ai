"""Application row/transaction behavior without a long transaction around I/O."""
from contextlib import closing
import os
import sqlite3
import unittest

from scrapper.db.database import selected_backend
from scrapper.db.postgresql_application import RepositoryRow
from scrapper.db.repository_backend import atomic_repository, begin_immediate, commit, execute
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase


class RowAndSelectionTests(unittest.TestCase):
    def test_row_contract_matches_sqlite_named_positional_slice_and_iteration(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            source = conn.execute("SELECT 7 AS identifier, '₹ exact' AS raw, NULL AS missing").fetchone()
        target = RepositoryRow(source.keys(), tuple(source))
        self.assertEqual(dict(target), dict(source))
        self.assertEqual(tuple(target), tuple(source))
        self.assertEqual(target[:2], source[:2])
        self.assertEqual(target[-1], source[-1])
        self.assertEqual(target['RAW'], source['RAW'])
        self.assertEqual(target, RepositoryRow(source.keys(), tuple(source)))
        with self.assertRaises(IndexError):
            target['not_a_column']

    def test_backend_selection_is_explicit_and_fails_closed(self):
        self.assertEqual(selected_backend({}), 'sqlite')
        self.assertEqual(selected_backend({'DATABASE_URL': 'postgresql://ignored/test'}), 'sqlite')
        self.assertEqual(selected_backend({'TRADING_AI_DB_BACKEND': 'postgresql'}), 'postgresql')
        for value in ('', 'postgres', 'SQLite', 'unsupported'):
            with self.assertRaises(ValueError):
                selected_backend({'TRADING_AI_DB_BACKEND': value})


class ApplicationTransactionTests(DisposablePostgreSQLTestCase):
    def test_selects_are_idle_and_writes_wait_for_explicit_commit(self):
        with self.session() as writer, self.session() as reader:
            self.assertEqual(writer.execute('SELECT ? AS n', (1,)).fetchone()['n'], 1)
            self.assertFalse(writer.in_transaction)
            writer.execute("INSERT INTO companies(current_legal_name) VALUES (?)", ('Synthetic',))
            self.assertTrue(writer.in_transaction)
            self.assertEqual(reader.execute('SELECT count(*) FROM companies').fetchone()[0], 0)
            writer.commit()
            self.assertFalse(writer.in_transaction)
            self.assertEqual(reader.execute('SELECT count(*) FROM companies').fetchone()[0], 1)
            self.assertFalse(reader.in_transaction)

    def test_context_keeps_connection_open_and_rolls_back_whole_unit(self):
        with self.session() as conn:
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                with conn:
                    conn.execute("INSERT INTO companies(current_legal_name) VALUES (?)", ('first',))
                    conn.commit()  # Cannot split the context's unit of work.
                    conn.execute("INSERT INTO companies(current_legal_name) VALUES (?)", ('second',))
                    raise RuntimeError('rollback')
            self.assertFalse(conn.closed)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute('SELECT count(*) FROM companies').fetchone()[0], 0)
            with conn:
                conn.execute("INSERT INTO companies(current_legal_name) VALUES (?)", ('committed',))
            self.assertFalse(conn.in_transaction)

    def test_nested_and_statement_failures_do_not_poison_outer_unit(self):
        with self.session() as conn, conn:
            conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (1,?)", ('original',))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (1,?)", ('private source',))
            with self.assertRaisesRegex(RuntimeError, 'nested'):
                with conn:
                    conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (2,?)", ('nested',))
                    raise RuntimeError('nested')
            conn.execute("INSERT INTO companies(company_id,current_legal_name) VALUES (3,?)", ('survives',))
            self.assertEqual([row[0] for row in conn.execute('SELECT company_id FROM companies ORDER BY company_id')], [1, 3])

    def test_atomic_repository_commits_before_return_and_preserves_binding(self):
        @atomic_repository('unit-contract')
        def write(conn, value):
            begin_immediate(conn)
            cursor = execute(conn, "INSERT INTO companies(current_legal_name) VALUES (?)", (value,), returning='company_id')
            commit(conn)
            return cursor.fetchone()['company_id']
        with self.session() as conn:
            value = "Exact ? 50% '; DROP TABLE companies --"
            company = write(conn, value)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute('SELECT current_legal_name FROM companies WHERE company_id=?', (company,)).fetchone()[0], value)

    def test_cte_write_uses_a_rollback_unit_and_shared_writer_lock(self):
        with self.session() as first, self.session() as second:
            first.execute("""WITH added AS (
                INSERT INTO companies(current_legal_name) VALUES (?) RETURNING company_id
            ) SELECT company_id FROM added""", ('synthetic CTE source',))
            self.assertTrue(first.in_transaction)
            self.assertEqual(second.execute('SELECT count(*) FROM companies').fetchone()[0], 0)
            second.execute("SET lock_timeout='100ms'")
            with self.assertRaises(sqlite3.OperationalError):
                second.execute("INSERT INTO companies(current_legal_name) VALUES (?)", ('competing writer',))
            second.rollback()
            first.rollback()
            self.assertEqual(first.execute('SELECT count(*) FROM companies').fetchone()[0], 0)
            with second:
                second.execute("INSERT INTO companies(current_legal_name) VALUES (?)", ('released lock',))
            self.assertEqual(first.execute('SELECT count(*) FROM companies').fetchone()[0], 1)

    def test_read_cte_and_sql_keywords_in_literals_or_comments_stay_idle(self):
        with self.session() as conn:
            row = conn.execute("""WITH selected AS (
                SELECT 'INSERT UPDATE DELETE FOR UPDATE PG_ADVISORY_XACT_LOCK' AS text
            ) SELECT text AS "DELETE" FROM selected /* INSERT */ -- UPDATE
            """).fetchone()
            self.assertIn('INSERT UPDATE', row[0])
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT '; $tag$ ? UPDATE' AS text").fetchone()[0], '; $tag$ ? UPDATE')
            self.assertFalse(conn.in_transaction)

    def test_read_only_cte_rejects_writes_and_keeps_reads_idle(self):
        with self.session(read_only=True) as conn:
            self.assertEqual(conn.execute('WITH selected AS (SELECT 9 AS value) SELECT value FROM selected').fetchone()[0], 9)
            self.assertFalse(conn.in_transaction)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("WITH changed AS (INSERT INTO companies(current_legal_name) VALUES ('denied') RETURNING company_id) SELECT * FROM changed")
            self.assertFalse(conn.in_transaction)

    def test_multi_statement_and_dollar_quoted_application_sql_are_rejected(self):
        with self.session() as conn:
            for statement in ('SELECT 1; SELECT 2', 'SELECT $$opaque body$$',
                              'SELECT /* nested /* INSERT */ comment */ 1'):
                with self.subTest(statement=statement), self.assertRaises(ValueError):
                    conn.execute(statement)
                self.assertFalse(conn.in_transaction)

    def test_deferred_foreign_key_commit_failure_is_sanitized_and_recovers(self):
        for contextual in (False, True):
            with self.subTest(contextual=contextual), self.session() as conn:
                def invalid():
                    conn.begin()
                    conn.execute('SET CONSTRAINTS ALL DEFERRED')
                    conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value,is_current) VALUES (987654321,'NSE','PRIVATE-SOURCE-MARKER',1)")
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    if contextual:
                        with conn:
                            invalid()
                    else:
                        invalid()
                        conn.commit()
                self.assertNotIn('PRIVATE-SOURCE-MARKER', str(caught.exception))
                self.assertNotIn('987654321', str(caught.exception))
                self.assertFalse(conn.in_transaction)
                self.assertEqual(conn.execute('SELECT count(*) FROM security_identifiers').fetchone()[0], 0)

    def test_read_only_and_unported_dialects_fail_closed(self):
        with self.session(read_only=True) as conn:
            self.assertEqual(conn.execute('SELECT 1').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO companies(current_legal_name) VALUES ('denied')")
            self.assertFalse(conn.in_transaction)
        with self.session() as conn:
            with self.assertRaisesRegex(NotImplementedError, 'explicit infrastructure session'):
                conn.cursor()
            for sql in ('BEGIN IMMEDIATE', 'PRAGMA foreign_keys=OFF',
                        "INSERT OR IGNORE INTO companies(current_legal_name) VALUES ('no translation')"):
                with self.assertRaisesRegex(ValueError, 'explicit PostgreSQL repository port'):
                    conn.execute(sql)
            with self.assertRaises(ValueError):
                conn.set_authorizer(None)
