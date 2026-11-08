"""Reporting byte contracts and preserved Trendlyne callers."""
import ast
import importlib
import inspect
import json
import pickle
import tempfile
import unittest
from pathlib import Path
from scrapper import cli
from scrapper.exports.writers import write_json_payload
GROUPS = {'trendlyne_vertical': ['TrendlyneSliceResult', 'ingest_trendlyne_response', 'scrape_trendlyne_company'], 'trendlyne_pilot': ['PILOT_CODE', 'PILOT_SYMBOLS', 'MIN_MONTHS', 'MAX_MONTHS', 'MAX_BATCH_SIZE', 'TrendlynePilotRequest', 'TrendlynePilotPlan', 'TrendlynePilotRunResult', '_canonical_periods', '_pilot_mappings', '_accepted_parse_id', 'build_trendlyne_pilot_plan', '_persist_plan', '_reconcile_plan', 'register_trendlyne_pilot_plan', 'run_trendlyne_pilot_batch']}
class ReportingTrendlyneTests(unittest.TestCase):
    def test_legacy_definitions_and_pickle_lookup(self):
        for name, symbols in GROUPS.items():
            old=importlib.import_module('scrapper.pipeline.'+name)
            owner=importlib.import_module('scrapper.workflows.mutual_funds.'+name)
            for symbol in symbols:
                self.assertIs(getattr(old,symbol),getattr(owner,symbol))
        self.assertIs(pickle.loads(b'cscrapper.pipeline.trendlyne_vertical\nTrendlyneSliceResult\n.'),importlib.import_module('scrapper.workflows.mutual_funds.trendlyne_vertical').TrendlyneSliceResult)
    def test_cli_routes_and_frozen_pilot_bounds(self):
        source=inspect.getsource(cli)
        for name in GROUPS:
            self.assertIn('.workflows.mutual_funds.'+name,source)
            self.assertNotIn('.pipeline.'+name,source)
        from scrapper.workflows.mutual_funds import trendlyne_pilot as pilot
        self.assertEqual((pilot.PILOT_SYMBOLS,pilot.MIN_MONTHS,pilot.MAX_MONTHS,pilot.MAX_BATCH_SIZE),(('RBA',),6,12,3))
        self.assertIs(cli.PILOT_SYMBOLS,pilot.PILOT_SYMBOLS)
    def test_shared_writer_exact_bytes_and_error_order(self):
        payload={'z':None,'unicode':'é ₹','a':[False,1,1.5]}
        with tempfile.TemporaryDirectory() as root:
            out=Path(root)/'report.json'
            for ascii_value in (True,False):
                self.assertIs(write_json_payload(payload,out,ensure_ascii=ascii_value),out)
                self.assertEqual(out.read_bytes(),(json.dumps(payload,indent=2,sort_keys=True,ensure_ascii=ascii_value)+'\n').encode('utf-8'))
            out.write_bytes(b'previous')
            with self.assertRaises(TypeError):write_json_payload({'bad':object()},out)
            self.assertEqual(out.read_bytes(),b'previous')
            with self.assertRaises(FileNotFoundError):write_json_payload(payload,Path(root)/'missing'/'report.json')
    def test_no_reverse_imports_and_shared_writer_callers(self):
        for name in GROUPS:
            owner=importlib.import_module('scrapper.workflows.mutual_funds.'+name)
            for node in ast.walk(ast.parse(inspect.getsource(owner))):
                if isinstance(node,ast.ImportFrom):self.assertFalse(set((node.module or '').split('.')) & {'pipeline','cli','commands','view','operations'})
        for name in ['mf_workbook_json_export','mf_feature_json_export','mfdata_crosscheck_json_export','mf_coverage_report','trendlyne_json_export','json_export']:
            module=importlib.import_module('scrapper.exports.'+name)
            self.assertIs(module.write_json_payload,write_json_payload)
