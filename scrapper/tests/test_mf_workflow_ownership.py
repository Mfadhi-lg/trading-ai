"""Workflow ownership and legacy lookup contracts for C24.3."""
import ast
import dataclasses
import importlib
import inspect
import pickle
import unittest
from scrapper import cli
from scrapper.workflows.mutual_funds import import_mf, crosscheck_mfdata
GROUPS = {'import_mf': ['PUBLISHER_RE', 'MONTH_RE', 'IMPORT_PLAN_SCHEMA_VERSION', 'ProposedImport', 'normalize_publisher', 'validate_month', '_candidate_files', '_destination', '_known_inventory', 'plan_import', 'plan_directory_import', 'import_plan_payload', '_copy_immutable', 'execute_import'], 'crosscheck_mfdata': ['execute_mfdata_crosscheck']}
class WorkflowOwnershipTests(unittest.TestCase):
    def test_all_legacy_definitions_are_owner_objects(self):
        for name, symbols in GROUPS.items():
            old=importlib.import_module('scrapper.pipeline.'+name)
            owner=importlib.import_module('scrapper.workflows.mutual_funds.'+name)
            for symbol in symbols:
                with self.subTest(module=name, symbol=symbol):
                    self.assertIs(getattr(old,symbol),getattr(owner,symbol))
    def test_real_cli_lazy_targets_use_new_owners(self):
        source=inspect.getsource(cli)
        for name in GROUPS:
            self.assertIn('.workflows.mutual_funds.'+name,source)
            self.assertNotIn('.pipeline.'+name,source)
        for name in ['plan_import','execute_import','execute_mfdata_crosscheck']:
            self.assertTrue(callable(getattr(cli,name)))
    def test_legacy_pickle_class_lookup_and_frozen_dto(self):
        self.assertIs(pickle.loads(b'cscrapper.pipeline.import_mf\nProposedImport\n.'),import_mf.ProposedImport)
        self.assertTrue(import_mf.ProposedImport.__dataclass_params__.frozen)
        self.assertEqual([f.name for f in dataclasses.fields(import_mf.ProposedImport)],['publisher','disclosure_month','inspection','proposed_action','proposed_destination','structure','structure_error'])
    def test_owners_do_not_import_facades_or_ui(self):
        for module in [import_mf,crosscheck_mfdata]:
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse(set((node.module or '').split('.')) & {'pipeline','cli','commands','view','operations'})
