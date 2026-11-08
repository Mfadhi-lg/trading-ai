"""Grouped repository ownership and legacy public-contract checks."""
import ast
import dataclasses
import inspect
import unittest
from pathlib import Path
from scrapper.db import mf_workbook_repo as legacy
from scrapper.db.mutual_funds import workbook_inventory as inventory, holdings
from scrapper.pipeline import import_mf

class WorkbookOwnershipTests(unittest.TestCase):
    def test_legacy_functions_and_types_are_same_objects(self):
        for owner, names in ((inventory, ('PersistedImport', 'persist_inventory', 'persist_ppfas_structure', 'persist_ppfas_structure_failure', '_json', '_table_position', 'M02_PASSING_RULES')), (holdings, ('HoldingsNormalizationSummary', 'persist_ppfas_holdings', 'holdings_normalization_summary', '_upsert_m03_validation', '_security_for_isin', 'M03_VALIDATION_RULES', 'M03_VALIDATION_RULE_COUNT'))):
            for name in names:
                with self.subTest(name=name):
                    self.assertIs(getattr(legacy,name),getattr(owner,name))
    def test_selected_caller_uses_grouped_owners(self):
        for owner, names in ((inventory, ('persist_inventory','persist_ppfas_structure','persist_ppfas_structure_failure')), (holdings, ('persist_ppfas_holdings',))):
            for name in names:
                self.assertIs(getattr(import_mf,name),getattr(owner,name))
                self.assertEqual(getattr(import_mf,name).__module__,owner.__name__)
        self.assertIs(import_mf.finalize_ppfas_import,legacy.finalize_ppfas_import)
        self.assertEqual(legacy.finalize_ppfas_import.__module__,'scrapper.db.mutual_funds.scheme_acceptance')
    def test_types_remain_frozen_and_fields_compatible(self):
        value=legacy.PersistedImport(1,2,3,'inbox',False)
        self.assertEqual(dataclasses.asdict(value),dict(import_file_id=1,workbook_blob_id=2,import_observation_id=3,import_state='inbox',duplicate=False))
        with self.assertRaises(dataclasses.FrozenInstanceError):value.import_state='accepted'
        summary=legacy.HoldingsNormalizationSummary(*range(12))
        self.assertEqual(summary.to_dict(),dataclasses.asdict(summary))
    def test_db_owners_have_no_reverse_layer_imports(self):
        for module in (inventory,holdings):
            tree=ast.parse(Path(inspect.getfile(module)).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse(set((node.module or '').split('.')) & {'cli','commands','view','operations','pipeline','workflows'})
        self.assertNotIn('def persist_inventory(',inspect.getsource(legacy))
        self.assertNotIn('def persist_ppfas_holdings(',inspect.getsource(legacy))
