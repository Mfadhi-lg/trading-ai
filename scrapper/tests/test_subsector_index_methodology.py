"""Portable basket methodology checks; no retained data or network requests."""
from decimal import Decimal
import unittest

from scrapper.research.subsector_ss02.full_universe import eligibility, initial_members, select
from scrapper.research.subsector_ss02 import policy


class SubsectorIndexMethodologyTests(unittest.TestCase):
    cutoff = policy.CUTOFFS['FY2026-Q4']

    def record(self, company_id=1, cap=100, **changes):
        return dict(dict(company_id=company_id, symbol=f'SYNTH{company_id}',
                         basic_industry='Private Sector Bank',
                         available_at='2026-03-06T17:15:00+00:00',
                         observation_date='2026-02-27',
                         historical_index_mcap_crore=cap,
                         historical_impact_cost_percent=0.02,
                         quote_evidence={'status': 'synthetic_quote'}), **changes)

    def test_invalid_capitalisation_never_qualifies(self):
        for cap in (None, 0, -1, float('nan'), float('inf'), float('-inf'), 'bad'):
            with self.subTest(cap=cap):
                self.assertIn('missing_historical_capitalisation',
                              eligibility(self.record(cap=cap), 'PRIVATE_BANKS', self.cutoff))

    def test_liquidity_must_be_finite_nonnegative_and_within_limit(self):
        for impact in (None, -0.01, 0.50001, float('nan'), float('inf'), 'bad'):
            with self.subTest(impact=impact):
                self.assertIn('missing_or_insufficient_historical_liquidity',
                              eligibility(self.record(historical_impact_cost_percent=impact),
                                          'PRIVATE_BANKS', self.cutoff))
        for impact in (0, policy.MAX_IMPACT_COST_PERCENT):
            self.assertEqual(eligibility(self.record(historical_impact_cost_percent=impact),
                                         'PRIVATE_BANKS', self.cutoff), [])

    def test_unknown_naive_or_malformed_publication_time_is_not_historical_proof(self):
        for available in (None, '', 'bad', '2026-03-01', '2026-03-01T10:00:00'):
            with self.subTest(available=available):
                self.assertIn('missing_or_invalid_publisher_availability',
                              eligibility(self.record(available_at=available), 'PRIVATE_BANKS', self.cutoff))
        for cutoff in (None, 'bad', '2026-03-31'):
            with self.assertRaisesRegex(ValueError, 'timezone-aware'):
                eligibility(self.record(), 'PRIVATE_BANKS', cutoff)

    def test_unknown_observation_date_is_not_historical_proof(self):
        for observed in (None, '', '2026-02-30'):
            self.assertIn('missing_or_invalid_observation_date',
                          eligibility(self.record(observation_date=observed), 'PRIVATE_BANKS', self.cutoff))

    def test_membership_uses_each_cutoff_and_contemporaneous_business(self):
        # A report published in April cannot qualify the March reconstruction.
        later = self.record(available_at='2026-04-01T00:00:00+05:30')
        self.assertIn('publisher_version_after_cutoff', eligibility(later, 'PRIVATE_BANKS', self.cutoff))
        self.assertEqual(eligibility(later, 'PRIVATE_BANKS', policy.CUTOFFS['FY2027-Q1']), [])
        # Later classification changes do not rewrite the earlier record.
        earlier = self.record()
        changed = self.record(basic_industry='Public Sector Bank')
        self.assertEqual(eligibility(earlier, 'PRIVATE_BANKS', self.cutoff), [])
        self.assertIn('no_dated_granular_business_proof_for_this_subsector',
                      eligibility(changed, 'PRIVATE_BANKS', policy.CUTOFFS['FY2027-Q1']))

    def test_cutoff_specific_member_sets_do_not_backfill_later_entrants(self):
        records = [self.record(1, 100),
                   self.record(2, 300, available_at='2026-04-01T00:00:00+05:30'),
                   self.record(3, 500, available_at=None)]
        snapshots = {}
        for quarter, cutoff in policy.CUTOFFS.items():
            qualified = [r for r in records if not eligibility(r, 'PRIVATE_BANKS', cutoff)]
            snapshots[quarter] = initial_members(qualified)
        self.assertEqual([m['company_id'] for m in snapshots['FY2026-Q4']], [1])
        self.assertEqual([m['company_id'] for m in snapshots['FY2027-Q1']], [2, 1])
        self.assertEqual(snapshots['FY2026-Q4'][0]['historical_cap_weight'], 1)
        self.assertEqual([m['historical_cap_weight'] for m in snapshots['FY2027-Q1']], [0.75, 0.25])

    def test_publication_boundary_compares_instants_with_offsets(self):
        # 18:29:59 UTC is exactly the March policy cutoff in IST.
        self.assertEqual(eligibility(self.record(available_at='2026-03-31T18:29:59+00:00'),
                                     'PRIVATE_BANKS', self.cutoff), [])
        self.assertIn('publisher_version_after_cutoff',
                      eligibility(self.record(available_at='2026-03-31T18:30:00+00:00'),
                                  'PRIVATE_BANKS', self.cutoff))

    def test_top_three_and_ties_do_not_use_performance(self):
        records = [self.record(4, 10, monthly_return=999), self.record(3, 100),
                   self.record(2, 200), self.record(1, 100, monthly_return=-999)]
        self.assertEqual([r['company_id'] for r in select(reversed(records))], [2, 1, 3])
        self.assertEqual([r['company_id'] for r in select(records)], [2, 1, 3])

    def test_hand_calculated_initial_weights_and_provenance(self):
        # 300 + 200 + 100 = 600 crore; the fourth company is outside TOP_N.
        records = [self.record(1, 300), self.record(2, 200), self.record(3, 100), self.record(4, 50)]
        members = initial_members(records)
        for member, cap in zip(members, (300, 200, 100)):
            self.assertAlmostEqual(member['equal_weight'], float(Decimal(1)/3))
            self.assertAlmostEqual(member['historical_cap_weight'], float(Decimal(cap)/600))
            self.assertIs(member['selection_evidence'], records[member['rank']-1])
        for field in ('equal_weight', 'historical_cap_weight'):
            self.assertAlmostEqual(sum(m[field] for m in members), 1)

    def test_sparse_baskets_and_large_caps(self):
        self.assertEqual(initial_members([]), [])
        member = initial_members([self.record()])[0]
        self.assertEqual((member['equal_weight'], member['historical_cap_weight']), (1, 1))
        # Binary-float summation overflows for these individually finite inputs.
        members = initial_members([self.record(1, 1e308), self.record(2, 1e308)])
        self.assertEqual([m['historical_cap_weight'] for m in members], [0.5, 0.5])
        members = initial_members([self.record(1, '0.1'), self.record(2, '0.3')])
        self.assertEqual([m['historical_cap_weight'] for m in members], [0.75, 0.25])

    def test_selection_refuses_duplicate_companies_and_invalid_caps(self):
        with self.assertRaisesRegex(ValueError, 'duplicate canonical'):
            initial_members([self.record(), self.record()])
        for cap in (None, 0, -1, float('nan'), float('inf')):
            with self.assertRaisesRegex(ValueError, 'finite positive'):
                initial_members([self.record(cap=cap)])
