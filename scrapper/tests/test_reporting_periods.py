"""Calendar priorities are query hints, never source-period assignments."""
from datetime import datetime, timezone
import unittest

from scrapper.sources.exchange.reporting_periods import priority_result_periods


class ReportingPeriodTests(unittest.TestCase):
    def test_quarter_boundaries_and_publication_months(self):
        for day, period in (
            ('2026-03-31', '2025-12'), ('2026-04-01', '2026-03'),
            ('2026-05-31', '2026-03'), ('2026-06-30', '2026-03'),
            ('2026-07-01', '2026-06'), ('2026-08-31', '2026-06'),
            ('2026-09-30', '2026-06'), ('2026-10-01', '2026-09'),
            ('2026-11-30', '2026-09'), ('2026-12-31', '2026-09'),
            ('2027-01-01', '2026-12'), ('2027-02-28', '2026-12'),
        ):
            with self.subTest(day=day):
                self.assertEqual(priority_result_periods(scheduled_for=day)[0], period)

    def test_ist_year_change_before_utc_midnight(self):
        before = datetime(2026, 12, 31, 18, 29, tzinfo=timezone.utc)
        after = datetime(2026, 12, 31, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(priority_result_periods(now=before), ('2026-09', '2026-06'))
        self.assertEqual(priority_result_periods(now=after), ('2026-12', '2026-09'))

    def test_override_is_a_single_valid_quarter(self):
        self.assertEqual(priority_result_periods(override='2024-03'), ('2024-03',))
        for invalid in ('2026-08', '2026-9', '', 'September'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                priority_result_periods(override=invalid)

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            priority_result_periods(now=datetime(2026, 10, 1))
