"""Synthetic regressions for build identity, action and coverage contracts."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scrapper.indices.catalog import research_catalog
from scrapper.indices.production import ordinary_securities, action_inputs, build
from scrapper.indices.frames_build import extend


def security(symbol, isin, linked):
    return dict(exchange='NSE',segment='equity',symbol=symbol,isin=isin,
                existing_registry_company_id=linked)


class ProductionIndexTests(unittest.TestCase):
    def test_catalog_preserves_ordinary_and_rights_mapping_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'research.sqlite3'
            c=sqlite3.connect(path)
            c.executescript('''
                CREATE TABLE ss04_decisions(company_id INTEGER,major_scope INTEGER,primary_code TEXT,status TEXT,decision_json TEXT);
                CREATE TABLE ss02_security_mapping(company_id INTEGER,isin TEXT,security_evidence_json TEXT);
                CREATE TABLE ss04_taxonomy(code TEXT,label TEXT);
                CREATE TABLE ss04_exposures(company_id INTEGER,role TEXT);
            ''')
            c.execute("INSERT INTO ss04_decisions VALUES(1,1,'HOTELS','VERIFIED_RESEARCH','{}')")
            c.execute("INSERT INTO ss04_taxonomy VALUES('HOTELS','Hotels')")
            for s in [security('ORDINARY','INE001',1),security('ORDINARY-RE','INE002',None)]:
                c.execute('INSERT INTO ss02_security_mapping VALUES(?,?,?)',(1,s['isin'],json.dumps(dict(securities=[s]))))
            c.commit();c.close()
            member=research_catalog(path)['groups'][0]['members'][0]
            evidence=member['security_evidence']
            self.assertEqual(len(evidence['records']),2)
            self.assertEqual(len(evidence['securities']),2)
            self.assertEqual(list(ordinary_securities(evidence['securities'],1)),['INE001'])

    def test_rights_never_replace_linked_ordinary_security(self):
        rows=[security('ORDINARY','INE001',1),security('ORDINARY-RE','INE002',None),
              security('OTHER','INE003',2),security('MISTAKEN-RE','INE004',1)]
        self.assertEqual(list(ordinary_securities(rows,1)),['INE001'])
        rows.append(security('SECOND_ORDINARY','INE005',1))
        self.assertEqual(len(ordinary_securities(rows,1)),2)  # Caller must mark ambiguity unavailable.

    def test_action_inputs_preserve_hashes_and_reject_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            event=dict(symbol='A',ex_date='2026-11-04',kind='SPLIT',provider_basis='UNKNOWN',new_shares_per_old_share='2')
            a=root/'artifacts/subsector-index-v1/inputs/corporate-action-review.json'
            b=root/'artifacts/subsector-index-v2/inputs/peer-action-review.json'
            a.parent.mkdir(parents=True);b.parent.mkdir(parents=True)
            a.write_text(json.dumps(dict(in_window_share_unit_events=[event])))
            b.write_text(json.dumps(dict(confirmed_in_window_events=[event])))
            result=action_inputs(root)
            self.assertEqual(result['events'],[event]);self.assertEqual(len(result['reviews']),2)
            self.assertTrue(all(len(r['sha256'])==64 for r in result['reviews']))
            b.write_text(json.dumps(dict(events=[dict(event,new_shares_per_old_share='3')])))
            with self.assertRaisesRegex(ValueError,'Conflicting action'):
                action_inputs(root)

    def test_daily_and_loo_receive_actions_and_report_last_valid_date(self):
        days=['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06']
        members=[dict(symbol=s,company_id=i,instrument_key='NSE_EQ|'+s,listing_date='2020-01-01') for i,s in enumerate('ABC',1)]
        group=dict(code='TEST',members=members,unresolved=[])
        event=dict(symbol='A',ex_date='2026-11-04',kind='SPLIT',provider_basis='UNKNOWN',new_shares_per_old_share='2')
        def candles(store,company_id,symbol,**kwargs):
            rows=[]
            for i,d in enumerate(days):
                p='50' if symbol=='A' and i>=2 else '100'
                rows.append(dict(symbol=symbol,instrument_key='NSE_EQ|'+symbol,date=d,
                                 raw_open=p,raw_high=p,raw_low=p,raw_close=p,raw_volume='100'))
            return dict(rows=rows,exclusions=[],source_revisions=[])
        with tempfile.TemporaryDirectory() as tmp, \
             patch('scrapper.indices.production.membership',return_value=[group]), \
             patch('scrapper.indices.production.action_inputs',return_value=dict(events=[event],reviews=[])), \
             patch('scrapper.indices.production.HistoricalStore'), \
             patch('scrapper.indices.official.load_official',return_value=dict(bars={})), \
             patch('scrapper.indices.passport.daily_prices',side_effect=candles):
            destination=Path(tmp)/'build';build(destination,start=days[0],end=days[-1])
            data=json.loads((destination/'datasets.json').read_text())['TEST']
            self.assertEqual(data['coverage']['status'],'PARTIAL')
            self.assertEqual(data['coverage']['last_date'],days[-1])
            self.assertEqual(data['coverage']['last_valid_date'],days[1])
            self.assertIsNone(data['daily'][2]['index_close'])
            self.assertIsNotNone(data['leave_one_out']['A'][-1]['index_close'])
            self.assertIsNone(data['leave_one_out']['B'][-1]['index_close'])
            self.assertEqual(json.loads((destination/'action-evidence.json').read_text())['events'],[event])

    def test_frames_pass_frozen_actions_to_every_full_and_loo_calculation(self):
        event=dict(symbol='A',ex_date='2026-11-04',kind='SPLIT',provider_basis='UNKNOWN',new_shares_per_old_share='2')
        members=[dict(symbol=s,company_id=i,instrument_key='NSE_EQ|'+s,listing_date='2020-01-01') for i,s in enumerate('ABC',1)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            inputs={'datasets.json':dict(TEST=dict(members=list('ABC'),coverage=dict(first_date='2026-10-26'))),
                    'constituent-candles.json':dict(A=[],B=[],C=[]),
                    'coverage.json':dict(start='2026-10-26',end='2026-11-08'),
                    'action-evidence.json':dict(events=[event]),
                    'membership.json':[dict(code='TEST',members=members)],'official.json':dict(bars={})}
            for name,value in inputs.items():(root/name).write_text(json.dumps(value))
            def rows(*args,**kwargs):
                return [dict(date='2026-10-26T09:15:00+05:30',index_close='1000'),dict(date='2026-11-02T09:15:00+05:30',index_close='1001')]
            with patch('scrapper.indices.frames_build.HistoricalStore') as store, \
                 patch('scrapper.indices.benchmark_intraday.load_benchmarks',return_value={'1-hour':[],'4-hour':[]}), \
                 patch('scrapper.indices.frames_build.calculate',side_effect=rows) as calculate:
                store.return_value.read.return_value=dict(rows=[],exclusions=[],source_revisions=[])
                extend(root,intraday_start='2026-10-26')
                self.assertEqual(calculate.call_count,12)  # 3 full frames plus 3 leave-one-out per frame.
                self.assertTrue(all(call.kwargs['actions']==[event] for call in calculate.call_args_list))
                coverage=json.loads((root/'timeframes/coverage.json').read_text())
                self.assertTrue(all(r['last_valid_date']=='2026-11-02T09:15:00+05:30' for r in coverage))
