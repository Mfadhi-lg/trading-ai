"""TA-02A research evidence gates. No acquisition, database or provider clients.

Checks represent reviewed evidence, not parser confidence. TA-01 projections alone
must never supply a VERIFIED check. Missing evidence cannot enable a join.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Mapping

from .events import IST, Session, TradingCalendar, aware
from scrapper.sources.exchange.security_master import classify_instrument
from scrapper.sources.exchange.tabular import normalize_isin, parse_ist_timestamp


class Outcome(str, Enum):
    VERIFIED = "VERIFIED"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Check:
    outcome: Outcome
    reason: str
    source_ref: str

    def __post_init__(self):
        if not isinstance(self.outcome, Outcome) or not self.reason or not self.source_ref:
            raise ValueError("typed outcome, reason and evidence reference required")


def publication_time(raw: Mapping[str, str], *, field_evidence: Check
                     ) -> tuple[datetime | None, Check]:
    """Only an authenticated public dissemination field establishes publication.

    Receipt, revised, board-meeting, filename and scraper times are not public
    release evidence. This resolves this filing's release, not an issuer's first
    earnings disclosure (which additionally requires relationship review).
    """
    ref = field_evidence.source_ref
    if field_evidence.outcome != Outcome.VERIFIED:
        return None, Check(Outcome.UNRESOLVED, "dissemination_field_not_independently_verified", ref)
    try:
        stamp = datetime.fromisoformat(parse_ist_timestamp(
            raw.get("EXCHANGE DISSEMINATION TIME", ""), field="dissemination"))
        received = raw.get("EXCHANGE RECEIVED TIME", "")
        if received and datetime.fromisoformat(parse_ist_timestamp(received, field="received")) > stamp:
            return None, Check(Outcome.UNRESOLVED, "received_after_dissemination_conflict", ref)
        # When another independently evidenced field claims public release,
        # disagreement needs adjudication; do not silently choose min/max.
        other = raw.get("INDEPENDENT PUBLIC DISSEMINATION", "")
        if other and datetime.fromisoformat(parse_ist_timestamp(other, field="public")) != stamp:
            return None, Check(Outcome.UNRESOLVED, "conflicting_public_timestamps", ref)
    except (ValueError, TypeError):
        return None, Check(Outcome.UNRESOLVED, "missing_or_invalid_dissemination", ref)
    return stamp.astimezone(IST), Check(Outcome.VERIFIED, "explicit_public_dissemination", ref)


def filing_relationship(declaration: str, *, evidence: Check,
                        parent_id: str | None = None) -> tuple[str, Check]:
    """Caller must evidence the declaration and, for later filings, the parent.

    Close timestamps, same scope or same issuer-period alone cannot prove a link.
    An original check must include review of earlier earnings disclosures.
    """
    kind = {"Original": "original", "Revision": "amendment", "Duplicate": "duplicate"}.get(declaration)
    if evidence.outcome != Outcome.VERIFIED or kind is None:
        return "unresolved", Check(Outcome.UNRESOLVED, "relationship_not_evidenced", evidence.source_ref)
    if kind != "original" and not parent_id:
        return kind, Check(Outcome.UNRESOLVED, "explicit_parent_link_missing", evidence.source_ref)
    return kind, Check(Outcome.VERIFIED, "reviewed_filing_relationship", evidence.source_ref)


def historical_instrument(*, event_day: date, symbol: str, isin: str | None,
                          exchange: str, series: str, version: Mapping | None,
                          provider: Mapping | None, evidence: Check) -> Check:
    """Match effective-dated security evidence and an actual provider record.

    A syntactically constructed NSE_EQ|ISIN is only a candidate key. Even a
    provider BOD record cannot backdate the exchange-security version.
    """
    ref = evidence.source_ref
    if not isin or version is None or provider is None:
        return Check(Outcome.UNRESOLVED, "missing_historical_identity_or_provider_record", ref)
    try:
        normalize_isin(isin)
        start = date.fromisoformat(version["valid_from_date"])
        end = date.fromisoformat(version["valid_to_date"]) if version.get("valid_to_date") else None
    except (ValueError, TypeError, KeyError):
        return Check(Outcome.BLOCKED, "invalid_isin_or_validity", ref)
    expected = f"{exchange}_EQ|{isin}"
    if (version.get("isin") != isin or version.get("exchange") != exchange
            or version.get("symbol") != symbol or version.get("series_or_group") != series
            or classify_instrument(series, version.get("instrument_raw") or "") != "equity"
            or version.get("status") != "active"
            or provider.get("isin") != isin or provider.get("exchange") != exchange
            or provider.get("segment") != f"{exchange}_EQ"
            or provider.get("instrument_type") != "EQ"
            or provider.get("instrument_key") != expected):
        return Check(Outcome.BLOCKED, "isin_venue_series_or_category_mismatch", ref)
    if not start <= event_day or (end is not None and event_day >= end):
        return Check(Outcome.UNRESOLVED, "security_version_does_not_cover_event", ref)
    if evidence.outcome != Outcome.VERIFIED:
        return Check(Outcome.UNRESOLVED, "historical_tradability_not_verified", ref)
    return Check(Outcome.VERIFIED, "historical_isin_venue_series_match", ref)


def evidenced_calendar(payload: Mapping) -> tuple[TradingCalendar | None, Check]:
    """Adapt a reviewed, complete daily ledger to the unchanged TA-01 contract.

    Every date, including closures, must have an evidenced decision. Proposed
    hours and an annual holiday list alone cannot certify historical sessions.
    Live special sessions are explicit; mock sessions cannot become live bars.
    """
    ref = payload["evidence_ref"]
    first, last = (date.fromisoformat(payload[k]) for k in ("covered_from", "covered_through"))
    days = payload["days"]
    expected = [first + timedelta(days=i) for i in range((last-first).days+1)]
    if [r["day"] for r in days] != [d.isoformat() for d in expected]:
        return None, Check(Outcome.BLOCKED, "calendar_daily_coverage_gap", ref)
    if not payload.get("historical_hours_verified") or not payload.get("special_session_review_complete"):
        return None, Check(Outcome.BLOCKED, "historical_hours_or_special_sessions_unresolved", ref)
    sessions = []
    for row in days:
        if row.get("status") != Outcome.VERIFIED.value or not row.get("source_refs"):
            return None, Check(Outcome.BLOCKED, "unverified_calendar_day", ref)
        if row["kind"] in ("regular", "special_live"):
            if row.get("mock"):
                return None, Check(Outcome.BLOCKED, "mock_is_not_live_trading", ref)
            sessions.append(Session(date.fromisoformat(row["day"]),
                                    datetime.fromisoformat(row["opens_at"]),
                                    datetime.fromisoformat(row["closes_at"])))
        elif row["kind"] not in ("closed", "mock_only"):
            return None, Check(Outcome.BLOCKED, "unknown_session_kind", ref)
    return TradingCalendar(payload["exchange"], payload["version"], ref, "Asia/Kolkata",
                           first, last, tuple(sessions)), Check(Outcome.VERIFIED, "reviewed_explicit_calendar", ref)


REQUIRED = ("source", "publication", "company", "instrument", "session", "relationship", "period_scope")


def candle_join(checks: Mapping[str, Check]) -> Check:
    failures = [key for key in REQUIRED if key not in checks or checks[key].outcome != Outcome.VERIFIED]
    return Check(Outcome.BLOCKED if failures else Outcome.VERIFIED,
                 "required_evidence:" + ",".join(failures) if failures else "all_required_evidence_verified",
                 ";".join(checks[k].source_ref for k in REQUIRED if k in checks) or "missing:evidence")


def completed_candle(*, start: datetime, end: datetime, available_at: datetime,
                     publication_at: datetime, signal_at: datetime, mode: str,
                     session: Session) -> bool:
    """One signal cutoff for all inputs; no partial/straddling candle totals."""
    for stamp in (start, end, available_at, publication_at, signal_at):
        aware(stamp)
    if mode not in ("pre_announcement", "reaction"):
        raise ValueError("explicit feature timing mode required")
    if not (session.opens_at <= start < end <= session.closes_at
            and end <= available_at <= signal_at):
        return False
    return (end < publication_at and available_at < publication_at
            if mode == "pre_announcement" else publication_at <= start)


# ER-02: source/receipt-bound review records; no market-data dependency.
def _json_pointer(value, pointer):
    if pointer == '':
        return value
    if not pointer.startswith('/'):
        raise ValueError('JSON pointer required')
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _official_bytes(ref, root, exchange):
    """Require original body AND a separately retained acquisition receipt.

    A hash by itself never certifies publication. This validates the retained
    receipt/body binding; the receipt's provenance remains an auditable claim.
    No download, credentials, clock inference or source-path guessing occurs.
    """
    import hashlib
    import json
    from pathlib import Path
    from urllib.parse import urlparse
    domains = {'NSE': {'www.nseindia.com', 'nsearchives.nseindia.com', 'archives.nseindia.com'},
               'BSE': {'www.bseindia.com', 'api.bseindia.com'}}
    if exchange not in domains:
        raise ValueError('unknown exchange')
    body = (Path(root) / ref['body_path']).read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    receipt = json.loads((Path(root) / ref['receipt_path']).read_text())
    if digest != ref['sha256'] or receipt['response_sha256'] != digest:
        raise ValueError('receipt/body hash mismatch')
    if receipt['http_status'] != 200 or receipt.get('curl_exit_code',0) != 0 or receipt.get('error_kind') or not receipt.get('provenance_locator'):
        raise ValueError('successful attributable acquisition receipt required')
    aware(datetime.fromisoformat(receipt['captured_at']))
    for key in ('requested_url', 'final_url'):
        url = urlparse(receipt[key])
        if url.scheme != 'https' or url.hostname not in domains[exchange] or url.username or url.password:
            raise ValueError('official exchange HTTPS origin required')
    return body, receipt


def verify_disclosure(disclosure, *, company_id, period_end, evidence_root, _content_only=False):
    """Independent filing/time/actual-results/earliest gates for one quarter.

    Adjudications must cite retained bytes, quotes and complete announcement
    searches. Source declaration 'Original' does not satisfy earliest review.
    Search dispositions describe ALL rows, including notices and other periods.
    """
    import json
    result = dict(candidate=False, timestamp_verified=False, publication_at_ist=None,
                  actual_results_verified=False, earliest_verified=False,
                  original_release_at_ist=None, reasons=[], evidence_refs=[])
    if not disclosure:
        result['reasons'].append('NO_DISCLOSURE_REVIEW')
        return result
    try:
        exchange = disclosure['exchange']
        body, receipt = _official_bytes(disclosure['announcement'], evidence_root, exchange)
        row = _json_pointer(json.loads(body), disclosure['row_pointer'])
        if str(row[disclosure['identity_field']]) != str(disclosure['exchange_key']):
            raise ValueError('exchange identity mismatch')
        native = str(row[disclosure['native_id_field']])
        if not native or native != str(disclosure['native_announcement_id']):
            raise ValueError('native announcement ID mismatch')
        result['candidate'] = True
        result['evidence_refs'].append(disclosure['announcement'])
        # Explicit public dissemination field. Other fields need a reviewed,
        # retained official semantics citation; creation/receipt are forbidden.
        field = disclosure['public_timestamp_field']
        forbidden = {'Fld_CreateDate', 'EXCHANGE RECEIVED TIME', 'received_at', 'board_meeting_end', 'DT_TM'}
        if field in forbidden:
            raise ValueError('creation/receipt/board time is not dissemination')
        if field not in {'EXCHANGE DISSEMINATION TIME', 'exchdisstime', 'DissemDT', 'dissemDT'}:
            semantics = disclosure['timestamp_semantics']
            semantics_body, _ = _official_bytes(semantics['source'], evidence_root, exchange)
            if (not semantics.get('reviewed_by') or not semantics.get('quote') or semantics['field'] != field
                    or semantics.get('meaning') != 'public_dissemination'
                    or semantics['quote'].encode() not in semantics_body):
                raise ValueError('public timestamp semantics unsupported')
        public_fields={'EXCHANGE DISSEMINATION TIME':row[field]}
        for conflict_field in ('EXCHANGE RECEIVED TIME','INDEPENDENT PUBLIC DISSEMINATION'):
            if conflict_field in row:public_fields[conflict_field]=row[conflict_field]
        stamp, check = publication_time(public_fields,
            field_evidence=Check(Outcome.VERIFIED, 'receipt_bound_official_record', receipt['provenance_locator']))
        if check.outcome != Outcome.VERIFIED:
            raise ValueError(check.reason)
        if stamp > aware(datetime.fromisoformat(receipt['captured_at'])):
            raise ValueError('public timestamp is later than retained acquisition receipt')
        result.update(timestamp_verified=True, publication_at_ist=stamp.isoformat())
        attachment_body, attachment_receipt = _official_bytes(disclosure['attachment'], evidence_root, exchange)
        if row[disclosure['attachment_url_field']] != attachment_receipt['requested_url']:
            raise ValueError('attachment not linked by announcement')
        content = disclosure['content_review']
        # Text is a retained extraction with original attachment hash and locator.
        from pathlib import Path
        text = (Path(evidence_root) / content['text_path']).read_text()
        if (not content.get('reviewed_by') or not content.get('locator') or not content.get('quote')
                or content['quote'] not in text or content['company_id'] != company_id
                or content['period_end'] != period_end or content['kind'] != 'actual_financial_results'
                or content['attachment_sha256'] != disclosure['attachment']['sha256']):
            raise ValueError('actual financial results content not substantiated')
        result['actual_results_verified'] = True
        result['evidence_refs'].append(disclosure['attachment'])
        if _content_only:return result
        earliest = disclosure['earliest_review']
        if not earliest.get('reviewed_by') or earliest.get('conflicts') or earliest.get('unresolved_relationships'):
            raise ValueError('earliest/revision conflict unresolved')
        expected_venues = set(disclosure['issuer_exchanges'])
        if not expected_venues or not expected_venues.issubset({'NSE', 'BSE'}):
            raise ValueError('issuer exchange search scope missing')
        covered = set()
        chosen_present = False
        for search in earliest['searches']:
            search_body, search_receipt = _official_bytes(search['source'], evidence_root, search['exchange'])
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(search_receipt['requested_url']).query)
            if search['exchange'] == 'NSE':
                keys = ('symbol','from_date','to_date'); fmt='%d-%m-%Y'
            else:
                keys = ('strScrip','strPrevDate','strToDate'); fmt='%Y%m%d'
            if query.get(keys[0]) != [str(search['exchange_key'])]:
                raise ValueError('search receipt issuer scope mismatch')
            from_date=datetime.strptime(query[keys[1]][0],fmt).date()
            to_date=datetime.strptime(query[keys[2]][0],fmt).date()
            if from_date > date.fromisoformat(period_end) or to_date < stamp.date():
                raise ValueError('search receipt date scope incomplete')
            if disclosure['issuer_identifiers'].get(search['exchange']) != search['exchange_key']:
                raise ValueError('search exchange crosswalk mismatch')
            search_payload=json.loads(search_body)
            records = _json_pointer(search_payload, search['records_pointer'])
            if search.get('total_count_pointer') is not None:
                if int(_json_pointer(search_payload,search['total_count_pointer'])) != len(records):
                    raise ValueError('native total proves missing announcement pages')
            else:
                contract=search['completeness_contract']
                contract_body,_=_official_bytes(contract['source'],evidence_root,search['exchange'])
                if not contract.get('reviewed_by') or not contract.get('quote') or contract['quote'].encode() not in contract_body:
                    raise ValueError('endpoint completeness contract not evidenced')
            if not isinstance(records, list):
                raise ValueError('announcement search must enumerate rows')
            # Complete pagination and per-record review are explicit evidence,
            # never inferred from an empty/default API page or date filename.
            if (not search.get('complete') or not search.get('pagination_complete')
                    or search['total_records'] != len(records)
                    or len(search['dispositions']) != len(records)
                    or aware(datetime.fromisoformat(search['from_at'])) > datetime.fromisoformat(period_end+'T00:00:00+05:30')
                    or aware(datetime.fromisoformat(search['through_at'])) < stamp):
                raise ValueError('earlier announcement search incomplete')
            seen = set()
            for record, disposition in zip(records, search['dispositions']):
                if str(record[search['identity_field']]) != str(search['exchange_key']):
                    raise ValueError('search row issuer mismatch')
                rid = str(record[search['native_id_field']])
                if rid != str(disposition['native_announcement_id']) or rid in seen:
                    raise ValueError('search review row binding mismatch')
                seen.add(rid)
                if not disposition.get('reviewed_by') or not disposition.get('reason'):
                    raise ValueError('unreviewed search record')
                kind = disposition['kind']
                if kind not in {'actual_results', 'board_notice', 'other_period', 'other_document', 'linked_correction', 'duplicate'}:
                    raise ValueError('ambiguous earlier announcement')
                if kind in {'linked_correction', 'duplicate'}:
                    if not disposition.get('parent_id'):raise ValueError('correction/duplicate missing explicit parent')
                    rel=disposition['relationship_review']
                    rel_body,_=_official_bytes(rel['source'],evidence_root,search['exchange'])
                    if not rel.get('reviewed_by') or not rel.get('quote') or rel['quote'].encode() not in rel_body:
                        raise ValueError('correction relationship source not substantiated')
                if search['exchange']==exchange and rid==native and kind=='actual_results':chosen_present=True
                if kind == 'actual_results':
                    if disposition['period_end'] == period_end:
                        if not disposition.get('content_review_ref'):
                            raise ValueError('search actual-results classification needs content evidence')
                        if search['exchange']==exchange and rid==native:
                            if disposition['content_review_ref']!='content_review':raise ValueError('chosen content review reference mismatch')
                        else:
                            other=search['disclosure_reviews'][rid]
                            content_check=verify_disclosure(other,company_id=company_id,period_end=period_end,evidence_root=evidence_root,_content_only=True)
                            if not content_check['actual_results_verified'] or str(other['native_announcement_id'])!=rid:
                                raise ValueError('other actual-results disclosure lacks verified content')
                        prior = aware(datetime.fromisoformat(disposition['publication_at_ist']))
                        raw_field=search.get('public_timestamp_field',disclosure['public_timestamp_field'])
                        if raw_field not in {'EXCHANGE DISSEMINATION TIME','exchdisstime','DissemDT','dissemDT'}:
                            raise ValueError('earlier actual-results timestamp semantics need separate review')
                        bound,_ = publication_time({'EXCHANGE DISSEMINATION TIME':record[raw_field]},field_evidence=Check(Outcome.VERIFIED,'search_source_bound',search_receipt['provenance_locator']))
                        if bound != prior:raise ValueError('search disposition publication time not source bound')
                        if prior < stamp:
                            raise ValueError('earlier actual-results disclosure found')
            covered.add(search['exchange'])
        if not chosen_present:raise ValueError('chosen original absent from reviewed announcement search')
        if covered != expected_venues:
            raise ValueError('issuer exchange earlier-search coverage incomplete')
        result.update(earliest_verified=True, original_release_at_ist=stamp.isoformat())
    except (ValueError, TypeError, KeyError, IndexError, OSError, UnicodeError) as exc:
        result['reasons'].append(str(exc))
    return result


def verify_event_boundaries(review, *, company_id, period_end, next_period_end, cutoff, evidence_root):
    """Source-only window validation; calendars, instruments and candles are separate."""
    start = verify_disclosure(review.get('start'), company_id=company_id, period_end=period_end, evidence_root=evidence_root)
    nxt = verify_disclosure(review.get('next'), company_id=company_id, period_end=next_period_end, evidence_root=evidence_root)
    limit = aware(datetime.fromisoformat(cutoff))
    for item in (start, nxt):
        if item['timestamp_verified'] and datetime.fromisoformat(item['publication_at_ist']) > limit:
            item.update(timestamp_verified=False, earliest_verified=False, original_release_at_ist=None)
            item['reasons'].append('PUBLICATION_AFTER_OBSERVATION_CUTOFF')
    first, last = start['original_release_at_ist'], nxt['original_release_at_ist']
    complete = bool(first and last and datetime.fromisoformat(first) < datetime.fromisoformat(last))
    reasons = []
    if first and last and not complete:
        reasons.append('NEXT_RELEASE_NOT_AFTER_START')
    return dict(start=start, next=nxt, start_verified=bool(first), next_verified=bool(last),
                complete=complete, start_inclusive_ist=first,
                end_exclusive_ist=last if complete else None, reasons=reasons)
