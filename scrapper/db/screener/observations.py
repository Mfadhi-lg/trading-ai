"""Screener source, blob and parsed-observation persistence.

The compatible db.repo facade supplies its clock and section hook. SQL,
SQLite transaction boundaries and immutable response storage retain their contracts.
PostgreSQL methods participate in the caller-owned managed transaction; nested
observation writes use a savepoint and never commit or close the native session.
Financial acceptance and workflow sequencing belong to their existing owners.
"""
from __future__ import annotations
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from ...config import SNAPSHOTS
from ...core.http import FetchResponse
from ...sources.screener.company import ParsedCompany
from ...sources.screener.peers import ParsedPeerTable
from ..repository_backend import (
    commit, execute, executemany, generated_id, is_postgresql, require_transaction, statement, transaction,
)

def ensure_source_identifier(conn: sqlite3.Connection, company_id: int, parsed: ParsedCompany, canonical_url: str, *, utc_now) -> None:
    if not parsed.source_company_id:
        raise ValueError('Screener source company ID is missing')
    path_parts = [part for part in urlsplit(canonical_url).path.split('/') if part]
    source_slug = path_parts[1].lower() if len(path_parts) >= 2 else None
    execute(conn, "INSERT INTO source_identifiers\n           (company_id, source, source_company_id, source_symbol, source_slug,\n            canonical_url, mapping_status, verified_at_utc)\n           VALUES (?, 'screener', ?, ?, ?, ?, 'verified', ?)\n           ON CONFLICT(company_id, source) DO UPDATE SET\n             source_company_id=excluded.source_company_id,\n             source_symbol=excluded.source_symbol,\n             source_slug=excluded.source_slug,\n             canonical_url=excluded.canonical_url,\n             mapping_status='verified',\n             verified_at_utc=excluded.verified_at_utc", (company_id, parsed.source_company_id, parsed.nse_symbol or parsed.bse_code or None, source_slug, canonical_url, utc_now()))

def store_blob(conn: sqlite3.Connection, response: FetchResponse, symbol: str, scope: str, observed_at_utc: str, snapshot_root: Path=SNAPSHOTS, resource_kind: str='company_page', *, utc_now) -> int:
    require_transaction(conn)
    sha256 = hashlib.sha256(response.content).hexdigest()
    resource_path = () if resource_kind == 'company_page' else (resource_kind,)
    destination = snapshot_root / 'screener' / symbol.upper() / scope / Path(*resource_path) / f'{sha256}.html'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        # Independent workers may save the same content at the same instant.
        # A shared temporary name lets one worker replace another's file.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent,
                    prefix=destination.name + '.', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(response.content)
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    execute(conn, statement(conn, 'INSERT OR IGNORE INTO response_blobs\n           (content_sha256, content_type, encoding, byte_size, local_path,\n            first_observed_at_utc, created_at_utc)\n           VALUES (?, ?, ?, ?, ?, ?, ?)', 'INSERT INTO response_blobs\n           (content_sha256, content_type, encoding, byte_size, local_path,\n            first_observed_at_utc, created_at_utc)\n           VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(content_sha256) DO NOTHING'), (sha256, response.content_type, response.encoding, len(response.content), str(destination.resolve()), observed_at_utc, utc_now()))
    row = execute(conn, 'SELECT blob_id FROM response_blobs WHERE content_sha256=?', (sha256,)).fetchone()
    if row is None:
        raise RuntimeError('Failed to persist response blob')
    return int(row['blob_id'])

