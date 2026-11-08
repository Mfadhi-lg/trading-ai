"""TA-01 immutable research projection; no acquisition, SQL or provider calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Mapping
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from scrapper.sources.exchange.result_filings import ResultFiling

VERSION = "earnings-event-v1"
IST = ZoneInfo("Asia/Kolkata")


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value


class FilingKind(str, Enum):
    ORIGINAL = "original"
    DUPLICATE = "duplicate"
    AMENDMENT = "amendment"
    UNRESOLVED = "unresolved_candidate"


@dataclass(frozen=True)
class Evidence:
    source_ref: str
    snapshot_ref: str
    raw_row_json: str
    broadcast_raw: str
    captured_at: datetime | None = None
    content_sha256: str | None = None
    announcement_id: str | None = None  # Native ID only; never a generated filing key.
    limitations: tuple[str, ...] = ("historical_availability_not_established",)

    def __post_init__(self):
        if not self.source_ref or not self.snapshot_ref:
            raise ValueError("traceable source and snapshot required")
        if self.captured_at is not None:
            aware(self.captured_at)
        if not isinstance(self.limitations, tuple):
            raise ValueError("immutable limitations required")


@dataclass(frozen=True)
class Identity:
    company_id: int | None = None
    instrument_id: str | None = None
    instrument_exchange: str | None = None
    instrument_kind: str = "unresolved"
    company_evidence: str | None = None
    instrument_evidence: str | None = None
    # Identity may be current-only. A research projection does not certify PIT.
    historical_basis: str = "unresolved"

    @property
    def candle_join_allowed(self) -> bool:
        return bool(self.company_id and self.company_evidence and self.instrument_id
                    and self.instrument_exchange and self.instrument_evidence
                    and self.instrument_kind == "equity")


@dataclass(frozen=True)
class EarningsEvent:
    filing_id: str
    filing_key: str
    exchange: str
    exchange_key: str
    isin: str | None
    company_name_raw: str
    result_period: str
    period_end: date
    reporting_scope: str
    publication_at: datetime | None
    evidence: Evidence
    identity: Identity = Identity()
    kind: FilingKind = FilingKind.UNRESOLVED
    related_filing_id: str | None = None
    quality: tuple[str, ...] = ()
    version: str = VERSION

    def __post_init__(self):
        if not self.filing_id or not self.filing_key:
            raise ValueError("historical filing identity required")
        if self.publication_at is not None:
            aware(self.publication_at)
        if self.result_period != self.period_end.strftime("%Y-%m"):
            raise ValueError("source period disagrees with exact period end")
        if not isinstance(self.kind, FilingKind) or not isinstance(self.quality, tuple):
            raise ValueError("typed classification and immutable quality required")

    @property
    def fiscal_label(self) -> str | None:
        # Indian April–March fiscal year; non-quarter ends remain unresolved.
        if (self.period_end.month, self.period_end.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)):
            return None
        month = self.period_end.month
        return f"Q{ {3: 4, 6: 1, 9: 2, 12: 3}[month]} FY{self.period_end.year + (month > 3)}"


def _timestamp(raw: object) -> datetime | None:
    try:
        return aware(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return None


def from_result_filing(filing: ResultFiling, *, filing_id: str, evidence: Evidence,
                       identity: Identity = Identity(), kind: FilingKind = FilingKind.UNRESOLVED,
                       related_filing_id: str | None = None) -> EarningsEvent:
    """Adapt canonical parser output without parsing or acquiring another feed.

    Caller supplies classification/identity evidence. Parser acceptance alone
    does not establish earnings semantics, original status or instrument scope.
    """
    stamp = _timestamp(filing.broadcast_at_utc) if evidence.broadcast_raw.strip() else None
    return EarningsEvent(
        filing_id, filing.filing_key, filing.exchange, filing.exchange_key, filing.isin,
        filing.company_name_raw, filing.result_period, date.fromisoformat(filing.period_end_date),
        filing.reporting_scope, stamp.astimezone(IST) if stamp else None, evidence, identity,
        kind, related_filing_id, () if stamp else ("missing_or_ambiguous_publication",))


def from_stored_filing(row: Mapping, *, evidence: Evidence,
                       identity: Identity = Identity(), kind: FilingKind = FilingKind.UNRESOLVED,
                       related_filing_id: str | None = None) -> EarningsEvent:
    """Read-only projection of an existing exchange_result_filings row.

    Connection ownership/SELECTs remain outside this adapter. Preserve the stored
    filing key, exact end and raw evidence; never call the operational DB opener.
    """
    stamp = _timestamp(row["broadcast_at_utc"]) if evidence.broadcast_raw.strip() else None
    return EarningsEvent(
        str(row["filing_id"]), row["filing_key"], row["exchange"], row["exchange_key"],
        row["isin"], row["company_name_raw"], row["result_period"],
        date.fromisoformat(row["period_end_date"]), row["reporting_scope"],
        stamp.astimezone(IST) if stamp else None, evidence, identity, kind,
        related_filing_id, () if stamp else ("missing_or_ambiguous_publication",))


@dataclass(frozen=True)
class Session:
    day: date
    opens_at: datetime
    closes_at: datetime

    def __post_init__(self):
        if aware(self.opens_at) >= aware(self.closes_at):
            raise ValueError("session must have positive duration")
        if self.opens_at.date() != self.day or self.closes_at.date() != self.day:
            raise ValueError("session dates must use venue timezone")


@dataclass(frozen=True)
class TradingCalendar:
    exchange: str
    version: str
    evidence_ref: str
    timezone: str
    covered_from: date
    covered_through: date
    sessions: tuple[Session, ...]

    def __post_init__(self):
        if not self.version or not self.evidence_ref or not isinstance(self.sessions, tuple):
            raise ValueError("versioned explicit calendar required")
        zone = ZoneInfo(self.timezone)
        if self.covered_from > self.covered_through:
            raise ValueError("invalid coverage")
        previous = None
        for s in self.sessions:
            if not self.covered_from <= s.day <= self.covered_through:
                raise ValueError("session outside coverage")
            if s.opens_at.astimezone(zone).date() != s.day or s.closes_at.astimezone(zone).date() != s.day:
                raise ValueError("calendar timezone disagrees with session")
            if previous and (s.day <= previous.day or s.opens_at <= previous.closes_at):
                raise ValueError("sessions must be ordered, unique and nonoverlapping")
            previous = s


@dataclass(frozen=True)
class EventTiming:
    phase: str
    publication_date: date | None = None
    publication_session: date | None = None
    market_event_date: date | None = None
    first_eligible_session: date | None = None
    observable_from: datetime | None = None
    calendar_version: str | None = None
    reason: str | None = None


def resolve_timing(event: EarningsEvent, calendar: TradingCalendar | None) -> EventTiming:
    """First regular session with any time remaining after publication.

    Open is inclusive, close exclusive. Intraday availability starts at publication,
    not that day's open. This is source timing, not a trading-entry instruction.
    Calendar coverage asserts that every regular session in its range is listed.
    """
    if event.publication_at is None:
        return EventTiming("unresolved", reason="missing_or_ambiguous_publication")
    if calendar is None:
        return EventTiming("unresolved", reason="missing_calendar")
    venue = event.identity.instrument_exchange or event.exchange
    if calendar.exchange != venue:
        return EventTiming("unresolved", reason="calendar_exchange_mismatch")
    stamp = event.publication_at.astimezone(ZoneInfo(calendar.timezone))
    day = stamp.date()
    if not calendar.covered_from <= day <= calendar.covered_through:
        return EventTiming("unresolved", reason="calendar_coverage_gap")
    same = next((s for s in calendar.sessions if s.day == day), None)
    phase = ("non_session" if same is None else "before_open" if stamp < same.opens_at
             else "intraday" if stamp < same.closes_at else "after_close")
    eligible = next((s for s in calendar.sessions if s.closes_at > stamp), None)
    if eligible is None:
        return EventTiming("unresolved", day, same.day if same else None,
                           calendar_version=calendar.version, reason="next_session_not_covered")
    return EventTiming(phase, day, same.day if same else None, eligible.day, eligible.day,
                       max(stamp, eligible.opens_at), calendar.version)


@dataclass(frozen=True)
class EventGroup:
    key: tuple[str, date]
    filings: tuple[EarningsEvent, ...]
    original_filing_id: str | None
    reason: str | None


def group_events(events: tuple[EarningsEvent, ...]) -> tuple[EventGroup, ...]:
    """Company-period cluster across scope/exchange; never count revisions twice.

    Exactly one evidenced original and explicit links from all other filings are
    needed to resolve a cluster. Unknown company identity cannot be merged by name.
    """
    groups: dict[tuple[str, date], list[EarningsEvent]] = {}
    for event in events:
        identity = event.identity
        company = (f"company:{identity.company_id}" if identity.company_id and identity.company_evidence
                   else f"unresolved:{event.exchange}:{event.filing_id}")
        groups.setdefault((company, event.period_end), []).append(event)
    result = []
    for key, rows in groups.items():
        originals = [e for e in rows if e.kind == FilingKind.ORIGINAL]
        original = originals[0].filing_id if len(originals) == 1 else None
        resolved = (not key[0].startswith("unresolved:") and original is not None
                    and len({(e.exchange, e.filing_id) for e in rows}) == len(rows)
                    and all(e.kind == FilingKind.ORIGINAL or (
                        e.kind in (FilingKind.AMENDMENT, FilingKind.DUPLICATE)
                        and e.related_filing_id == original) for e in rows))
        result.append(EventGroup(key, tuple(rows), original if resolved else None,
                                 None if resolved else "review_filing_relationships"))
    return tuple(result)


def reaction_candle_eligible(event: EarningsEvent, timing: EventTiming, *,
                             start: datetime, end: datetime, signal_at: datetime) -> bool:
    """Boundary guard only; TA-02 must also validate session/instrument/candle data.

    Reject earlier/straddling candles and candles completed after the signal.
    Whole daily candles on intraday announcement dates therefore cannot qualify.
    """
    aware(start), aware(end), aware(signal_at)
    return bool(event.identity.candle_join_allowed and timing.reason is None
                and timing.observable_from is not None
                and timing.observable_from <= start < end <= signal_at)
