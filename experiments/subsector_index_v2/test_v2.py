import copy, json, unittest
from decimal import Decimal as D
from .build import load,calculate,SETS,PRIMARY,CUTOFFS,OUT,INPUT,V1,read_csv,core,study

class V2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles,cls.nifty=load()
        cls.stocks,cls.indices,cls.loo,cls.cal,cls.bm=calculate(cls.candles)

    def test_core_business_boundaries(self):
        self.assertNotIn('TOLINS',SETS['TYRES']);self.assertIn('TOLINS',SETS['TYRES_WITH_RETREAD'])
        self.assertNotIn('PGEL',SETS['EMS']);self.assertNotIn('CENTUM',SETS['EMS'])
        self.assertNotIn('POWERINDIA',SETS['TRANSFORMERS']);self.assertIn('POWERINDIA',SETS['TRANSFORMERS_BROAD'])

    def test_exact_instrument_and_historical_listing(self):
        plan=json.loads((OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json').read_text())
        regs=read_csv(INPUT/'registry.csv')
        for r in plan['requests']:
            match=[x for x in regs if x['exchange']=='NSE' and x['symbol']==r['symbol']]
            self.assertEqual(len(match),1);self.assertEqual(r['instrument_key'],'NSE_EQ|'+match[0]['isin'])
            self.assertLessEqual(r['listing_date'],r['from_date'])
        self.assertEqual(plan['request_count'],8)

    def test_observed_grid_and_normalized_base(self):
        self.assertEqual(len(self.cal),227)
        for rows in self.indices.values():
            self.assertEqual([r['date'] for r in rows],self.cal);self.assertEqual(rows[0]['index_close'],1000)
            self.assertTrue(all(r['price_coverage_pct']==100 for r in rows))

    def test_actual_loo_independence_under_target_perturbation(self):
        changed=copy.deepcopy(self.candles)
        for r in changed:
            if r['symbol']=='CEATLTD':r['raw_close']*=D('1.13') if r['date']>'2026-05-01' else 1
        _,indices,loo,_,_=calculate(changed)
        for q in CUTOFFS:
            self.assertEqual(self.loo[q,'TYRES','CEATLTD'],loo[q,'TYRES','CEATLTD'])
            self.assertNotEqual(self.indices[q,'TYRES'],indices[q,'TYRES'])
        for (q,g,s),rows in self.loo.items():
            self.assertTrue(all(s not in r['members'].split(';') and r['expected_constituents']>=2 for r in rows))

    def test_missing_member_breaks_chain_no_reweighting(self):
        missing=[r for r in self.candles if not(r['symbol']=='AVALON' and r['date']==self.cal[100])]
        _,indices,_,_,_=calculate(missing)
        rows=indices['FY2027-Q1','EMS']
        self.assertIsNotNone(rows[99]['index_close']);self.assertTrue(all(r['index_close'] is None for r in rows[100:]))

    def test_insufficient_peer_set(self):
        self.assertEqual(core.equal_weight_index(self.stocks,self.cal,self.bm,members=('SYRMA',),min_members=2),[])

    def test_nifty_missing_tail_not_filled(self):
        receipt=OUT/'nifty-tail-execution.json'
        complete=receipt.exists() and json.loads(receipt.read_text()).get('complete')
        if complete:
            self.assertTrue(all(d in self.nifty for d in self.cal));self.assertEqual(max(self.nifty),'2026-10-30')
        else:
            self.assertEqual(max(self.nifty),'2026-08-14');self.assertNotIn(self.cal[-1],self.nifty)

    def test_original_cohort_assessments_and_blue_correction(self):
        focus=read_csv(INPUT/'corrected-current-q2.csv');original=read_csv(INPUT/'all-q2-assessments.csv')
        self.assertEqual(len(focus),117);self.assertEqual(len(original),120)
        blue=read_csv(OUT/'BLUE_IDENTITY_OVERLAY.csv');self.assertEqual(len(blue),27)
        self.assertIn('NSE:SAMBHV',{r['corrected_token'] for r in blue})
        self.assertNotIn('NSE:SAMBHAAV',{r['corrected_token'] for r in blue})
        self.assertIn('NSE:ATHERENERG',{r['identity_key'] for r in focus})

    def test_action_uncertainty_explicit_and_no_synthetic_ohlc(self):
        for rows in self.indices.values():
            for r in rows:
                self.assertIn('PROVISIONAL',r['adjustment_certification'])
                self.assertFalse(any(k in r for k in ('open','high','low','volume')))
        review=json.loads((INPUT/'peer-action-review.json').read_text())
        self.assertEqual(len(review['members']),8)

if __name__=='__main__':unittest.main()
