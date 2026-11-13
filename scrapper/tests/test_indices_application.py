import unittest
from decimal import Decimal as D
from scrapper.indices.service import daily, validate, weekly


def prices():
    return {s:[dict(symbol=s,instrument_key=s,date=d,raw_open=p,raw_high=p,raw_low=p,raw_close=p,raw_volume='100')
               for d,p in zip(['2026-11-02','2026-11-03','2026-11-04'],ps)]
            for s,ps in [('A',['100','110','121']),('B',['100','90','90'])]}


class IndexApplicationTests(unittest.TestCase):
    def test_independent_returns(self):
        p=prices(); days=[r['date'] for r in p['A']]
        rows=daily(p,days,{},members=['A','B'])
        self.assertEqual([r['index_close'] for r in rows],[D(1000),D(1000),D(1050)])
        self.assertEqual(rows[-1]['rising_count'],1)
        self.assertEqual(rows[-1]['traded_value_proxy_crore'],D('0.00211'))

    def test_missing_member_breaks_chain(self):
        p=prices();days=[r['date'] for r in p['A']];p['B'].pop(1)
        rows=daily(p,days,{},members=['A','B'])
        self.assertIsNone(rows[-1]['index_close'])
        self.assertIn('B',rows[1]['missing_members'])

    def test_invalid_and_prelisting(self):
        p=prices()['A'];p[0]['raw_high']='99'
        with self.assertRaises(ValueError):validate(p)
        with self.assertRaises(ValueError):validate(prices()['A'],'2026-11-03')

    def test_split_and_unknown_basis(self):
        from scrapper.indices.core import normalise_prices
        p=prices()['A'];p[1]['raw_close']='50'
        a=dict(ex_date='2026-11-03',kind='SPLIT',new_shares_per_old_share='2',provider_basis='UNADJUSTED_CONFIRMED')
        self.assertEqual(normalise_prices(p,[a])[1]['adjusted_close'],D(100))
        a['provider_basis']='UNKNOWN'
        self.assertIsNone(normalise_prices(p,[a])[1]['adjusted_close'])

    def test_week_never_emits_before_completion(self):
        p=prices();days=[r['date'] for r in p['A']];rows=daily(p,days,{},members=['A','B'])
        self.assertEqual(weekly(rows,days,completed_through=days[-1]),[])

    def test_membership(self):
        for members in (['A'],['A','A']):
            with self.assertRaises(ValueError):daily(prices(),['2026-11-02'],{},members=members)

    def test_json_benchmark_and_exact_rs55(self):
        from datetime import date, timedelta
        days=[str(date(2025,1,1)+timedelta(days=i)) for i in range(56)]
        p={s:[dict(symbol=s,instrument_key=s,date=d,raw_open='100',raw_high='100',raw_low='100',raw_close='100',raw_volume='100') for d in days] for s in ('A','B')}
        rows=daily(p,days,{d:'200' for d in days},members=['A','B'])
        self.assertEqual(rows[-1]['rs55'],D(0))
        self.assertIsNone(rows[-2]['rs55'])

    def test_offline_build(self):
        import json, tempfile
        from pathlib import Path
        from scrapper.indices.__main__ import build
        p=prices()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'input.json'
            source.write_text(json.dumps(dict(groups=[dict(code='SYNTHETIC',members=['A','B'],membership_status='VERIFIED_RETROSPECTIVE')],prices=p,calendar=[r['date'] for r in p['A']],benchmark={})))
            result=build(source,root/'output')
            self.assertEqual(result['coverage'][0]['status'],'COMPLETED')
            self.assertTrue((root/'output/index.html').exists())
            self.assertTrue((root/'output/lightweight-charts.js').exists())