def record_fetch(conn: sqlite3.Connection, run_id: int, company_id: int | None, response: FetchResponse, scope: str, fetched_at_utc: str, blob_id: int | None, requested_source_period: str='not_applicable', resolved_source_period: str='not_applicable', resource_kind: str='company_page') -> int:
    cursor = execute(conn, "INSERT INTO fetch_observations\n           (run_id, company_id, source, reporting_scope,\n            requested_source_period, resolved_source_period,\n            requested_url, final_url, fetched_at_utc, http_status, etag,\n            last_modified, auth_mode, blob_id, fetch_status, resource_kind)\n           VALUES (?, ?, 'screener', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (run_id, company_id, scope, requested_source_period, resolved_source_period, response.requested_url, response.final_url, fetched_at_utc, response.status_code, response.etag, response.last_modified, response.auth_mode, blob_id, response.fetch_status, resource_kind), returning='fetch_id')
    commit(conn)
    return generated_id(cursor, 'fetch_id')

def get_or_create_parse_artifact(conn: sqlite3.Connection, blob_id: int, parser_name: str, parser_version: str, manifest_version: str, *, utc_now) -> tuple[int, str]:
    require_transaction(conn)
    if is_postgresql(conn):
        # The lock covers this lookup and insertion until the caller commits.
        # JSON avoids ambiguous concatenated identity components.
        import json
        key = json.dumps([blob_id, parser_name, parser_version, manifest_version])
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s, 0))", ("parse-cache:" + key,))
    row = execute(conn, 'SELECT parse_id, parse_status FROM parse_artifacts\n           WHERE blob_id=? AND parser_name=? AND parser_version=? AND manifest_version=?', (blob_id, parser_name, parser_version, manifest_version)).fetchone()
    if row:
        return (int(row['parse_id']), str(row['parse_status']))
    cursor = execute(conn, "INSERT INTO parse_artifacts\n           (blob_id, parser_name, parser_version, manifest_version, parsed_at_utc,\n            parse_status, validation_status)\n           VALUES (?, ?, ?, ?, ?, 'pending', 'pending')", (blob_id, parser_name, parser_version, manifest_version, utc_now()), returning='parse_id')
    commit(conn)
    return (generated_id(cursor, 'parse_id'), 'pending')

def _insert_section(conn: sqlite3.Connection, parse_id: int, section_code: str, heading: str, position: int) -> int:
    cursor = execute(conn, "INSERT INTO source_sections\n           (parse_id, section_code, heading_raw, position, parse_status)\n           VALUES (?, ?, ?, ?, 'ok')", (parse_id, section_code, heading, position), returning='section_id')
    return generated_id(cursor, 'section_id')

def link_supplemental_parse(conn: sqlite3.Connection, parent_parse_id: int, component_code: str, supplemental_parse_id: int) -> None:
    execute(conn, 'INSERT INTO supplemental_parse_links\n           (parent_parse_id, component_code, supplemental_parse_id)\n           VALUES (?, ?, ?)\n           ON CONFLICT(parent_parse_id, component_code) DO UPDATE SET\n             supplemental_parse_id=excluded.supplemental_parse_id', (parent_parse_id, component_code, supplemental_parse_id))
    commit(conn)

def store_parsed_peer_table(conn: sqlite3.Connection, parse_id: int, parsed: ParsedPeerTable, reporting_scope: str, first_observed_at_utc: str, *, _insert_section) -> None:
    artifact = execute(conn, statement(conn, 'SELECT parse_status FROM parse_artifacts WHERE parse_id=?', 'SELECT parse_status FROM parse_artifacts WHERE parse_id=? FOR UPDATE'), (parse_id,)).fetchone()
    if artifact is None or artifact['parse_status'] != 'pending':
        raise ValueError('Only a pending peer parse artifact can receive observations')
    try:
        with transaction(conn):
            section_id = _insert_section(conn, parse_id, 'peer_table', parsed.heading, 4)
            executemany(conn, "INSERT INTO source_table_cells\n                   (section_id, table_position, row_position, column_position,\n                    row_label_raw, column_label_raw, raw_text,\n                    normalized_display_text, link_url, cell_role,\n                    visibility_state, rowspan, colspan)\n                   VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?)", [(section_id, cell.row, cell.column, cell.row_label, cell.column_label, cell.raw_text, cell.display_text, cell.link_url, cell.role, cell.rowspan, cell.colspan) for cell in parsed.cells])
            executemany(conn, "INSERT INTO peer_observations\n                   (parse_id, reporting_scope, row_kind,\n                    peer_source_company_id, peer_name_raw, peer_source_url,\n                    is_subject_company, row_position, column_position,\n                    metric_code, metric_label_raw, display_value, value_decimal,\n                    unit_code, scale_multiplier, missing_kind,\n                    first_observed_at_utc, source_published_at_utc,\n                    available_at_utc, availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,\n                           NULL, ?, 'first_observed')", [(parse_id, reporting_scope, obs.row_kind, obs.peer_source_company_id, obs.peer_name_raw, obs.peer_source_url, 1 if obs.is_subject_company else 0, obs.row, obs.column, obs.metric_code, obs.metric_label_raw, obs.display_value, obs.value_decimal, obs.unit_code, obs.scale_multiplier, obs.missing_kind, first_observed_at_utc, first_observed_at_utc) for obs in parsed.observations])
            execute(conn, "UPDATE parse_artifacts SET parse_status='ok' WHERE parse_id=?", (parse_id,))
    except Exception:
        execute(conn, "UPDATE parse_artifacts\n               SET parse_status='failed', validation_status='failed'\n               WHERE parse_id=?", (parse_id,))
        commit(conn)
        raise

