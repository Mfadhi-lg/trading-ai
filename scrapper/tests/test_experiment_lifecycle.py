"""Keep experiment coverage and direct operational integrations reviewable.

Parse source only: importing experiment runners can open stores or write reports.
"""
import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ExperimentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.register = json.loads((ROOT / 'experiments/lifecycle.json').read_text())

    def test_every_experiment_has_an_accountable_interface_and_gate(self):
        rows = self.register['experiments']
        names = [r['name'] for r in rows]
        directories = {
            p.name for p in (ROOT / 'experiments').iterdir()
            if p.is_dir() and not p.name.startswith(('.', '__'))
        }
        self.assertEqual(len(names), len(set(names)), 'duplicate experiment registration')
        self.assertEqual(set(names), directories, 'update lifecycle register for added/removed experiments')
        for row in rows:
            with self.subTest(experiment=row['name']):
                self.assertIn(row['status'], {
                    'production', 'research_only', 'deprecated', 'pending_validation',
                })
                for key in ('owner', 'interface', 'validation_gate'):
                    self.assertTrue(row[key].strip(), f'missing {key}')

    def test_direct_operational_imports_are_explicit(self):
        actual = set()
        for base in ('scrapper', 'ops', 'scripts'):
            for path in (ROOT / base).rglob('*'):
                if not path.is_file() or path.name.startswith('test_') or '.venv' in path.relative_to(ROOT).parts:
                    continue
                if path.suffix != '.py' and not (base == 'scripts' and not path.suffix):
                    continue
                # Non-Python suffixless launchers are outside this AST check.
                try:
                    tree = ast.parse(path.read_text())
                except (SyntaxError, UnicodeDecodeError):
                    if path.suffix == '.py':
                        raise
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        modules = [node.module] if node.module else []
                        if node.module == 'experiments':
                            modules = ['experiments.' + a.name for a in node.names]
                    elif isinstance(node, ast.Import):
                        modules = [a.name for a in node.names]
                    else:
                        continue
                    for module in modules:
                        if module.startswith('experiments.'):
                            actual.add((str(path.relative_to(ROOT)), module))
        recorded = {(r['consumer'], r['module']) for r in self.register['operational_imports']}
        self.assertEqual(actual, recorded, 'review and register changed operational experiment imports')
        names = {r['name'] for r in self.register['experiments']}
        for consumer, module in recorded:
            self.assertIn(module.split('.')[1], names)
            self.assertTrue((ROOT / consumer).is_file())


if __name__ == '__main__':
    unittest.main()
