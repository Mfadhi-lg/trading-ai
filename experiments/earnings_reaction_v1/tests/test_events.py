"""Deterministic synthetic fixtures, never real market-calendar evidence."""
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime

from experiments.earnings_reaction_v1.events import (
    EarningsEvent, Evidence, FilingKind, Identity, Session, TradingCalendar,
    from_result_filing, from_stored_filing, group_events, reaction_candle_eligible,
    resolve_timing,
)


def dt(day, clock):
    return datetime.fromisoformat(f"2026-07-{day:02}T{clock}:00+05:30")


def fixture():
    evidence = Evidence("synthetic:feed", "synthetic:snapshot", '{}', "synthetic")
    identity = Identity(1, "synthetic-equity-1", "NSE", "equity", "synthetic:company", "synthetic:instrument")
    event = EarningsEvent("1", "original-source-key", "NSE", "SYNTH", None, "Synthetic Ltd",
                          "2026-06", date(2026, 6, 30), "standalone", dt(3, '08:00'), evidence,
                          identity, FilingKind.ORIGINAL)
    # Friday, weekend, Monday synthetic holiday, Tuesday: explicitly enumerated.
    sessions = tuple(Session(date(2026, 7, d), dt(d, '09:15'), dt(d, '15:30')) for d in (3, 7, 8))
    calendar = TradingCalendar("NSE", "synthetic-v1", "synthetic:calendar", "Asia/Kolkata",
                               date(2026, 7, 3), date(2026, 7, 8), sessions)
    return event, calendar


