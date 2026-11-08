"""First complete Screener vertical slice."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..core.collection_scope import MarketCapBelowFloor, parsed_cap, scope_snapshot
from ..config import (
    DB_PATH,
    EXPORTS,
    MANIFEST_VERSION,
    PARSER_NAME,
    PARSER_VERSION,
    PEER_PARSER_NAME,
    PEER_PARSER_VERSION,
    SNAPSHOTS,
    SCREENER_MIN_MARKET_CAP_CRORE,
)
from ..core.http import (
    FetchResponse,
    ScreenerHostClient,
    fetch_screener,
    fetch_screener_peers,
    fetch_screener_quick_ratios,
    fetch_screener_commentary,
)
from ..db.collection_scope_repo import accepted_scope
from ..db.database import connect, init_db
from ..db.repo import (
    ensure_company,
    ensure_source_identifier,
    finish_run,
    get_or_create_parse_artifact,
    link_supplemental_parse,
    record_fetch,
    start_run,
    store_blob,
    store_parsed_company,
    store_parsed_peer_table,
    utc_now,
)
from ..exports.json_export import write_export
from .accepted_document_downloads import (
    enqueue_concall_documents_for_accepted_parse,
    enqueue_documents_for_accepted_parse,
)
from ..sources.screener.company import (
    parse_company_page,
    parse_quick_ratios_fragment,
    parse_commentary_fragment,
)
from ..sources.screener.peers import parse_peer_table
from ..sources.screener.urls import (
    company_path_url,
    company_url,
    peer_table_url,
    quick_ratios_url,
    commentary_url,
)
from ..validation.screener import accept_parse, validate_parse, validate_peer_parse





from ..workflows.screener import ingest as _workflow
from ..workflows.screener.ingest import VerticalSliceResult, DocumentRefreshResult

def _market_cap_outside_scope(parsed: object) -> bool:
    return _workflow._market_cap_outside_scope(parsed, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, parsed_cap=parsed_cap)


def ingest_screener_response(
    symbol: str,
    scope: str,
    response: FetchResponse,
    peer_response: FetchResponse | None = None,
    quick_ratios_response: FetchResponse | None = None,
    commentary_response: FetchResponse | None = None,
    *,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    export_root: Path = EXPORTS,
    fetched_at_utc: str | None = None,
    expected_result_period: str | None = None,
    document_only: bool = False,
    expected_company_id: int | None = None,
) -> VerticalSliceResult | DocumentRefreshResult:
    return _workflow.ingest_screener_response(symbol, scope, response, peer_response, quick_ratios_response, commentary_response, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, fetched_at_utc=fetched_at_utc, expected_result_period=expected_result_period, document_only=document_only, expected_company_id=expected_company_id, DocumentRefreshResult=DocumentRefreshResult, MANIFEST_VERSION=MANIFEST_VERSION, MarketCapBelowFloor=MarketCapBelowFloor, PARSER_NAME=PARSER_NAME, PARSER_VERSION=PARSER_VERSION, PEER_PARSER_NAME=PEER_PARSER_NAME, PEER_PARSER_VERSION=PEER_PARSER_VERSION, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, VerticalSliceResult=VerticalSliceResult, __version__=__version__, _market_cap_outside_scope=_market_cap_outside_scope, accept_parse=accept_parse, accepted_scope=accepted_scope, connect=connect, enqueue_concall_documents_for_accepted_parse=enqueue_concall_documents_for_accepted_parse, enqueue_documents_for_accepted_parse=enqueue_documents_for_accepted_parse, ensure_company=ensure_company, ensure_source_identifier=ensure_source_identifier, finish_run=finish_run, get_or_create_parse_artifact=get_or_create_parse_artifact, init_db=init_db, link_supplemental_parse=link_supplemental_parse, parse_commentary_fragment=parse_commentary_fragment, parse_company_page=parse_company_page, parse_peer_table=parse_peer_table, parse_quick_ratios_fragment=parse_quick_ratios_fragment, parsed_cap=parsed_cap, peer_table_url=peer_table_url, record_fetch=record_fetch, scope_snapshot=scope_snapshot, start_run=start_run, store_blob=store_blob, store_parsed_company=store_parsed_company, store_parsed_peer_table=store_parsed_peer_table, utc_now=utc_now, validate_parse=validate_parse, validate_peer_parse=validate_peer_parse, write_export=write_export)


def scrape_screener_company(
    symbol: str,
    scope: str = "standalone",
    *,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    export_root: Path = EXPORTS,
) -> VerticalSliceResult:
    return _workflow.scrape_screener_company(symbol, scope, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, commentary_url=commentary_url, company_url=company_url, fetch_screener=fetch_screener, fetch_screener_commentary=fetch_screener_commentary, fetch_screener_peers=fetch_screener_peers, fetch_screener_quick_ratios=fetch_screener_quick_ratios, ingest_screener_response=ingest_screener_response, parse_company_page=parse_company_page, peer_table_url=peer_table_url, quick_ratios_url=quick_ratios_url, scope_snapshot=scope_snapshot, utc_now=utc_now)


def scrape_screener_company_path(
    company_path: str,
    reporting_scope_hint: str,
    *,
    expected_result_period: str | None = None,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    export_root: Path = EXPORTS,
    host_client: ScreenerHostClient | None = None,
    document_only: bool = False,
    expected_company_id: int | None = None,
) -> VerticalSliceResult | DocumentRefreshResult:
    return _workflow.scrape_screener_company_path(company_path, reporting_scope_hint, expected_result_period=expected_result_period, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, host_client=host_client, document_only=document_only, expected_company_id=expected_company_id, MarketCapBelowFloor=MarketCapBelowFloor, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, ScreenerHostClient=ScreenerHostClient, _market_cap_outside_scope=_market_cap_outside_scope, commentary_url=commentary_url, company_path_url=company_path_url, ingest_screener_response=ingest_screener_response, parse_company_page=parse_company_page, parsed_cap=parsed_cap, peer_table_url=peer_table_url, quick_ratios_url=quick_ratios_url, scope_snapshot=scope_snapshot, utc_now=utc_now)


def reprocess_screener_blobs(
    symbol: str,
    scope: str,
    blob_id: int,
    peer_blob_id: int,
    *,
    db_path: Path = DB_PATH,
    export_root: Path = EXPORTS,
) -> VerticalSliceResult:
    return _workflow.reprocess_screener_blobs(symbol, scope, blob_id, peer_blob_id, db_path=db_path, export_root=export_root, MANIFEST_VERSION=MANIFEST_VERSION, PARSER_NAME=PARSER_NAME, PARSER_VERSION=PARSER_VERSION, PEER_PARSER_NAME=PEER_PARSER_NAME, PEER_PARSER_VERSION=PEER_PARSER_VERSION, Path=Path, VerticalSliceResult=VerticalSliceResult, __version__=__version__, accept_parse=accept_parse, connect=connect, enqueue_documents_for_accepted_parse=enqueue_documents_for_accepted_parse, ensure_company=ensure_company, ensure_source_identifier=ensure_source_identifier, finish_run=finish_run, get_or_create_parse_artifact=get_or_create_parse_artifact, init_db=init_db, link_supplemental_parse=link_supplemental_parse, parse_commentary_fragment=parse_commentary_fragment, parse_company_page=parse_company_page, parse_peer_table=parse_peer_table, parse_quick_ratios_fragment=parse_quick_ratios_fragment, peer_table_url=peer_table_url, start_run=start_run, store_parsed_company=store_parsed_company, store_parsed_peer_table=store_parsed_peer_table, validate_parse=validate_parse, validate_peer_parse=validate_peer_parse, write_export=write_export)
