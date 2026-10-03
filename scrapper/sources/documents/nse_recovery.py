"""Bounded NSE corporate-announcement rating recovery adapter."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlencode, urlsplit

from ...core.http import validate_exchange_dataset_url
from ...documents.recovery import RecoverySourceType, RecoveryTarget
from .recovery_common import (
    OfficialCandidateObservation,
    RATING_TERMS,
    RecoveryQuery,
    date_window,
    explicit_document_originator,
    first_value,
    json_rows,
    normalize_attachment_url,
    parse_publication_time,
    relevant_title,
)


NSE_ANNOUNCEMENTS_API = "https://www.nseindia.com/api/corporate-announcements"


class NSECorporateAnnouncementResolver:
    source = "nse"

    def build_queries(self, target: RecoveryTarget, *, window_days: int = 21) -> tuple[RecoveryQuery, ...]:
        date_from, date_to = date_window(target, days=window_days)
        queries: list[RecoveryQuery] = []
        for symbol in target.nse_symbols:
            parameters = urlencode({
                "index": "equities",
                "from_date": date.fromisoformat(date_from).strftime("%d-%m-%Y"),
                "to_date": date.fromisoformat(date_to).strftime("%d-%m-%Y"),
                "symbol": symbol,
            })
            url = f"{NSE_ANNOUNCEMENTS_API}?{parameters}"
            validate_exchange_dataset_url(url, "NSE")
            queries.append(RecoveryQuery("nse", url, "NSE", symbol, date_from, date_to))
        return tuple(queries)

    def parse_response(
        self, content: bytes, *, query: RecoveryQuery, discovered_at_utc: str,
        terms: tuple[str, ...] = RATING_TERMS,
    ) -> tuple[OfficialCandidateObservation, ...]:
        payload = json.loads(content.decode("utf-8"))
        observations: list[OfficialCandidateObservation] = []
        for row in json_rows(payload):
            title = first_value(row, "desc", "subject", "purpose", "smIndustry")
            category = first_value(row, "attchmntText", "category", "subCategory")
            if not relevant_title(" ".join(filter(None, (title, category))), terms):
                continue
            attachment = first_value(
                row, "attchmntFile", "attachment", "attachmentUrl", "fileName"
            )
            if not attachment:
                continue
            url = normalize_attachment_url(query.url, attachment)
            validate_exchange_dataset_url(url, "NSE")
            timestamp_raw = first_value(row, "an_dt", "broadcastDate", "timestamp")
            publication_date, published_at = parse_publication_time(timestamp_raw)
            symbol = first_value(row, "symbol")
            isin = first_value(row, "isin", "sm_isin")
            identifiers = {"NSE": (symbol.upper(),)} if symbol else {}
            if isin:
                identifiers["ISIN"] = (isin.upper(),)
            evidence = {
                "exchange": "NSE",
                "announcement_id": first_value(row, "seq_id", "announcementId", "id"),
                "announcement_timestamp_raw": timestamp_raw,
                "filing_category": category,
                "issuer": first_value(row, "sm_name", "companyName", "company"),
                "query_identifier_kind": query.identifier_kind,
                "query_identifier_value": query.identifier_value,
                "returned_identifier_kinds": sorted(identifiers),
            }
            observations.append(OfficialCandidateObservation(
                candidate_url=url,
                retrieval_host=(urlsplit(url).hostname or "").lower(),
                source_type=RecoverySourceType.NSE_EXCHANGE_ATTACHMENT,
                discovery_page_url=query.url,
                document_title=title,
                publication_date=publication_date,
                published_at_utc=published_at,
                available_at_utc=published_at or discovered_at_utc,
                availability_basis=("exchange_filing_timestamp" if published_at else "first_observation"),
                company_identifiers=identifiers,
                document_originator=explicit_document_originator(
                    title, category, attachment
                ),
                rating=None,
                outlook=None,
                instrument_or_facility=None,
                source_evidence=evidence,
            ))
        return tuple(observations)