class EventTests(unittest.TestCase):
    def setUp(self):
        self.event, self.calendar = fixture()

    def timing(self, day, clock):
        return resolve_timing(replace(self.event, publication_at=dt(day, clock)), self.calendar)

    def test_period_is_not_announcement_quarter(self):
        self.assertEqual(self.event.fiscal_label, 'Q1 FY2027')
        self.assertEqual(self.event.period_end, date(2026, 6, 30))
        self.assertEqual(self.timing(3, '08:00').publication_date, date(2026, 7, 3))
        for end, label in [('2026-03-31', 'Q4 FY2026'), ('2026-09-30', 'Q2 FY2027')]:
            self.assertEqual(replace(self.event, period_end=date.fromisoformat(end), result_period=end[:7]).fiscal_label, label)

    def test_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.event.filing_key = 'changed'
        with self.assertRaises(FrozenInstanceError):
            self.event.evidence.source_ref = 'changed'

    def test_before_open(self):
        t = self.timing(3, '08:00')
        self.assertEqual(t.phase, 'before_open')
        self.assertEqual(t.observable_from, dt(3, '09:15'))

    def test_open_and_close_boundaries(self):
        self.assertEqual(self.timing(3, '09:15').phase, 'intraday')
        self.assertEqual(self.timing(3, '15:30').first_eligible_session, date(2026, 7, 7))

    def test_intraday(self):
        t = self.timing(3, '12:00')
        self.assertEqual(t.first_eligible_session, date(2026, 7, 3))
        self.assertEqual(t.observable_from, dt(3, '12:00'))

    def test_after_hours_skips_weekend_and_holiday(self):
        t = self.timing(3, '16:00')
        self.assertEqual(t.phase, 'after_close')
        self.assertEqual(t.publication_session, date(2026, 7, 3))
        self.assertEqual(t.market_event_date, date(2026, 7, 7))
        self.assertEqual(t.observable_from, dt(7, '09:15'))

    def test_weekend_and_holiday(self):
        for day in (4, 5, 6):
            t = self.timing(day, '12:00')
            self.assertEqual(t.phase, 'non_session')
            self.assertIsNone(t.publication_session)
            self.assertEqual(t.first_eligible_session, date(2026, 7, 7))

    def test_missing_timestamp_and_calendar_fail_closed(self):
        self.assertEqual(resolve_timing(replace(self.event, publication_at=None), self.calendar).reason,
                         'missing_or_ambiguous_publication')
        self.assertEqual(resolve_timing(self.event, None).reason, 'missing_calendar')
        with self.assertRaises(ValueError):
            replace(self.event, publication_at=datetime(2026, 7, 3))

    def test_coverage_does_not_guess_next_session(self):
        self.assertEqual(self.timing(8, '16:00').reason, 'next_session_not_covered')
        self.assertEqual(self.timing(2, '12:00').reason, 'calendar_coverage_gap')
        self.assertEqual(resolve_timing(self.event, replace(self.calendar, exchange='BSE')).reason,
                         'calendar_exchange_mismatch')

    def test_calendar_rejects_unsorted_or_missing_provenance(self):
        with self.assertRaises(ValueError):
            replace(self.calendar, sessions=tuple(reversed(self.calendar.sessions)))
        with self.assertRaises(ValueError):
            replace(self.calendar, evidence_ref='')

    def test_revision_and_duplicate_are_one_group(self):
        amended = replace(self.event, filing_id='2', kind=FilingKind.AMENDMENT,
                          related_filing_id='1', publication_at=dt(7, '16:00'))
        duplicate = replace(amended, filing_id='3', kind=FilingKind.DUPLICATE,
                            exchange='BSE', reporting_scope='consolidated')
        g, = group_events((self.event, amended, duplicate))
        self.assertEqual(len(g.filings), 3)
        self.assertEqual(g.original_filing_id, '1')
        self.assertEqual(g.filings[1].publication_at, dt(7, '16:00'))

    def test_multiple_originals_and_unlinked_amendments_need_review(self):
        for extra in (replace(self.event, filing_id='2'),
                      replace(self.event, filing_id='2', kind=FilingKind.AMENDMENT),
                      replace(self.event, filing_id='2', kind=FilingKind.UNRESOLVED)):
            g, = group_events((self.event, extra))
            self.assertIsNone(g.original_filing_id)
        self.assertIsNone(group_events((self.event, self.event))[0].original_filing_id)

    def test_unknown_company_never_merged_by_name(self):
        unknown = replace(self.event, identity=Identity())
        self.assertEqual(len(group_events((unknown, replace(unknown, filing_id='2')))), 2)
        self.assertIsNone(group_events((unknown,))[0].original_filing_id)

    def test_intraday_candle_cannot_precede_or_straddle_publication(self):
        e = replace(self.event, publication_at=dt(3, '12:00'))
        t = resolve_timing(e, self.calendar)
        for start, end in [('10:00', '11:00'), ('11:30', '12:30'), ('09:15', '15:30')]:
            self.assertFalse(reaction_candle_eligible(e, t, start=dt(3, start), end=dt(3, end), signal_at=dt(3, '15:30')))
        self.assertTrue(reaction_candle_eligible(e, t, start=dt(3, '12:00'), end=dt(3, '12:30'), signal_at=dt(3, '12:30')))
        self.assertFalse(reaction_candle_eligible(e, t, start=dt(3, '12:00'), end=dt(3, '12:30'), signal_at=dt(3, '12:15')))

    def test_after_hours_blocks_previous_session(self):
        e = replace(self.event, publication_at=dt(3, '16:00'))
        self.assertFalse(reaction_candle_eligible(e, resolve_timing(e, self.calendar),
                         start=dt(3, '14:00'), end=dt(3, '15:00'), signal_at=dt(7, '12:00')))

    def test_unresolved_or_non_equity_instrument_blocks_join(self):
        for identity in (Identity(), replace(self.event.identity, instrument_id=None),
                         replace(self.event.identity, instrument_kind='bond'),
                         replace(self.event.identity, instrument_evidence=None)):
            e = replace(self.event, identity=identity)
            self.assertFalse(reaction_candle_eligible(e, resolve_timing(e, self.calendar),
                             start=dt(3, '10:00'), end=dt(3, '11:00'), signal_at=dt(3, '12:00')))

    def test_stored_adapter_preserves_identity_and_missing_timestamp(self):
        row = dict(filing_id=42, filing_key='retained-key', exchange='NSE', exchange_key='SYNTH', isin=None,
                   company_name_raw='Synthetic', result_period='2026-06', period_end_date='2026-06-30',
                   reporting_scope='consolidated', broadcast_at_utc='2026-07-03T06:30:00+00:00')
        e = from_stored_filing(row, evidence=self.event.evidence)
        self.assertEqual((e.filing_id, e.filing_key, e.reporting_scope), ('42', 'retained-key', 'consolidated'))
        self.assertEqual(getattr(e.publication_at.tzinfo, 'key'), 'Asia/Kolkata')
        self.assertIs(e.evidence, self.event.evidence)
        self.assertEqual(e.kind, FilingKind.UNRESOLVED)
        for raw in (None, '', '2026-07-03', 'ambiguous'):
            self.assertIsNone(from_stored_filing(dict(row, broadcast_at_utc=raw), evidence=e.evidence).publication_at)

    def test_normalized_timestamp_without_raw_source_time_fails_closed(self):
        row = dict(filing_id=42, filing_key='retained-key', exchange='NSE', exchange_key='SYNTH', isin=None,
                   company_name_raw='Synthetic', result_period='2026-06', period_end_date='2026-06-30',
                   reporting_scope='standalone', broadcast_at_utc='2026-07-03T06:30:00+00:00')
        evidence = replace(self.event.evidence, broadcast_raw='')
        event = from_stored_filing(row, evidence=evidence)
        self.assertEqual(resolve_timing(event, self.calendar).reason, 'missing_or_ambiguous_publication')

    def test_canonical_result_filing_adapter(self):
        from scrapper.sources.exchange.result_filings import ResultFiling
        f = ResultFiling(1, 'NSE', 'SYNTH', None, 'Synthetic', '2026-06', '2026-06-30',
                         'standalone', '03-Jul-2026 12:00:00', '2026-07-03T06:30:00+00:00',
                         None, None, None, {}, ())
        e = from_result_filing(f, filing_id='snapshot:1:row:1', evidence=self.event.evidence)
        self.assertEqual(e.filing_key, f.filing_key)
        self.assertEqual(e.publication_at, dt(3, '12:00'))
        self.assertEqual(e.kind, FilingKind.UNRESOLVED)


if __name__ == '__main__':
    unittest.main()
