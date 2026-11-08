"""Offline regression tests for selective activation and stop conditions."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


host = module('nse_results')
runtime = module('nse_results_runtime')


class ActivationTests(unittest.TestCase):
    def test_exact_accepted_configuration(self):
        self.assertEqual(host.check_configuration()['image_reference'], host.IMAGE)

    def test_preview_budgets_and_guard_cannot_be_overridden(self):
        preview = {'validation': {'status': 'passed'}, 'accepted_row_count': 1, 'enqueue': {'queued': 100}}
        runtime.validate_preview(preview)
        for change in ({'validation': {'status': 'failed'}}, {'accepted_row_count': 0},
                       {'accepted_row_count': 6001}, {'enqueue': {'queued': 101}}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                runtime.validate_preview(preview | change)

    def test_total_budgets_include_prior_period_snapshot(self):
        preview = {'validation': {'status': 'passed'}, 'accepted_row_count': 2, 'enqueue': {'queued': 2}}
        runtime.validate_preview(preview, {'rows': 11998, 'queued': 98})
        for totals in ({'rows': 11999, 'queued': 98}, {'rows': 11998, 'queued': 99}):
            with self.subTest(totals=totals), self.assertRaises(RuntimeError):
                runtime.validate_preview(preview, totals)

    def test_busy_bse_day_keeps_validation_and_queue_guards(self):
        preview = {'validation': {'status': 'passed'}, 'accepted_row_count': 1500,
                   'enqueue': {'queued': 108}}
        for queued in (108, 501, 2000):
            runtime.validate_preview(preview | {'enqueue': {'queued': queued}},
                                     queue_limit=runtime.BSE_QUEUE_LIMIT)
        for change in ({'enqueue': {'queued': 2001}}, {'validation': {'status': 'failed'}}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                runtime.validate_preview(preview | change, queue_limit=runtime.BSE_QUEUE_LIMIT)
        # NSE retains its bounded queue admission contract.
        with self.assertRaises(RuntimeError):
            runtime.validate_preview(preview)

    def test_nse_two_real_quarters_fit_combined_row_budget(self):
        preview = {'validation': {'status': 'passed'}, 'accepted_row_count': 4430,
                   'enqueue': {'queued': 13}}
        runtime.validate_preview(preview, {'rows': 1638, 'queued': 100}, admission_limit=0)
        with self.assertRaises(RuntimeError):
            runtime.validate_preview(preview | {'accepted_row_count': 6001}, admission_limit=0)

    def test_sealed_image_cannot_execute_unpackaged_application_changes(self):
        host.require_packaged_period_logic()
        with patch.object(host, 'SOURCE', host.ROOT / 'docs/acquisition-readiness/baselines/AR-02-combined-20261008/source-manifest.json'), \
             patch.object(host, 'RELEASE', host.BASELINE_RELEASE):
            with self.assertRaisesRegex(RuntimeError, 'replacement release'):
                host.require_packaged_period_logic()

    def test_default_compose_has_no_fixed_period(self):
        config = json.loads((host.HERE / 'compose.nse.json').read_text())
        self.assertEqual(config['services']['nse-results']['command'], [])

    def test_offline_default_and_override_do_not_inspect_production(self):
        for argv in (['runner'], ['runner', '--period', '2024-03']):
            with patch.object(host.sys, 'argv', argv), patch.object(host, 'checked') as checked:
                self.assertEqual(host.main(), 0)
                checked.assert_not_called()

    def test_shared_verified_owner_gate_is_used(self):
        gate = SimpleNamespace(owner_gate=lambda: {'status': 'PASS'})
        with patch.dict(host.sys.modules, {'production_ownership': gate}):
            self.assertEqual(host.owner_gate()['status'], 'PASS')
        gate = SimpleNamespace(owner_gate=lambda: (_ for _ in ()).throw(RuntimeError('production owner')))
        with patch.dict(host.sys.modules, {'production_ownership': gate}):
            with self.assertRaisesRegex(RuntimeError, 'production owner'):
                host.owner_gate()


if __name__ == '__main__':
    unittest.main()
