"""Ownership, compatibility and dependency contracts for C24.2."""
import ast
import dataclasses
import importlib
import inspect
import pickle
import unittest
from pathlib import Path
from scrapper.db.mutual_funds import scheme_acceptance, changes, features, feature_metrics
from scrapper.pipeline import import_mf

GROUPS = {'mf_workbook_repo': ['SchemeRegistrySummary', 'ImportFinalization', 'M04_VALIDATION_RULES', '_resolved_classification', 'persist_scheme_registry', '_transition_import_state', 'finalize_ppfas_import'], 'mf_change_repo': ['CHANGE_ENGINE_VERSION', 'MONTH_RE', 'ChangeGenerationSummary', 'ChangeRefreshSummary', '_json', '_month_parts', 'adjacent_month', '_decimal', '_decimal_text', '_pointer_evidence', '_eligible_holdings', '_candidate_scheme_ids', '_change_class', '_persist_change_validations', 'generate_scheme_month_changes', 'refresh_changes_for_accepted_month'], 'mf_feature_repo': ['FEATURE_ENGINE_VERSION', 'FEATURE_SCOPES', 'FeatureGenerationSummary', '_json', '_effective_classification', '_pair_inputs', '_summary', '_persist_validations', 'generate_stock_month_features', 'derive_stock_month_features_through'], 'mf_feature_metrics': ['decimal_value', 'decimal_text', 'is_active_equity', 'aggregate_feature_rows']}
OWNERS = dict(zip(GROUPS, (scheme_acceptance, changes, features, feature_metrics)))

class SchemeOwnershipTests(unittest.TestCase):
    def test_every_legacy_definition_resolves_to_owner(self):
        for legacy, names in GROUPS.items():
            old = importlib.import_module('scrapper.db.' + legacy)
            for name in names:
                with self.subTest(legacy=legacy, name=name):
                    self.assertIs(getattr(old, name), getattr(OWNERS[legacy], name))
    def test_selected_real_callers_use_grouped_owners(self):
        for name, owner in [('finalize_ppfas_import', scheme_acceptance), ('refresh_changes_for_accepted_month', changes), ('generate_stock_month_features', features)]:
            self.assertIs(getattr(import_mf, name), getattr(owner, name))
            self.assertEqual(getattr(import_mf, name).__module__, owner.__name__)
        cli = Path(__file__).parents[1] / 'cli/__init__.py'
        export = Path(__file__).parents[1] / 'exports/mf_feature_json_export.py'
        self.assertNotIn('.db.mf_feature_repo', cli.read_text())
        self.assertNotIn('.db.mf_feature_repo', export.read_text())
    def test_dto_shape_freezing_and_legacy_pickle_lookup(self):
        value = scheme_acceptance.SchemeRegistrySummary(1, 2, 3, 4, 'v1')
        self.assertEqual(value.to_dict(), dict(scheme_count=1, active_equity_scheme_count=2, classified_holding_count=3, active_equity_holding_count=4, classification_rule_version='v1'))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.scheme_count = 0
        for old, owner in [('mf_workbook_repo', scheme_acceptance), ('mf_change_repo', changes), ('mf_feature_repo', features)]:
            for name in GROUPS[old]:
                obj = getattr(owner, name)
                if isinstance(obj, type) and dataclasses.is_dataclass(obj):
                    self.assertIs(pickle.loads(('cscrapper.db.' + old + '\n' + name + '\n.').encode()), obj)
    def test_new_owners_have_no_reverse_imports_or_facade_dependency(self):
        forbidden = {'cli', 'commands', 'view', 'operations', 'pipeline', 'workflows', *GROUPS}
        for owner in OWNERS.values():
            for node in ast.walk(ast.parse(inspect.getsource(owner))):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(set((node.module or '').split('.')) & forbidden)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(set(alias.name.split('.')) & forbidden)
