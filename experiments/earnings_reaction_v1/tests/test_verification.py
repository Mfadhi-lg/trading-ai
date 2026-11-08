"""Offline TA-02A guards; synthetic promotion fixtures are not source evidence."""
import csv
import json
import unittest
from dataclasses import replace
from datetime import date, datetime

from experiments.earnings_reaction_v1.events import resolve_timing
from experiments.earnings_reaction_v1.investigate_sample import OUT, build_rows
from experiments.earnings_reaction_v1.verification import (
    Check, Outcome, REQUIRED, candle_join, completed_candle, evidenced_calendar,
    filing_relationship, historical_instrument, publication_time,
)
from test_events import fixture


def dt(value):
    return datetime.fromisoformat(value + "+05:30")


VERIFIED = Check(Outcome.VERIFIED, "synthetic_review", "synthetic:source")
UNKNOWN = Check(Outcome.UNRESOLVED, "source_missing", "missing:source")


class VerificationTests(unittest.TestCase):
    def test_dissemination_precedes_neither_revision_nor_receipt_by_assumption(self):
        raw = {"EXCHANGE RECEIVED TIME": "15-May-2026 17:25:29",
               "EXCHANGE DISSEMINATION TIME": "15-May-2026 17:25:32",
               "REVISED DATE/TIME": "15-MAY-2026 17:25:29",
               "BROADCAST DATE/TIME": "15-MAY-2026 17:25:00"}
        stamp, check = publication_time(raw, field_evidence=VERIFIED)
        self.assertEqual(stamp, dt("2026-05-15T17:25:32"))
        self.assertEqual(check.outcome, Outcome.VERIFIED)
        raw["EXCHANGE RECEIVED TIME"] = "15-May-2026 17:25:40"
        self.assertIsNone(publication_time(raw, field_evidence=VERIFIED)[0])

    def test_no_fallback_to_discovery_board_meeting_or_filename(self):
        for raw in ({}, {"REVISED DATE/TIME": "15-May-2026 17:25:29"},
                    {"BROADCAST DATE/TIME": "15-May-2026 17:25:00"},
                    {"EXCHANGE DISSEMINATION TIME": "not-a-time"}):
            self.assertIsNone(publication_time(raw, field_evidence=VERIFIED)[0])
        raw = {"EXCHANGE DISSEMINATION TIME": "15-May-2026 17:25:32"}
        self.assertIsNone(publication_time(raw, field_evidence=UNKNOWN)[0])

    def test_conflicting_independent_public_times_need_review(self):
        stamp, check = publication_time({"EXCHANGE DISSEMINATION TIME": "15-May-2026 17:25:32",
            "INDEPENDENT PUBLIC DISSEMINATION": "15-May-2026 17:25:00"}, field_evidence=VERIFIED)
        self.assertIsNone(stamp)
        self.assertEqual(check.reason, "conflicting_public_timestamps")

    def test_relationship_requires_review_and_explicit_parent(self):
        self.assertEqual(filing_relationship("Original", evidence=UNKNOWN)[0], "unresolved")
        self.assertEqual(filing_relationship("Original", evidence=VERIFIED)[1].outcome, Outcome.VERIFIED)
        self.assertEqual(filing_relationship("Revision", evidence=VERIFIED)[1].outcome, Outcome.UNRESOLVED)
        for declaration, kind in (("Revision", "amendment"), ("Duplicate", "duplicate")):
            resolved, check = filing_relationship(declaration, evidence=VERIFIED, parent_id="15192")
            self.assertEqual((resolved, check.outcome), (kind, Outcome.VERIFIED))

    def instrument(self, **overrides):
        args = dict(event_day=date(2026, 5, 15), symbol="AETHER", isin="INE0BWX01014", exchange="NSE", series="EQ",
                    version=dict(isin="INE0BWX01014", exchange="NSE", symbol="AETHER", series_or_group="EQ",
                                 status="active", valid_from_date="2022-06-03", valid_to_date=None),
                    provider=dict(isin="INE0BWX01014", exchange="NSE", segment="NSE_EQ", instrument_type="EQ",
                                  instrument_key="NSE_EQ|INE0BWX01014"), evidence=VERIFIED)
        args.update(overrides)
        return historical_instrument(**args)

    def test_historical_isin_venue_series_match(self):
        self.assertEqual(self.instrument().outcome, Outcome.VERIFIED)
        for field, value in (("isin", "INE947Q01028"), ("exchange", "BSE"), ("series", "BE"), ("symbol", "OTHER")):
            self.assertEqual(self.instrument(**{field: value}).outcome, Outcome.BLOCKED)

    def test_missing_or_incorrect_identity_never_joins(self):
        for override in (dict(isin=None), dict(provider=None), dict(version=None), dict(evidence=UNKNOWN)):
            self.assertEqual(self.instrument(**override).outcome, Outcome.UNRESOLVED)
        self.assertEqual(self.instrument(isin="bad").outcome, Outcome.BLOCKED)
        self.assertEqual(self.instrument(provider={}).outcome, Outcome.BLOCKED)

    def test_current_identity_and_validity_end_cannot_be_backdated(self):
        base = dict(isin="INE0BWX01014", exchange="NSE", symbol="AETHER", series_or_group="EQ", status="active")
        for validity in (dict(valid_from_date="2026-08-06"),
                         dict(valid_from_date="2022-06-03", valid_to_date="2026-05-15")):
            self.assertEqual(self.instrument(version={**base, **validity}).outcome, Outcome.UNRESOLVED)

    def test_actual_calendar_evidence_holiday_and_mock_not_live(self):
        calendars = json.loads((OUT / "calendar-evidence.json").read_text())
        holiday = calendars["apr30"]["days"][1]
        self.assertEqual((holiday["day"], holiday["kind"], holiday["status"]),
                         ("2026-05-01", "closed", "VERIFIED"))
        mock = calendars["may15"]["days"][1]
        self.assertEqual((mock["day"], mock["kind"]), ("2026-05-16", "mock_only"))
        self.assertTrue(any("CMTR74259" in ref for ref in mock["source_refs"]))
        for payload in calendars.values():
            calendar, check = evidenced_calendar(payload)
            self.assertIsNone(calendar)
            self.assertEqual(check.outcome, Outcome.BLOCKED)

    def promoted_synthetic_calendar(self):
        payload = json.loads((OUT / "calendar-evidence.json").read_text())["apr30"]
        payload.update(historical_hours_verified=True, special_session_review_complete=True,
                       evidence_ref="synthetic:promoted-calendar")
        for row in payload["days"]:
            row.update(status="VERIFIED", source_refs=["synthetic:review"])
        return payload

    def test_calendar_contract_boundaries_on_evidenced_holiday_dates(self):
        # Hours and exception completeness here are synthetic; May 1 holiday is real.
        cal, check = evidenced_calendar(self.promoted_synthetic_calendar())
        event, _ = fixture()
        event = replace(event, publication_at=dt("2026-04-30T23:06:18"))
        self.assertEqual(resolve_timing(event, cal).first_eligible_session, date(2026, 5, 4))
        for stamp, phase in (("2026-04-30T09:14:59", "before_open"),
                             ("2026-04-30T09:15:00", "intraday"),
                             ("2026-04-30T15:30:00", "after_close")):
            self.assertEqual(resolve_timing(replace(event, publication_at=dt(stamp)), cal).phase, phase)

    def test_calendar_gaps_and_unreviewed_days_fail_closed(self):
        payload = self.promoted_synthetic_calendar()
        payload["days"].pop(1)
        self.assertEqual(evidenced_calendar(payload)[1].reason, "calendar_daily_coverage_gap")
        payload = self.promoted_synthetic_calendar()
        payload["days"][0]["status"] = "UNRESOLVED"
        self.assertIsNone(evidenced_calendar(payload)[0])
        payload = self.promoted_synthetic_calendar()
        payload["days"][0]["mock"] = True
        self.assertEqual(evidenced_calendar(payload)[1].reason, "mock_is_not_live_trading")

    def test_all_independent_gates_required_including_relationship(self):
        checks = dict.fromkeys(REQUIRED, VERIFIED)
        self.assertEqual(candle_join(checks).outcome, Outcome.VERIFIED)
        for name in REQUIRED:
            self.assertEqual(candle_join({**checks, name: UNKNOWN}).outcome, Outcome.BLOCKED)
            self.assertEqual(candle_join({k:v for k,v in checks.items() if k != name}).outcome, Outcome.BLOCKED)
        self.assertEqual(candle_join({}).outcome, Outcome.BLOCKED)

    def test_intraday_cutoff_no_straddling_or_unfinished_totals(self):
        _, cal = fixture()
        session = cal.sessions[0]
        args = dict(start=dt("2026-07-03T14:15:00"), end=dt("2026-07-03T14:20:00"),
                    available_at=dt("2026-07-03T14:20:01"), publication_at=dt("2026-07-03T14:11:08"),
                    signal_at=dt("2026-07-03T14:20:01"), mode="reaction", session=session)
        self.assertTrue(completed_candle(**args))
        for override in (dict(start=dt("2026-07-03T14:10:00")),
                         dict(signal_at=dt("2026-07-03T14:19:59")),
                         dict(available_at=dt("2026-07-03T14:21:00")),
                         dict(end=dt("2026-07-03T16:00:00"))):
            self.assertFalse(completed_candle(**{**args, **override}))
        pre = {**args, "mode":"pre_announcement", "start":dt("2026-07-03T14:00:00"),
               "end":dt("2026-07-03T14:05:00"), "available_at":dt("2026-07-03T14:05:01")}
        self.assertTrue(completed_candle(**pre))
        self.assertFalse(completed_candle(**{**pre, "available_at":dt("2026-07-03T14:12:00")}))
        self.assertFalse(completed_candle(**{**args, "mode":"pre_announcement"}))

    def test_manifest_retains_all_failures_and_reproduces(self):
        rows = build_rows()
        self.assertEqual([r["filing_id"] for r in rows], ["15191", "15192", "15833", "16266", "748"])
        self.assertEqual(sum(r["source_status"] == "VERIFIED" for r in rows), 2)
        for row in rows:
            self.assertEqual(row["candle_join_status"], "BLOCKED")
            self.assertEqual(row["earliest_verified_publication_at"], "")
            self.assertEqual(row["first_eligible_session"], "")
        with (OUT / "event-verification-manifest.csv").open() as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(saved, [{k:str(v) for k,v in row.items()} for row in rows])


if __name__ == "__main__":
    unittest.main()
