"""Promote accepted Screener document-link metadata into D01 artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from calendar import monthrange
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from ..db.repo import utc_now


PROMOTER_VERSION = "1.2.0"
PROMOTABLE = {
    "concall_transcript": "transcript",
    "concall_ppt": "presentation",
    "concall_recording": "recording",
    "credit_rating": "credit_rating",
}


def _exact_event_date(value: str | None) -> str | None:
    from ..documents.discovery.promotion import _exact_event_date as _owner
    return _owner(value, datetime=datetime)


def _result_period_end(value: str | None) -> str | None:
    from ..documents.discovery.promotion import _result_period_end as _owner
    return _owner(value, datetime=datetime, monthrange=monthrange)


def call_matches_result_window(label: str | None, period_end: str | None) -> bool:
    from ..documents.discovery.promotion import call_matches_result_window as _owner
    return _owner(label, period_end, datetime=datetime)


def url_fiscal_year_conflicts(url: str | None, period_end: str | None) -> bool:
    from ..documents.discovery.promotion import url_fiscal_year_conflicts as _owner
    return _owner(url, period_end, datetime=datetime, re=re, unquote=unquote, urlsplit=urlsplit)


def _normalized_http_url(value: str | None) -> str | None:
    from ..documents.discovery.promotion import _normalized_http_url as _owner
    return _owner(value, urlsplit=urlsplit, urlunsplit=urlunsplit)


def _accepted_links(
    conn: sqlite3.Connection,
    *,
    document_type: str,
    parse_id: int | None,
    limit: int | None,
    verified_company_id: int | None = None,
) -> list[sqlite3.Row]:
    from ..documents.discovery.promotion import _accepted_links as _owner
    return _owner(conn, document_type=document_type, parse_id=parse_id, limit=limit, verified_company_id=verified_company_id, Any=Any)


def _record_promotion(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    artifact_id: int | None,
    artifact_source_id: int | None,
    status: str,
    reason: str,
    normalized_url: str | None,
    artifact_type: str | None,
    promoted_at_utc: str,
) -> None:
    from ..documents.discovery.promotion import _record_promotion as _owner
    return _owner(conn, row=row, artifact_id=artifact_id, artifact_source_id=artifact_source_id, status=status, reason=reason, normalized_url=normalized_url, artifact_type=artifact_type, promoted_at_utc=promoted_at_utc, PROMOTER_VERSION=PROMOTER_VERSION)


def promote_document_links(
    conn: sqlite3.Connection,
    *,
    document_type: str = "credit_rating",
    dry_run: bool = False,
    limit: int | None = None,
    parse_id: int | None = None,
    promoted_at_utc: str | None = None,
    resolved_result_period: str | None = None,
    verified_company_id: int | None = None,
) -> dict[str, Any]:
    from ..documents.discovery.promotion import promote_document_links as _owner
    return _owner(conn, document_type=document_type, dry_run=dry_run, limit=limit, parse_id=parse_id, promoted_at_utc=promoted_at_utc, resolved_result_period=resolved_result_period, verified_company_id=verified_company_id, Counter=Counter, PROMOTABLE=PROMOTABLE, PROMOTER_VERSION=PROMOTER_VERSION, _accepted_links=_accepted_links, _exact_event_date=_exact_event_date, _normalized_http_url=_normalized_http_url, _record_promotion=_record_promotion, _result_period_end=_result_period_end, call_matches_result_window=call_matches_result_window, hashlib=hashlib, json=json, url_fiscal_year_conflicts=url_fiscal_year_conflicts, urlsplit=urlsplit, utc_now=utc_now)
