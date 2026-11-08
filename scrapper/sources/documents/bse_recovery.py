"""Bounded BSE corporate/credit-rating announcement recovery adapter."""

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


BSE_ANNOUNCEMENTS_API = (
    "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
)


class BSECorporateRatingResolver:
    source = "bse"

    def build_queries(self, target: RecoveryTarget, *, window_days: int = 21) -> tuple[RecoveryQuery, ...]:
        date_from, date_to = date_window(target, days=window_days)
        queries: list[RecoveryQuery] = []
        for code in target.bse_codes:
            parameters = urlencode({
                "pageno": 1, "strCat": -1,
                "strPrevDate": date.fromisoformat(date_from).strftime("%Y%m%d"),
                "strScrip": code, "strSearch": "P",
                "strToDate": date.fromisoformat(date_to).strftime("%Y%m%d"),
                "strType": "C",
            })
            url = f"{BSE_ANNOUNCEMENTS_API}?{parameters}"
            validate_exchange_dataset_url(url, "BSE")
            queries.append(RecoveryQuery("bse", url, "BSE", code, date_from, date_to))
        return tuple(queries)

    def parse_response(
        self, content: bytes, *, query: RecoveryQuery, discovered_at_utc: str,
        terms: tuple[str, ...] = RATING_TERMS,
    ) -> tuple[OfficialCandidateObservation, ...]:
        payload = json.loads(content.decode("utf-8"))
        observations: list[OfficialCandidateObservation] = []
        for row in json_rows(payload):
            title = first_value(row, "NEWSSUB", "subject", "headline")
            category = first_value(row, "CATEGORYNAME", "SUBCATNAME", "category")
            if not relevant_title(" ".join(filter(None, (title, category))), terms):
                continue
            attachment = first_value(
                row, "ATTACHMENTNAME", "NSURL", "attachment", "attachmentUrl"
            )
            if not attachment:
                continue
            old_flag = first_value(row, "OLD")
            historical = old_flag is not None and old_flag.strip().lower() not in {
                "0", "false", "n", "no"
            }
            if not attachment.lower().startswith("https://"):
                attachment_bucket = "AttachHis" if historical else "AttachLive"
                attachment = (
                    f"https://www.bseindia.com/xml-data/corpfiling/{attachment_bucket}/"
                    + attachment.lstrip("/")
                )
            url = normalize_attachment_url(query.url, attachment)
            validate_exchange_dataset_url(url, "BSE")
            timestamp_raw = first_value(row, "DT_TM", "NEWS_DT", "timestamp")
            publication_date, published_at = parse_publication_time(timestamp_raw)
            code = first_value(row, "SCRIP_CD", "scripCode")
            isin = first_value(row, "ISIN", "isin")
            identifiers = {"BSE": (code,)} if code else {}
            if isin:
                identifiers["ISIN"] = (isin.upper(),)
            observations.append(OfficialCandidateObservation(
                candidate_url=url,
                retrieval_host=(urlsplit(url).hostname or "").lower(),
                source_type=RecoverySourceType.BSE_EXCHANGE_ATTACHMENT,
                discovery_page_url=query.url,
                document_title=title,
                publication_date=publication_date,
                published_at_utc=published_at,
                available_at_utc=published_at or discovered_at_utc,
                availability_basis=("exchange_filing_timestamp" if published_at else "first_observation"),
                company_identifiers=identifiers,
                document_originator=explicit_document_originator(
                    title,
                    category,
                    first_value(row, "HEADLINE", "MORE", "XML_NAME"),
                    attachment,
                ),
                rating=None,
                outlook=None,
                instrument_or_facility=None,
                source_evidence={
                    "exchange": "BSE",
                    "filing_id": first_value(row, "NEWSID", "newsId", "id"),
                    "exchange_timestamp_raw": timestamp_raw,
                    "filing_category": category,
                    "attachment_archive": "historical" if historical else "live",
                    "issuer": first_value(row, "SLONGNAME", "companyName", "company"),
                    "query_identifier_kind": query.identifier_kind,
                    "query_identifier_value": query.identifier_value,
                    "returned_identifier_kinds": sorted(identifiers),
                },
            ))
        return tuple(observations)
