"""Synthetic retained-report checks; no database or driver is used."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit.postgresql_cutover_evidence import assess, main
from scrapper.db.postgresql_schema import manifest, source_profile


def reports(profile='077'):
    names = (source_profile() if profile == '077' else manifest())['objects']['table']
    tables = {name: {'rows': 0, 'sha256': 'a' * 64} for name in names}
    exported = dict(format=1, status='verified_snapshot', snapshot_sha256='b' * 64, tables=tables)
    imported = dict(format=1, status='committed', snapshot_sha256='b' * 64,
                    tables={name: dict(source=deepcopy(value), destination=deepcopy(value), verified=True)
                            for name, value in tables.items()})
    return exported, imported


class CutoverEvidenceTests(unittest.TestCase):
    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            assess(None, None, profile='unknown')

    def test_every_table_and_empty_tables_required_for_each_profile(self):
        for profile in ('076', '077'):
            exported, imported = reports(profile)
            result = assess(exported, imported, profile=profile)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(set(result['tables']), set(exported['tables']))
            self.assertEqual(result['production_health'], 'UNKNOWN')
            imported['status'] = 'verified_existing'
            self.assertEqual(assess(exported, imported, profile=profile)['status'], 'PASS')

    def test_missing_evidence_stays_unknown_even_if_both_reports_omit_table(self):
        self.assertEqual(assess(None, None)['status'], 'UNKNOWN')
        exported, imported = reports()
        name = next(iter(exported['tables']))
        del exported['tables'][name]
        del imported['tables'][name]
        result = assess(exported, imported)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['tables'][name], 'UNKNOWN')

    def test_same_count_corruption_and_invalid_summaries_fail(self):
        for mutation in ('hash', 'bool_count', 'missing_verified', 'bad_hash', 'negative'):
            exported, imported = reports()
            table = imported['tables'][next(iter(imported['tables']))]
            if mutation == 'hash': table['destination']['sha256'] = 'c' * 64
            if mutation == 'bool_count': table['destination']['rows'] = False
            if mutation == 'missing_verified': table.pop('verified')
            if mutation == 'bad_hash': table['destination']['sha256'] = 'invalid'
            if mutation == 'negative': table['destination']['rows'] = -1
            with self.subTest(mutation=mutation):
                self.assertEqual(assess(exported, imported)['status'], 'FAIL')

    def test_snapshot_mismatch_failed_transfer_and_foreign_tables_fail(self):
        for mutation in ('snapshot', 'status', 'extra', 'format', 'shape'):
            exported, imported = reports()
            if mutation == 'snapshot': imported['snapshot_sha256'] = 'c' * 64
            if mutation == 'status': imported['status'] = 'failed'
            if mutation == 'extra': imported['tables']['foreign'] = {}
            if mutation == 'format': imported['format'] = True
            if mutation == 'shape': imported = []
            with self.subTest(mutation=mutation):
                self.assertEqual(assess(exported, imported)['status'], 'FAIL')

    def test_cli_nonzero_unknown_malformed_and_no_private_text(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([]), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'UNKNOWN')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'
            path.write_text('private secret malformed')
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--export-report', str(path)]), 1)
            self.assertNotIn('private', output.getvalue())
            exported, imported = reports()
            path.write_text(json.dumps(exported))
            target = Path(directory) / 'target.json'
            target.write_text(json.dumps(imported))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--export-report', str(path), '--import-report', str(target)]), 0)
            self.assertEqual(json.loads(path.read_text()), exported)