def store_parsed_company(conn: sqlite3.Connection, parse_id: int, parsed: ParsedCompany, first_observed_at_utc: str, *, _insert_section) -> None:
    artifact = execute(conn, statement(conn, 'SELECT parse_status FROM parse_artifacts WHERE parse_id=?', 'SELECT parse_status FROM parse_artifacts WHERE parse_id=? FOR UPDATE'), (parse_id,)).fetchone()
    if artifact is None or artifact['parse_status'] != 'pending':
        raise ValueError('Only a pending parse artifact can receive observations')
    try:
        with transaction(conn):
            identity_section = _insert_section(conn, parse_id, 'identity', 'Identity and quote', 0)
            profile_section = _insert_section(conn, parse_id, 'company_profile', parsed.profile_heading, 1)
            ratio_section = _insert_section(conn, parse_id, 'ratio_panel', 'Ratio panel', 2)
            analysis_section = _insert_section(conn, parse_id, 'pros_cons', parsed.analysis_heading, 3)
            quarter_section = _insert_section(conn, parse_id, 'quarterly_results', parsed.quarter_heading, 5)
            pnl_section = _insert_section(conn, parse_id, 'profit_loss', parsed.pnl_heading, 6)
            balance_section = _insert_section(conn, parse_id, 'balance_sheet', parsed.balance_heading, 7)
            cash_section = _insert_section(conn, parse_id, 'cash_flow', parsed.cash_heading, 8)
            ratios_section = _insert_section(conn, parse_id, 'ratios', parsed.ratios_heading, 9)
            growth_section = _insert_section(conn, parse_id, 'growth', parsed.growth_heading, 10)
            shareholding_section = _insert_section(conn, parse_id, 'shareholding_quarterly', parsed.shareholding_heading, 11)
            shareholding_yearly_section = _insert_section(conn, parse_id, 'shareholding_yearly', parsed.shareholding_heading, 12)
            documents_section = _insert_section(conn, parse_id, 'document_links', parsed.documents_heading, 13)
            for section_id, fields in ((identity_section, parsed.identity_fields), (profile_section, parsed.profile_fields), (ratio_section, parsed.ratio_fields), (analysis_section, parsed.analysis_fields), (documents_section, parsed.document_fields)):
                executemany(conn, 'INSERT INTO source_fields\n                       (section_id, field_position, field_key_raw, raw_text,\n                        normalized_display_text, link_url, field_role, visibility_state)\n                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [(section_id, item.position, item.key_raw, item.raw_text, item.display_text, item.link_url, item.role, item.visibility_state) for item in fields])
            for frequency in parsed.shareholding_unavailable_frequencies:
                section_id = shareholding_section if frequency == 'quarterly' else shareholding_yearly_section
                notice = 'Shareholding pattern is currently not available for this company.'
                execute(conn, "INSERT INTO source_fields\n                       (section_id, field_position, field_key_raw, raw_text,\n                        normalized_display_text, link_url, field_role, visibility_state)\n                       VALUES (?, 1, 'Availability', ?, ?, NULL, 'source_unavailable', 'visible')", (section_id, notice, notice))
            if parsed.shareholding_classification_warning is not None:
                execute(conn, "INSERT INTO source_fields\n                       (section_id, field_position, field_key_raw, raw_text,\n                        normalized_display_text, link_url, field_role, visibility_state)\n                       VALUES (?, 0, 'Classification Warning', ?, ?, NULL,\n                               'classification_warning', 'visible')", (shareholding_section, parsed.shareholding_classification_warning, parsed.shareholding_classification_warning))
            for section_id, table_cells in ((quarter_section, parsed.quarter_cells), (pnl_section, parsed.pnl_cells), (balance_section, parsed.balance_cells), (cash_section, parsed.cash_cells), (ratios_section, parsed.ratios_cells), (shareholding_section, parsed.shareholding_q_cells), (shareholding_yearly_section, parsed.shareholding_y_cells)):
                executemany(conn, "INSERT INTO source_table_cells\n                       (section_id, table_position, row_position, column_position,\n                        row_label_raw, column_label_raw, raw_text,\n                        normalized_display_text, link_url, cell_role,\n                        visibility_state, rowspan, colspan)\n                       VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?)", [(section_id, cell.row, cell.column, cell.row_label, cell.column_label, cell.raw_text, cell.display_text, cell.link_url, cell.role, cell.rowspan, cell.colspan) for cell in table_cells])
            executemany(conn, "INSERT INTO source_table_cells\n                   (section_id, table_position, row_position, column_position,\n                    row_label_raw, column_label_raw, raw_text,\n                    normalized_display_text, link_url, cell_role,\n                    visibility_state, rowspan, colspan)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?)", [(growth_section, cell.table, cell.row, cell.column, cell.row_label, cell.column_label, cell.raw_text, cell.display_text, cell.link_url, cell.role, cell.rowspan, cell.colspan) for cell in parsed.growth_cells])
            quote = parsed.quote
            execute(conn, 'INSERT INTO quote_observations\n                   (parse_id, reporting_scope, price_display_value,\n                    price_decimal, currency,\n                    quote_timestamp_raw, quote_timestamp_utc, quote_timezone,\n                    quote_timestamp_status, first_observed_at_utc)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (parse_id, parsed.reporting_scope, quote.display_price, quote.price_decimal, quote.currency, quote.timestamp_raw, quote.timestamp_utc, quote.timezone, quote.timestamp_status, first_observed_at_utc))
            executemany(conn, "INSERT INTO company_metric_observations\n                   (parse_id, reporting_scope, metric_code, label_raw, position,\n                    display_value, value_decimal, value_json, unit_code,\n                    scale_multiplier, missing_kind, first_observed_at_utc,\n                    source_published_at_utc, available_at_utc,\n                    availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,\n                           'first_observed')", [(parse_id, parsed.reporting_scope, metric.metric_code, metric.label_raw, metric.position, metric.display_value, metric.value_decimal, metric.value_json, metric.unit_code, metric.scale_multiplier, metric.missing_kind, first_observed_at_utc, first_observed_at_utc) for metric in parsed.metrics])
            executemany(conn, "INSERT INTO financial_observations\n                   (parse_id, reporting_scope, section_code, line_item_code,\n                    line_item_raw, row_position, period_label_raw, period_end,\n                    period_type, fiscal_year, column_position,\n                    first_observed_at_utc, source_published_at_utc, available_at_utc,\n                    availability_basis, display_value, value_decimal, unit_code,\n                    scale_multiplier, missing_kind, is_expandable, schedule_key)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,\n                           ?, NULL, ?, 'first_observed', ?, ?, ?, ?, ?, ?, ?)", [(parse_id, parsed.reporting_scope, obs.section_code, obs.line_item_code, obs.line_item_raw, obs.row, obs.period_label_raw, obs.period_end, obs.period_type, obs.fiscal_year, obs.column, first_observed_at_utc, first_observed_at_utc, obs.display_value, obs.value_decimal, obs.unit_code, obs.scale_multiplier, obs.missing_kind, 1 if obs.is_expandable else 0, obs.schedule_key) for obs in (*parsed.financials, *parsed.pnl_financials, *parsed.balance_financials, *parsed.cash_financials, *parsed.ratios_financials)])
            executemany(conn, "INSERT INTO growth_observations\n                   (parse_id, reporting_scope, metric_code, metric_label_raw,\n                    block_position, row_position, period_code, period_label_raw,\n                    display_value, value_decimal, unit_code, missing_kind,\n                    first_observed_at_utc, source_published_at_utc,\n                    available_at_utc, availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,\n                           'first_observed')", [(parse_id, parsed.reporting_scope, obs.metric_code, obs.metric_label_raw, obs.block, obs.row, obs.period_code, obs.period_label_raw, obs.display_value, obs.value_decimal, obs.unit_code, obs.missing_kind, first_observed_at_utc, first_observed_at_utc) for obs in parsed.growth_observations])
            executemany(conn, "INSERT INTO shareholding_observations\n                   (parse_id, reporting_scope, frequency, holder_code,\n                    holder_label_raw, row_position, column_position,\n                    period_label_raw, period_end, display_value, value_decimal,\n                    unit_code, classification_regime, missing_kind,\n                    first_observed_at_utc, source_published_at_utc,\n                    available_at_utc, availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,\n                           'first_observed')", [(parse_id, parsed.reporting_scope, obs.frequency, obs.holder_code, obs.holder_label_raw, obs.row, obs.column, obs.period_label_raw, obs.period_end, obs.display_value, obs.value_decimal, obs.unit_code, obs.classification_regime, obs.missing_kind, first_observed_at_utc, first_observed_at_utc) for obs in (*parsed.shareholding_q_observations, *parsed.shareholding_y_observations)])
            executemany(conn, "INSERT INTO company_profile_links\n                   (parse_id, reporting_scope, link_type, label_raw,\n                    identifier_value, position, source_url,\n                    first_observed_at_utc, source_published_at_utc,\n                    available_at_utc, availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'first_observed')", [(parse_id, parsed.reporting_scope, link.link_type, link.label_raw, link.identifier_value, link.position, link.source_url, first_observed_at_utc, first_observed_at_utc) for link in parsed.profile_links])
            executemany(conn, "INSERT INTO company_text_observations\n                   (parse_id, reporting_scope, text_type, heading_raw, position,\n                    display_text, content_origin, is_source_generated,\n                    first_observed_at_utc, source_published_at_utc,\n                    available_at_utc, availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'first_observed')", [(parse_id, parsed.reporting_scope, text.text_type, text.heading_raw, text.position, text.display_text, text.content_origin, 1 if text.is_source_generated else 0, first_observed_at_utc, first_observed_at_utc) for text in (*parsed.profile_texts, *parsed.analysis_texts, *parsed.commentary_texts)])
            executemany(conn, "INSERT INTO document_links\n                   (parse_id, reporting_scope, document_type, period_label_raw,\n                    title_raw, position, source_url, publisher,\n                    availability_status, first_observed_at_utc,\n                    source_published_at_utc, available_at_utc,\n                    availability_basis)\n                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,\n                           'first_observed')", [(parse_id, parsed.reporting_scope, link.document_type, link.period_label_raw, link.title_raw, link.position, link.source_url, link.publisher, link.availability_status, first_observed_at_utc, first_observed_at_utc) for link in parsed.document_links])
            execute(conn, "UPDATE parse_artifacts SET parse_status='ok' WHERE parse_id=?", (parse_id,))
    except Exception:
        execute(conn, "UPDATE parse_artifacts SET parse_status='failed', validation_status='failed' WHERE parse_id=?", (parse_id,))
        commit(conn)
        raise
