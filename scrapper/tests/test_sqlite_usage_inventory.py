"""Source-only regressions for the SQLite/native runtime review boundary."""
import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.audit import sqlite_usage as audit
from scripts.audit import native_runtime_smoke as smoke


def reviewed(entries):
    return {'version': 1, 'files': [dict(entry,
        classification='live application', store='synthetic fixture', intent='read',
        backend_guard='explicit SQLite branch reviewed by fixture author',
        owner='test', disposition='supported fallback', follow_up='#96') for entry in entries]}


class SQLiteUsageInventoryTests(unittest.TestCase):
    def test_ast_evidence_normalizes_optional_empty_fields(self):
        node = ast.parse('def read(): pass').body[0]
        expected = audit.syntax_digest(node)
        node.type_params = []
        node.type_comment = None
        self.assertEqual(audit.syntax_digest(node), expected)
        with patch.object(audit.ast, 'dump', side_effect=AssertionError('version-dependent AST dump')):
            self.assertTrue(audit.python_sites("import sqlite3\nsqlite3.connect(':memory:')"))

    def test_alias_imports_and_assignment_aliases(self):
        source = '''import sqlite3 as lite
from sqlite3 import connect as open_db
from sqlite3 import dbapi2 as api
from sqlite3.dbapi2 import connect as another
alias = lite.connect
def read():
    return (lite.connect(':memory:'), open_db(':memory:'),
            api.connect(':memory:'), another(':memory:'), alias(':memory:'))
'''
        sites = audit.python_sites(source)
        self.assertEqual(len(sites), 5)
        self.assertTrue(all(site['kind'] == 'direct connection' for site in sites))
        self.assertTrue(all(site['symbol'] == 'read' for site in sites))

    def test_extensionless_embedded_and_dynamic_sql_are_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            (root/'scripts/reader').write_text("#!/usr/bin/env python3\nimport sqlite3 as s\ns.connect(':memory:')\n")
            (root/'Dockerfile').write_text('HEALTHCHECK CMD python -c "import sqlite3; sqlite3.connect(path)"\n')
            (root/'scripts/query.py').write_text("def read(c, field):\n    return c.execute(f'SELECT {field} FROM t')\n")
            entries = audit.scan(root, ['scripts/reader', 'Dockerfile', 'scripts/query.py'])
            self.assertEqual({site['kind'] for e in entries for site in e['sites']},
                             {'direct connection', 'embedded/config', 'dynamic query'})

    def test_new_unguarded_json_sql_fails_even_in_already_classified_file(self):
        guarded = '''def read(conn):
    if is_postgresql(conn):
        return conn.execute("SELECT data::jsonb ->> 'x' FROM t")
    else:
        return conn.execute("SELECT json_extract(data, '$.x') FROM t")
'''
        entry = {'path': 'ops/read.py', 'sites': audit.python_sites(guarded), 'caller_references': []}
        manifest = reviewed([entry])
        self.assertEqual(audit.check([entry], manifest), [])
        self.assertEqual(entry['sites'][0]['branch_hints'][0]['arm'], 'else')
        unguarded = guarded + '\ndef bad(conn):\n    return conn.execute("SELECT json_extract(data, \'$.x\') FROM t")\n'
        changed = dict(entry, sites=audit.python_sites(unguarded))
        self.assertTrue(audit.check([changed], manifest))
        # Removing only the backend dispatch must also invalidate old approval.
        bypass = guarded.replace('if is_postgresql(conn):', 'if False:')
        self.assertTrue(audit.check([dict(entry, sites=audit.python_sites(bypass))], manifest))

    def test_row_and_exception_compatibility_are_not_connection_sites(self):
        self.assertEqual(audit.python_sites('import sqlite3\nrow = sqlite3.Row\nerror = sqlite3.OperationalError\n'), [])

    def test_unclassified_stale_and_changed_callers_fail(self):
        entry = {'path': 'ops/read.py', 'sites': [], 'caller_references': []}
        self.assertTrue(audit.check([entry], {'files': []}))
        manifest = reviewed([entry])
        self.assertTrue(audit.check([], manifest))
        self.assertTrue(audit.check([dict(entry, caller_references=[{'path':'ops/new.py','line':1}])], manifest))
        manifest['files'][0]['backend_guard'] = 'UNCLASSIFIED'
        self.assertTrue(audit.check([entry], manifest))

    def test_candidate_evidence_refresh_does_not_acknowledge_review(self):
        entry = {'path': 'ops/read.py', 'source_sha256': 'new-source', 'sites': [], 'caller_references': []}
        manifest = reviewed([entry])
        manifest['files'][0]['reviewed_source_sha256'] = 'old-source'
        self.assertTrue(audit.check([entry], manifest))
        manifest['files'][0]['reviewed_source_sha256'] = 'new-source'
        self.assertEqual(audit.check([entry], manifest), [])

    def test_native_gate_fails_missing_environment_and_skipped_or_empty_suite(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(smoke.run(), 1)
        for count, skipped in ((1, [('native', 'missing dependency')]), (0, [])):
            result = SimpleNamespace(testsRun=count, skipped=skipped, wasSuccessful=lambda: True)
            with patch.dict('os.environ', {'TRADING_AI_ENV':'test', 'TRADING_AI_POSTGRESQL_TEST_DSN':'synthetic'}), \
                 patch.dict('sys.modules', {'psycopg': SimpleNamespace()}), \
                 patch.object(smoke.shutil, 'which', return_value='/synthetic/client'), \
                 patch.object(smoke.unittest.defaultTestLoader, 'loadTestsFromNames', return_value=unittest.TestSuite()), \
                 patch.object(smoke.unittest.TextTestRunner, 'run', return_value=result):
                self.assertEqual(smoke.run(), 1)

    def test_tracked_inventory_matches_reviewed_evidence(self):
        errors = audit.check(audit.scan(audit.ROOT), json.loads(audit.MANIFEST.read_text()))
        self.assertEqual(errors, [], '\n'.join(errors))
