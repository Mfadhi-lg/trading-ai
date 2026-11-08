"""Screener fetch, ingest and immutable saved-blob workflow ownership."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ... import __version__
from ...core.collection_scope import MarketCapBelowFloor, parsed_cap, scope_snapshot
from ...config import (
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
from ...core.http import (
    FetchResponse,
    ScreenerHostClient,
    fetch_screener,
    fetch_screener_peers,
    fetch_screener_quick_ratios,
    fetch_screener_commentary,
)
from ...db.collection_scope_repo import accepted_scope
from ...db.database import connect, init_db
from ...db.repo import (
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
from ...exports.json_export import write_export
from ...pipeline.accepted_document_downloads import (
    enqueue_concall_documents_for_accepted_parse,
    enqueue_documents_for_accepted_parse,
)
from ...sources.screener.company import (
    parse_company_page,
    parse_quick_ratios_fragment,
    parse_commentary_fragment,
)
from ...sources.screener.peers import parse_peer_table
from ...sources.screener.urls import (
    company_path_url,
    company_url,
    peer_table_url,
    quick_ratios_url,
    commentary_url,
)
from ...validation.screener import accept_parse, validate_parse, validate_peer_parse


@dataclass(frozen=True)
class VerticalSliceResult:
    run_id: int
    fetch_id: int
    blob_id: int
    parse_id: int
    peer_fetch_id: int
    peer_blob_id: int
    peer_parse_id: int
    company_id: int
    export_path: Path
    reused_parse: bool


@dataclass(frozen=True)
class DocumentRefreshResult:
    parse_id: int
    company_id: int
    promoted_document_only: bool


def _market_cap_outside_scope(parsed: object, *, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, parsed_cap=parsed_cap) -> bool:
    """Require a numeric Screener market cap strictly above the scope floor."""
    cap = parsed_cap(parsed)
    return cap is not None and cap < SCREENER_MIN_MARKET_CAP_CRORE

def ingest_screener_response(symbol: str, scope: str, response: FetchResponse, peer_response: FetchResponse | None=None, quick_ratios_response: FetchResponse | None=None, commentary_response: FetchResponse | None=None, *, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, export_root: Path=EXPORTS, fetched_at_utc: str | None=None, expected_result_period: str | None=None, document_only: bool=False, expected_company_id: int | None=None, ownership_check=None, DocumentRefreshResult=DocumentRefreshResult, MANIFEST_VERSION=MANIFEST_VERSION, MarketCapBelowFloor=MarketCapBelowFloor, PARSER_NAME=PARSER_NAME, PARSER_VERSION=PARSER_VERSION, PEER_PARSER_NAME=PEER_PARSER_NAME, PEER_PARSER_VERSION=PEER_PARSER_VERSION, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, VerticalSliceResult=VerticalSliceResult, __version__=__version__, _market_cap_outside_scope=_market_cap_outside_scope, accept_parse=accept_parse, accepted_scope=accepted_scope, connect=connect, enqueue_concall_documents_for_accepted_parse=enqueue_concall_documents_for_accepted_parse, enqueue_documents_for_accepted_parse=enqueue_documents_for_accepted_parse, ensure_company=ensure_company, ensure_source_identifier=ensure_source_identifier, finish_run=finish_run, get_or_create_parse_artifact=get_or_create_parse_artifact, init_db=init_db, link_supplemental_parse=link_supplemental_parse, parse_commentary_fragment=parse_commentary_fragment, parse_company_page=parse_company_page, parse_peer_table=parse_peer_table, parse_quick_ratios_fragment=parse_quick_ratios_fragment, parsed_cap=parsed_cap, peer_table_url=peer_table_url, record_fetch=record_fetch, scope_snapshot=scope_snapshot, start_run=start_run, store_blob=store_blob, store_parsed_company=store_parsed_company, store_parsed_peer_table=store_parsed_peer_table, utc_now=utc_now, validate_parse=validate_parse, validate_peer_parse=validate_peer_parse, write_export=write_export) -> VerticalSliceResult | DocumentRefreshResult:
    if document_only:
        from ..documents.screener_refresh import ingest_document_response
        return ingest_document_response(response=response,company_id=expected_company_id,result_period=expected_result_period,
            db_path=db_path,snapshot_root=snapshot_root,ownership_check=ownership_check,connection_factory=connect)
    expected_symbol = symbol.strip().upper()
    fetched_at = fetched_at_utc or utc_now()
    conn = connect(db_path)
    init_db(conn)
    if ownership_check is not None:
        ownership_check(conn)
    run_id = start_run(conn, __version__, {'source': 'screener', 'symbol': expected_symbol, 'scope': scope})
    try:
        blob_id = store_blob(conn, response, expected_symbol, scope, fetched_at, snapshot_root)
        fetch_id = record_fetch(conn, run_id, None, response, scope, fetched_at, blob_id)
        if response.fetch_status != 'ok':
            raise RuntimeError(f'Response rejected before parsing: status={response.status_code} classification={response.fetch_status}')
        blob = conn.execute('SELECT first_observed_at_utc FROM response_blobs WHERE blob_id=?', (blob_id,)).fetchone()
        first_observed = str(blob['first_observed_at_utc'])
        cap = scope_snapshot(response.content)['market_cap_crore']
        if cap is not None and cap < SCREENER_MIN_MARKET_CAP_CRORE:
            error = MarketCapBelowFloor(cap, SCREENER_MIN_MARKET_CAP_CRORE)
            error.blob_id = blob_id
            raise error
        parsed = parse_company_page(response.content, first_observed)
        if _market_cap_outside_scope(parsed):
            raise MarketCapBelowFloor(parsed_cap(parsed), SCREENER_MIN_MARKET_CAP_CRORE)
        if quick_ratios_response is not None and quick_ratios_response.fetch_status == 'ok':
            qr_fields, qr_metrics = parse_quick_ratios_fragment(quick_ratios_response.content, position_offset=len(parsed.ratio_fields))
            parsed.ratio_fields.extend(qr_fields)
            parsed.metrics.extend(qr_metrics)
        if commentary_response is not None and commentary_response.fetch_status == 'ok':
            parsed.commentary_texts = parse_commentary_fragment(commentary_response.content)
        if expected_result_period is not None and expected_result_period != '1970-01':
            actual_periods = {item.period_end[:7] for item in parsed.financials if item.section_code == 'quarters' and item.period_end}
            if expected_result_period not in actual_periods:
                raise RuntimeError(f'Screen result period is absent from company quarterly results: expected={expected_result_period} actual={sorted(actual_periods)}')
        observed_periods = {item.period_end[:7] for item in parsed.financials if item.section_code == 'quarters' and item.period_end}
        resolved_result_period = max(observed_periods) if observed_periods else 'not_applicable'
        company_id = ensure_company(conn, parsed)
        if expected_company_id is not None and company_id != expected_company_id:
            raise ValueError('Document refresh resolved a different company identity')
        ensure_source_identifier(conn, company_id, parsed, parsed.canonical_url or response.final_url)
        conn.execute('UPDATE fetch_observations SET company_id=? WHERE fetch_id=?', (company_id, fetch_id))
        if quick_ratios_response is not None:
            qr_blob_id = store_blob(conn, quick_ratios_response, expected_symbol, scope, fetched_at, snapshot_root, resource_kind='quick_ratios')
            record_fetch(conn, run_id, company_id, quick_ratios_response, scope, fetched_at, qr_blob_id, resource_kind='quick_ratios')
            if quick_ratios_response.fetch_status != 'ok':
                raise RuntimeError(f'Quick-ratios response rejected before parsing: status={quick_ratios_response.status_code} classification={quick_ratios_response.fetch_status}')
        if commentary_response is not None:
            comm_blob_id = store_blob(conn, commentary_response, expected_symbol, scope, fetched_at, snapshot_root, resource_kind='commentary')
            record_fetch(conn, run_id, company_id, commentary_response, scope, fetched_at, comm_blob_id, resource_kind='commentary')
        conn.commit()
        parse_id, parse_status = get_or_create_parse_artifact(conn, blob_id, PARSER_NAME, PARSER_VERSION, MANIFEST_VERSION)
        reused_parse = parse_status == 'ok'
        if parse_status == 'pending':
            store_parsed_company(conn, parse_id, parsed, first_observed)
        elif parse_status != 'ok':
            raise RuntimeError('This blob/parser version has a failed artifact; bump the parser version after fixing the parser')
        if not parsed.source_warehouse_id:
            raise RuntimeError('Screener warehouse ID for peer request is missing')
        expected_peer_url = peer_table_url(parsed.source_warehouse_id)
        if peer_response is None:
            raise RuntimeError('Peer capture requires the saved supplemental peer-table response')
        peer_blob_id = store_blob(conn, peer_response, expected_symbol, scope, fetched_at, snapshot_root, resource_kind='peer_table')
        peer_fetch_id = record_fetch(conn, run_id, company_id, peer_response, scope, fetched_at, peer_blob_id, resource_kind='peer_table')
        if peer_response.fetch_status != 'ok':
            raise RuntimeError(f'Peer response rejected before parsing: status={peer_response.status_code} classification={peer_response.fetch_status}')
        if peer_response.requested_url.rstrip('/') != expected_peer_url.rstrip('/'):
            raise RuntimeError('Peer response URL does not match the page warehouse ID')
        peer_blob = conn.execute('SELECT first_observed_at_utc FROM response_blobs WHERE blob_id=?', (peer_blob_id,)).fetchone()
        peer_first_observed = str(peer_blob['first_observed_at_utc'])
        peer_parse_id, peer_parse_status = get_or_create_parse_artifact(conn, peer_blob_id, PEER_PARSER_NAME, PEER_PARSER_VERSION, MANIFEST_VERSION)
        if peer_parse_status == 'pending':
            parsed_peers = parse_peer_table(peer_response.content, parsed.source_company_id)
            store_parsed_peer_table(conn, peer_parse_id, parsed_peers, scope, peer_first_observed)
            validate_peer_parse(conn, peer_parse_id, parsed.source_company_id, expected_symbol)
        elif peer_parse_status != 'ok':
            raise RuntimeError('This peer blob/parser version has a failed artifact; bump the peer parser version after fixing the parser')
        link_supplemental_parse(conn, parse_id, 'peer_table', peer_parse_id)
        main_artifact = conn.execute('SELECT validation_status FROM parse_artifacts WHERE parse_id=?', (parse_id,)).fetchone()
        if main_artifact['validation_status'] == 'pending':
            validate_parse(conn, parse_id, expected_symbol, scope)
        if ownership_check is not None:
            ownership_check(conn)
        if document_only:
            if expected_company_id is None or expected_result_period is None:
                raise ValueError('Document-only refresh requires an exact company and period')
            if parsed.reporting_scope != 'standalone':
                raise ValueError('Document-only refresh requires standalone results')
            enqueue_concall_documents_for_accepted_parse(conn, parse_id=parse_id, verified_company_id=company_id, resolved_result_period=expected_result_period)
            finish_run(conn, run_id, 'ok')
            return DocumentRefreshResult(parse_id, company_id, True)
        accept_parse(conn, company_id, parse_id, 'screener', scope, resolved_result_period)
        accepted_scope(conn, company_id=company_id, value=parsed_cap(parsed), canonical_url=parsed.canonical_url or response.final_url, observed_at=fetched_at, parse_id=parse_id)
        enqueue_documents_for_accepted_parse(conn, parse_id=parse_id, resolved_result_period=resolved_result_period)
        export_path = write_export(conn, run_id, company_id, parse_id, expected_symbol, export_root)
        finish_run(conn, run_id, 'ok')
        return VerticalSliceResult(run_id, fetch_id, blob_id, parse_id, peer_fetch_id, peer_blob_id, peer_parse_id, company_id, export_path, reused_parse)
    except Exception:
        finish_run(conn, run_id, 'error')
        raise
    finally:
        conn.close()

def scrape_screener_company(symbol: str, scope: str='standalone', *, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, export_root: Path=EXPORTS, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, commentary_url=commentary_url, company_url=company_url, fetch_screener=fetch_screener, fetch_screener_commentary=fetch_screener_commentary, fetch_screener_peers=fetch_screener_peers, fetch_screener_quick_ratios=fetch_screener_quick_ratios, ingest_screener_response=ingest_screener_response, parse_company_page=parse_company_page, peer_table_url=peer_table_url, quick_ratios_url=quick_ratios_url, scope_snapshot=scope_snapshot, utc_now=utc_now) -> VerticalSliceResult:
    if scope != 'standalone':
        raise ValueError('Screener financial ingestion is standalone-only')
    url = company_url(symbol, scope)
    response = fetch_screener(url)
    if response.fetch_status != 'ok':
        return ingest_screener_response(symbol, scope, response, None, None, None, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root)
    cap = scope_snapshot(response.content)['market_cap_crore']
    if cap is not None and cap < SCREENER_MIN_MARKET_CAP_CRORE:
        return ingest_screener_response(symbol, scope, response, None, None, None, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root)
    parsed = parse_company_page(response.content, utc_now())
    if not parsed.source_warehouse_id:
        raise RuntimeError('Screener warehouse ID for peer request is missing')
    peers = fetch_screener_peers(peer_table_url(parsed.source_warehouse_id), response.final_url)
    quick_ratios = fetch_screener_quick_ratios(quick_ratios_url(parsed.source_warehouse_id), response.final_url)
    commentary = None
    if parsed.source_company_id:
        try:
            commentary = fetch_screener_commentary(commentary_url(parsed.source_company_id), response.final_url)
        except (OSError, ValueError, RuntimeError) as exc:
            from ...core.host_request_contracts import AdministrativeDefer
            from ...db.document_work_claims import OwnershipLost
            if isinstance(exc,(AdministrativeDefer,OwnershipLost)):
                raise
            commentary = None
    return ingest_screener_response(symbol, scope, response, peers, quick_ratios, commentary, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root)

def scrape_screener_company_path(company_path: str, reporting_scope_hint: str, *, expected_result_period: str | None=None, db_path: Path=DB_PATH, snapshot_root: Path=SNAPSHOTS, export_root: Path=EXPORTS, host_client: ScreenerHostClient | None=None, document_only: bool=False, expected_company_id: int | None=None, document_connection_factory=None, MarketCapBelowFloor=MarketCapBelowFloor, SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE, ScreenerHostClient=ScreenerHostClient, _market_cap_outside_scope=_market_cap_outside_scope, commentary_url=commentary_url, company_path_url=company_path_url, ingest_screener_response=ingest_screener_response, parse_company_page=parse_company_page, parsed_cap=parsed_cap, peer_table_url=peer_table_url, quick_ratios_url=quick_ratios_url, scope_snapshot=scope_snapshot, utc_now=utc_now) -> VerticalSliceResult | DocumentRefreshResult:
    """Run the full accepted company pipeline against an exact discovered path."""
    url, path_scope = company_path_url(company_path)
    if reporting_scope_hint != 'standalone':
        raise ValueError('Screener financial ingestion is standalone-only')
    if path_scope != reporting_scope_hint:
        raise ValueError('Discovered path and reporting scope hint disagree')
    host_client = host_client or ScreenerHostClient()
    def guarded_ingest(*args, **kwargs):
        checkpoint = getattr(host_client, 'check', None)
        if getattr(host_client, 'shared_permits', False) is True and checkpoint is not None:
            checkpoint()
            kwargs["ownership_check"] = host_client.ownership_check
        if document_only and document_connection_factory is not None:
            kwargs["connect"] = document_connection_factory
        return ingest_screener_response(*args, **kwargs)
    response = host_client.fetch_company(url)
    path_identity = company_path.strip('/').split('/')[1].upper()
    if document_only:
        return guarded_ingest(path_identity,reporting_scope_hint,response,db_path=db_path,
            snapshot_root=snapshot_root,export_root=export_root,expected_result_period=expected_result_period,
            document_only=True,expected_company_id=expected_company_id)
    if response.fetch_status != 'ok':
        return guarded_ingest(path_identity, reporting_scope_hint, response, None, None, None, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, expected_result_period=expected_result_period, document_only=document_only, expected_company_id=expected_company_id)
    cap = scope_snapshot(response.content)['market_cap_crore']
    if cap is not None and cap < SCREENER_MIN_MARKET_CAP_CRORE:
        return guarded_ingest(path_identity, reporting_scope_hint, response, None, None, None, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, expected_result_period=expected_result_period, document_only=document_only, expected_company_id=expected_company_id)
    try:
        parsed = parse_company_page(response.content, utc_now())
    except ValueError:
        return guarded_ingest(path_identity, reporting_scope_hint, response, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, expected_result_period=expected_result_period)
    if parsed.reporting_scope != reporting_scope_hint:
        raise RuntimeError('Company page reporting scope differs from the discovered path')
    if _market_cap_outside_scope(parsed):
        raise MarketCapBelowFloor(parsed_cap(parsed), SCREENER_MIN_MARKET_CAP_CRORE)
    if expected_result_period not in (None, '1970-01') and expected_result_period not in {item.period_end[:7] for item in parsed.financials if item.section_code == 'quarters' and item.period_end}:
        return guarded_ingest(path_identity, reporting_scope_hint, response, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, expected_result_period=expected_result_period)
    durable_identifier = parsed.nse_symbol or parsed.bse_code or path_identity
    if not parsed.source_company_id:
        raise RuntimeError('Company page has no verified Screener source company ID')
    if not parsed.source_warehouse_id:
        raise RuntimeError('Screener warehouse ID for peer request is missing')
    peers = host_client.fetch_peers(peer_table_url(parsed.source_warehouse_id), response.final_url)
    quick_ratios = host_client.fetch_quick_ratios(quick_ratios_url(parsed.source_warehouse_id), response.final_url)
    commentary = None
    if parsed.source_company_id:
        try:
            commentary = host_client.fetch_commentary(commentary_url(parsed.source_company_id), response.final_url)
        except (OSError, ValueError, RuntimeError) as exc:
            from ...core.host_request_contracts import AdministrativeDefer
            from ...db.document_work_claims import OwnershipLost
            if isinstance(exc,(AdministrativeDefer,OwnershipLost)):
                raise
            commentary = None
    return guarded_ingest(durable_identifier, reporting_scope_hint, response, peers, quick_ratios, commentary, db_path=db_path, snapshot_root=snapshot_root, export_root=export_root, expected_result_period=expected_result_period, document_only=document_only, expected_company_id=expected_company_id)

def reprocess_screener_blobs(symbol: str, scope: str, blob_id: int, peer_blob_id: int, *, db_path: Path=DB_PATH, export_root: Path=EXPORTS, MANIFEST_VERSION=MANIFEST_VERSION, PARSER_NAME=PARSER_NAME, PARSER_VERSION=PARSER_VERSION, PEER_PARSER_NAME=PEER_PARSER_NAME, PEER_PARSER_VERSION=PEER_PARSER_VERSION, Path=Path, VerticalSliceResult=VerticalSliceResult, __version__=__version__, accept_parse=accept_parse, connect=connect, enqueue_documents_for_accepted_parse=enqueue_documents_for_accepted_parse, ensure_company=ensure_company, ensure_source_identifier=ensure_source_identifier, finish_run=finish_run, get_or_create_parse_artifact=get_or_create_parse_artifact, init_db=init_db, link_supplemental_parse=link_supplemental_parse, parse_commentary_fragment=parse_commentary_fragment, parse_company_page=parse_company_page, parse_peer_table=parse_peer_table, parse_quick_ratios_fragment=parse_quick_ratios_fragment, peer_table_url=peer_table_url, start_run=start_run, store_parsed_company=store_parsed_company, store_parsed_peer_table=store_parsed_peer_table, validate_parse=validate_parse, validate_peer_parse=validate_peer_parse, write_export=write_export) -> VerticalSliceResult:
    """Parse and validate already-fetched immutable responses without refetching.

    This is used when parser/manifest policy advances after a live response was
    saved. It deliberately creates no fetch observation; exports retain the
    original fetch provenance associated with each blob.
    """
    expected_symbol = symbol.strip().upper()
    conn = connect(db_path)
    init_db(conn)
    main_blob = conn.execute('SELECT * FROM response_blobs WHERE blob_id=?', (blob_id,)).fetchone()
    peer_blob = conn.execute('SELECT * FROM response_blobs WHERE blob_id=?', (peer_blob_id,)).fetchone()
    main_fetch = conn.execute("SELECT * FROM fetch_observations\n           WHERE blob_id=? AND resource_kind='company_page'\n           ORDER BY fetch_id DESC LIMIT 1", (blob_id,)).fetchone()
    peer_fetch = conn.execute("SELECT * FROM fetch_observations\n           WHERE blob_id=? AND resource_kind='peer_table'\n           ORDER BY fetch_id DESC LIMIT 1", (peer_blob_id,)).fetchone()
    if main_blob is None or peer_blob is None or main_fetch is None or (peer_fetch is None):
        conn.close()
        raise ValueError('Reprocessing requires saved company and peer blobs with fetch provenance')
    run_id = start_run(conn, __version__, {'source': 'screener', 'symbol': expected_symbol, 'scope': scope, 'mode': 'reprocess_saved_blobs', 'blob_id': blob_id, 'peer_blob_id': peer_blob_id})
    try:
        parsed = parse_company_page(Path(main_blob['local_path']).read_bytes(), str(main_blob['first_observed_at_utc']))
        qr_fetch = conn.execute("SELECT fo.*, rb.local_path FROM fetch_observations fo\n               JOIN response_blobs rb ON fo.blob_id = rb.blob_id\n               WHERE fo.run_id=? AND fo.resource_kind='quick_ratios'\n               LIMIT 1", (main_fetch['run_id'],)).fetchone()
        if qr_fetch:
            qr_fields, qr_metrics = parse_quick_ratios_fragment(Path(qr_fetch['local_path']).read_bytes(), position_offset=len(parsed.ratio_fields))
            parsed.ratio_fields.extend(qr_fields)
            parsed.metrics.extend(qr_metrics)
        comm_fetch = conn.execute("SELECT fo.*, rb.local_path FROM fetch_observations fo\n               JOIN response_blobs rb ON fo.blob_id = rb.blob_id\n               WHERE fo.run_id=? AND fo.resource_kind='commentary'\n               LIMIT 1", (main_fetch['run_id'],)).fetchone()
        if comm_fetch:
            parsed.commentary_texts = parse_commentary_fragment(Path(comm_fetch['local_path']).read_bytes())
        company_id = ensure_company(conn, parsed)
        ensure_source_identifier(conn, company_id, parsed, parsed.canonical_url or str(main_fetch['final_url']))
        conn.commit()
        parse_id, parse_status = get_or_create_parse_artifact(conn, blob_id, PARSER_NAME, PARSER_VERSION, MANIFEST_VERSION)
        reused_parse = parse_status == 'ok'
        if parse_status == 'pending':
            store_parsed_company(conn, parse_id, parsed, str(main_blob['first_observed_at_utc']))
        elif parse_status != 'ok':
            raise RuntimeError('Saved company blob has a failed current-version parse')
        if not parsed.source_warehouse_id:
            raise RuntimeError('Screener warehouse ID for peer request is missing')
        expected_peer_url = peer_table_url(parsed.source_warehouse_id)
        if str(peer_fetch['requested_url']).rstrip('/') != expected_peer_url.rstrip('/'):
            raise RuntimeError('Saved peer response URL does not match the page warehouse ID')
        peer_parse_id, peer_parse_status = get_or_create_parse_artifact(conn, peer_blob_id, PEER_PARSER_NAME, PEER_PARSER_VERSION, MANIFEST_VERSION)
        if peer_parse_status == 'pending':
            parsed_peers = parse_peer_table(Path(peer_blob['local_path']).read_bytes(), parsed.source_company_id)
            store_parsed_peer_table(conn, peer_parse_id, parsed_peers, scope, str(peer_blob['first_observed_at_utc']))
            validate_peer_parse(conn, peer_parse_id, parsed.source_company_id, expected_symbol)
        elif peer_parse_status != 'ok':
            raise RuntimeError('Saved peer blob has a failed current-version parse')
        link_supplemental_parse(conn, parse_id, 'peer_table', peer_parse_id)
        artifact = conn.execute('SELECT validation_status FROM parse_artifacts WHERE parse_id=?', (parse_id,)).fetchone()
        if artifact['validation_status'] == 'pending':
            validate_parse(conn, parse_id, expected_symbol, scope)
        quarter_periods = {item.period_end[:7] for item in parsed.financials if item.section_code == 'quarters' and item.period_end}
        resolved_period = max(quarter_periods) if quarter_periods else 'not_applicable'
        accept_parse(conn, company_id, parse_id, 'screener', scope, resolved_period)
        enqueue_documents_for_accepted_parse(conn, parse_id=parse_id, resolved_result_period=resolved_period)
        export_path = write_export(conn, run_id, company_id, parse_id, expected_symbol, export_root)
        finish_run(conn, run_id, 'ok')
        return VerticalSliceResult(run_id, int(main_fetch['fetch_id']), blob_id, parse_id, int(peer_fetch['fetch_id']), peer_blob_id, peer_parse_id, company_id, export_path, reused_parse)
    except Exception:
        finish_run(conn, run_id, 'error')
        raise
    finally:
        conn.close()
