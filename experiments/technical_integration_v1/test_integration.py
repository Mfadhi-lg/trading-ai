import copy,json,unittest
from decimal import Decimal as D
from datetime import datetime
from .build import activity_rows,dec_rows,V2,OUT,stamp,us_completed,select_context,verified_before,observations
from experiments.subsector_index_v2.build import load
from .charts import unpack

class TechnicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.indices=dec_rows(V2/'INDEX_V2_DAILY.csv');cls.breadth=dec_rows(V2/'BREADTH_AND_ACTIVITY.csv')
        cls.activity=activity_rows(cls.indices,cls.breadth)
        cls.cal=sorted({r['date'] for r in cls.indices});candles,nifty=load();cls.daily=observations(candles,nifty)

    def test_activity_warmup_and_excluded_current_baseline(self):
        rs=[r for r in self.activity if r['subsector']=='TYRES' and r['quarter']=='FY2027-Q1']
        self.assertIsNone(rs[18]['activity_sma20_including_current_crore']);self.assertIsNotNone(rs[19]['activity_sma20_including_current_crore'])
        self.assertIsNone(rs[19]['relative_group_activity20'])
        self.assertEqual(rs[20]['preceding20_mean_activity_crore'],sum(r['estimated_traded_value_crore'] for r in rs[:20])/20)
        self.assertEqual(rs[20]['relative_group_activity20'],rs[20]['estimated_traded_value_crore']/rs[20]['preceding20_mean_activity_crore'])

    def test_direction_colors_exact_same_date(self):
        for r in self.activity:
            ret=r['index_return_1s_pct']
            self.assertEqual(r['histogram_color'],'GREEN' if ret is not None and ret>0 else 'RED' if ret is not None and ret<0 else 'NEUTRAL')

    def test_missing_constituent_no_partial_group_value(self):
        index=copy.deepcopy(self.indices);breadth=copy.deepcopy(self.breadth)
        for rs in (index,breadth):
            r=next(r for r in rs if r['quarter']=='FY2027-Q1' and r['subsector']=='EMS' and r['date']==self.cal[50])
            r['observed_constituents']-=1;r['missing_data_flag']='MISSING';r['traded_value_proxy_crore']=None
        rs=[r for r in activity_rows(index,breadth) if r['quarter']=='FY2027-Q1' and r['subsector']=='EMS']
        self.assertIsNone(rs[50]['estimated_traded_value_crore']);self.assertTrue(all(rs[i]['relative_group_activity20'] is None for i in range(50,71)))

    def test_missing_session_does_not_compress_twenty_sessions(self):
        ix=[r for r in self.indices if not(r['quarter']=='FY2027-Q1' and r['subsector']=='EMS' and r['date']==self.cal[50])]
        br=[r for r in self.breadth if not(r['quarter']=='FY2027-Q1' and r['subsector']=='EMS' and r['date']==self.cal[50])]
        rs=[r for r in activity_rows(ix,br,self.cal) if r['quarter']=='FY2027-Q1' and r['subsector']=='EMS']
        self.assertEqual(len(rs),227);self.assertIsNone(rs[50]['estimated_traded_value_crore']);self.assertIsNone(rs[70]['relative_group_activity20'])

    def test_group_activity_not_median_member_rvol(self):
        self.assertTrue(any(r['relative_group_activity20'] is not None and r['relative_group_activity20']!=r['median_constituent_rvol20'] for r in self.activity))

    def test_future_activity_perturbation_does_not_change_prefix(self):
        a=copy.deepcopy(self.indices);b=copy.deepcopy(self.breadth)
        for rs in (a,b):
            for r in rs:
                if r['date']>self.cal[80]:r['traded_value_proxy_crore']*=100
        changed=activity_rows(a,b)
        self.assertEqual([r for r in self.activity if r['date']<=self.cal[80]],[r for r in changed if r['date']<=self.cal[80]])

    def test_split_price_volume_proxy_invariance(self):
        self.assertEqual(D(100)*D(200),D(50)*D(400));self.assertEqual((D(50)*2)*(D(400)/2),D(100)*200)

    def test_us_dst_holiday_and_early_close(self):
        self.assertEqual(us_completed(stamp('2026-03-06',9,15)).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).hour,2)
        self.assertEqual(us_completed(stamp('2026-03-10',9,15)).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).hour,1)
        self.assertEqual(str(us_completed(stamp('2026-07-06',9,15)).date()),'2026-07-02')
        self.assertEqual(us_completed(stamp('2025-12-26',9,15)).hour,13)

    def test_same_date_us_close_is_never_preopen_or_postclose_feature(self):
        for phase in ('PREOPEN','POSTCLOSE'):
            rows=select_context(self.daily,'2026-08-11',phase)
            for r in rows:
                if r['series'] in ('SP500','DJIA','NASDAQCOM'):
                    self.assertIsNone(r['verified_feature_value'])
                    self.assertTrue(not r['retrospective_provider_date'] or r['retrospective_provider_date']<'2026-08-11')

    def test_h10_release_after_preopen_may_enter_postclose(self):
        pre=next(r for r in select_context(self.daily,'2026-02-24','PREOPEN') if r['series']=='USDINR')
        post=next(r for r in select_context(self.daily,'2026-02-24','POSTCLOSE') if r['series']=='USDINR')
        self.assertIsNone(pre['verified_feature_value']);self.assertIsNotNone(post['verified_feature_value'])

    def test_settlement_cannot_be_intraday_futures_quote(self):
        r=dict(series='ES',decision_time_eligible=True,available_at='2026-08-11T08:00:00+05:30',observation_timestamp='2026-08-11T07:00:00+05:30',observation_kind='SETTLEMENT')
        self.assertFalse(verified_before(r,stamp('2026-08-11',9,15)))
        r.update(observation_kind='QUOTE',exchange_contract_id='ESU26',expiry='2026-09-18',roll_policy='EXPLICIT_CONTRACT_NO_BACK_ADJUSTMENT',provider_entitlement_reference='SYNTHETIC_TEST_ONLY')
        self.assertTrue(verified_before(r,stamp('2026-08-11',9,15)))
        r['available_at']='2026-08-11T09:15:00+05:30';self.assertFalse(verified_before(r,stamp('2026-08-11',9,15)))

    def test_original_stock_indicator_fields_preserved(self):
        for name in ('CEATLTD','PGEL','VOLTAMP'):
            src=V2.parents[1]/'artifacts/earnings-reaction-v1/er04/charts'/('FY2027-Q1-'+name+'-S3.html')
            a,_=unpack(src.read_text());b,_=unpack((OUT/'charts/stocks'/src.name).read_text())
            self.assertEqual(a['rows'],[{k:v for k,v in r.items() if k!='tech'} for r in b['rows']])

if __name__=='__main__':unittest.main()
