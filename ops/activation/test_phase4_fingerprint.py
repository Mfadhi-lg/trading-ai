"""Focused regression for the exact canary's preservation implementation."""
import sqlite3
import unittest
from phase4_documents_runtime import fingerprint, assert_preserved


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.addCleanup(self.c.close)
        self.c.executescript('''
            CREATE TABLE accepted_page_versions (
                company_id INTEGER NOT NULL, source TEXT NOT NULL,
                reporting_scope TEXT NOT NULL, resolved_source_period TEXT NOT NULL,
                parse_id INTEGER NOT NULL, accepted_at_utc TEXT NOT NULL,
                PRIMARY KEY(company_id,source,reporting_scope,resolved_source_period)
            ) WITHOUT ROWID;
            CREATE TABLE ordinary (id INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE no_pk (value TEXT);
            CREATE TABLE shadows_rowid (rowid TEXT, value TEXT);
            CREATE TABLE nullable_key (key TEXT COLLATE NOCASE, value TEXT, PRIMARY KEY(key));
        ''')

    def insert_acceptances(self, reverse=False):
        rows = [(2,'screener','standalone','2026-09',22,'date'),
                (1,'screener','standalone','2026-06',11,'date'),
                (1,'screener','standalone','2026-09',12,'date')]
        self.c.executemany('INSERT INTO accepted_page_versions VALUES (?,?,?,?,?,?)',
                           reversed(rows) if reverse else rows)

    def test_without_rowid_composite_key_and_stable_order(self):
        self.insert_acceptances()
        first = fingerprint(self.c, 'accepted_page_versions')
        self.assertEqual(first, fingerprint(self.c, 'accepted_page_versions'))
        self.c.execute('DELETE FROM accepted_page_versions')
        self.insert_acceptances(reverse=True)
        self.assertEqual(first, fingerprint(self.c, 'accepted_page_versions'))

    def test_declared_pk_order_is_not_column_declaration_order(self):
        self.c.execute('CREATE TABLE reordered (a TEXT,b TEXT,v TEXT,PRIMARY KEY(b,a)) WITHOUT ROWID')
        self.c.executemany('INSERT INTO reordered VALUES (?,?,?)', [('z','a','1'),('a','z','2')])
        statements=[]
        self.c.set_trace_callback(statements.append)
        fingerprint(self.c,'reordered')
        self.assertIn('ORDER BY "b" COLLATE BINARY,"a" COLLATE BINARY',statements[-1])

    def test_ordinary_integer_pk_fingerprint_stable_after_reinsertion(self):
        self.c.executemany('INSERT INTO ordinary VALUES (?,?)', [(2,'b'),(1,'a')])
        first=fingerprint(self.c,'ordinary')
        self.c.execute('DELETE FROM ordinary')
        self.c.executemany('INSERT INTO ordinary VALUES (?,?)', [(1,'a'),(2,'b')])
        self.assertEqual(first,fingerprint(self.c,'ordinary'))

    def test_ordinary_without_pk_and_shadowed_rowid(self):
        for table, values in [('no_pk',[('a',),('b',)]),('shadows_rowid',[('same','a'),('same','b')])]:
            self.c.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in values[0])+')',values)
            first=fingerprint(self.c,table)
            self.assertEqual(first,fingerprint(self.c,table))
            self.c.execute('UPDATE '+table+" SET value='changed' WHERE _rowid_=1")
            self.assertNotEqual(first,fingerprint(self.c,table))

    def test_nullable_ordinary_pk_uses_value_tie_breakers(self):
        self.c.executemany('INSERT INTO nullable_key VALUES (?,?)', [(None,'B'),(None,'a')])
        first=fingerprint(self.c,'nullable_key')
        self.c.execute('DELETE FROM nullable_key')
        self.c.executemany('INSERT INTO nullable_key VALUES (?,?)', [(None,'a'),(None,'B')])
        self.assertEqual(first,fingerprint(self.c,'nullable_key'))

    def test_protection_detects_real_insert_update_and_delete(self):
        self.insert_acceptances()
        self.c.execute("INSERT INTO ordinary VALUES (1,'original')")
        before={table:fingerprint(self.c,table) for table in ('accepted_page_versions','ordinary')}
        assert_preserved(self.c,before)
        changes=["UPDATE accepted_page_versions SET parse_id=99 WHERE company_id=2",
                 "INSERT INTO accepted_page_versions VALUES (3,'screener','standalone','2026-09',33,'date')",
                 'DELETE FROM accepted_page_versions WHERE company_id=2',
                 "UPDATE ordinary SET value='unauthorized' WHERE id=1"]
        for sql in changes:
            with self.subTest(sql=sql):
                self.c.execute('SAVEPOINT change')
                self.c.execute(sql)
                with self.assertRaisesRegex(RuntimeError,'Protected production data changed'):
                    assert_preserved(self.c,before)
                self.c.execute('ROLLBACK TO change');self.c.execute('RELEASE change')
                assert_preserved(self.c,before)

    def test_exact_real_binary_and_storage_type_changes_are_detected(self):
        self.c.execute('CREATE TABLE typed_values (id INTEGER PRIMARY KEY,value)')
        self.c.execute('INSERT INTO typed_values VALUES (1,?)', (1.0,))
        before={'typed_values':fingerprint(self.c,'typed_values')}
        for value in (1.0000000000000002, 1, '1.0', b'1.0', None):
            with self.subTest(value=value):
                self.c.execute('UPDATE typed_values SET value=? WHERE id=1',(value,))
                with self.assertRaisesRegex(RuntimeError,'Protected production data changed'):
                    assert_preserved(self.c,before)
        self.c.execute('UPDATE typed_values SET value=? WHERE id=1',(1.0,))
        assert_preserved(self.c,before)
        self.c.execute('UPDATE typed_values SET value=? WHERE id=1',(b'\x00\xff',))
        binary=fingerprint(self.c,'typed_values')
        self.c.execute('UPDATE typed_values SET value=? WHERE id=1',(b'\x00\xfe',))
        self.assertNotEqual(binary,fingerprint(self.c,'typed_values'))

    def test_quoted_identifiers_and_unknown_table_fail_closed(self):
        self.c.execute('CREATE TABLE "odd table" ("odd key" TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
        self.c.execute("INSERT INTO \"odd table\" VALUES ('a','value')")
        self.assertEqual(fingerprint(self.c,'odd table'),fingerprint(self.c,'odd table'))
        with self.assertRaises(ValueError): fingerprint(self.c,'missing')
        with self.assertRaises(ValueError): fingerprint(self.c,'ordinary"; DROP TABLE ordinary; --')
        self.assertIsNotNone(self.c.execute('SELECT * FROM ordinary'))


if __name__ == '__main__': unittest.main()
