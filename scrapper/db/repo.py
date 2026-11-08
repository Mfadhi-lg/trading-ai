"""Persistence for one end-to-end Screener company-page vertical slice."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..config import SNAPSHOTS
from ..core.http import FetchResponse
from ..sources.screener.company import ParsedCompany
from ..sources.screener.peers import ParsedPeerTable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(conn: sqlite3.Connection, code_version: str, config: dict) -> int:
    cursor = conn.execute(
        """INSERT INTO scrape_runs
           (started_at_utc, code_version, config_json, status)
           VALUES (?, ?, ?, 'running')""",
        (utc_now(), code_version, json.dumps(config, sort_keys=True)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    conn.execute(
        "UPDATE scrape_runs SET completed_at_utc=?, status=? WHERE run_id=?",
        (utc_now(), status, run_id),
    )
    conn.commit()


def ensure_company(conn: sqlite3.Connection, parsed: ParsedCompany) -> int:
    matches: set[int] = set()
    if parsed.source_company_id:
        row = conn.execute(
            """SELECT company_id FROM source_identifiers
               WHERE source='screener' AND source_company_id=?""",
            (parsed.source_company_id,),
        ).fetchone()
        if row:
            matches.add(int(row["company_id"]))
    for identifier_type, identifier_value in (
        ("NSE", parsed.nse_symbol),
        ("BSE", parsed.bse_code),
    ):
        if not identifier_value:
            continue
        row = conn.execute(
            """SELECT company_id FROM security_identifiers
               WHERE identifier_type=? AND identifier_value=? AND is_current=1""",
            (identifier_type, identifier_value),
        ).fetchone()
        if row:
            matches.add(int(row["company_id"]))
    if len(matches) > 1:
        raise ValueError("Conflicting verified Screener/NSE/BSE company identities")
    if matches:
        company_id = matches.pop()
        conn.execute(
            "UPDATE companies SET current_legal_name=? WHERE company_id=?",
            (parsed.company_name, company_id),
        )
    else:
        cursor = conn.execute(
            "INSERT INTO companies(current_legal_name, status) VALUES (?, 'active')",
            (parsed.company_name,),
        )
        company_id = int(cursor.lastrowid)
    for identifier_type, identifier_value in (
        ("NSE", parsed.nse_symbol),
        ("BSE", parsed.bse_code),
    ):
        if identifier_value:
            conn.execute(
                """INSERT OR IGNORE INTO security_identifiers
                   (company_id, identifier_type, identifier_value, is_current)
                   VALUES (?, ?, ?, 1)""",
                (company_id, identifier_type, identifier_value),
            )
    return company_id


def ensure_source_identifier(
    conn: sqlite3.Connection,
    company_id: int,
    parsed: ParsedCompany,
    canonical_url: str,
) -> None:
    if not parsed.source_company_id:
        raise ValueError("Screener source company ID is missing")
    path_parts = [part for part in urlsplit(canonical_url).path.split("/") if part]
    source_slug = path_parts[1].lower() if len(path_parts) >= 2 else None
    conn.execute(
        """INSERT INTO source_identifiers
           (company_id, source, source_company_id, source_symbol, source_slug,
            canonical_url, mapping_status, verified_at_utc)
           VALUES (?, 'screener', ?, ?, ?, ?, 'verified', ?)
           ON CONFLICT(company_id, source) DO UPDATE SET
             source_company_id=excluded.source_company_id,
             source_symbol=excluded.source_symbol,
             source_slug=excluded.source_slug,
             canonical_url=excluded.canonical_url,
             mapping_status='verified',
             verified_at_utc=excluded.verified_at_utc""",
        (
            company_id,
            parsed.source_company_id,
            parsed.nse_symbol or parsed.bse_code or None,
            source_slug,
            canonical_url,
            utc_now(),
        ),
    )


def store_blob(
    conn: sqlite3.Connection,
    response: FetchResponse,
    symbol: str,
    scope: str,
    observed_at_utc: str,
    snapshot_root: Path = SNAPSHOTS,
    resource_kind: str = "company_page",
) -> int:
    sha256 = hashlib.sha256(response.content).hexdigest()
    resource_path = () if resource_kind == "company_page" else (resource_kind,)
    destination = (
        snapshot_root
        / "screener"
        / symbol.upper()
        / scope
        / Path(*resource_path)
        / f"{sha256}.html"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_suffix(".html.tmp")
        temporary.write_bytes(response.content)
        os.replace(temporary, destination)
    conn.execute(
        """INSERT OR IGNORE INTO response_blobs
           (content_sha256, content_type, encoding, byte_size, local_path,
            first_observed_at_utc, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            sha256,
            response.content_type,
            response.encoding,
            len(response.content),
            str(destination.resolve()),
            observed_at_utc,
            utc_now(),
        ),
    )
    row = conn.execute(
        "SELECT blob_id FROM response_blobs WHERE content_sha256=?", (sha256,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to persist response blob")
    return int(row["blob_id"])


def record_fetch(
    conn: sqlite3.Connection,
    run_id: int,
    company_id: int | None,
    response: FetchResponse,
    scope: str,
    fetched_at_utc: str,
    blob_id: int | None,
    requested_source_period: str = "not_applicable",
    resolved_source_period: str = "not_applicable",
    resource_kind: str = "company_page",
) -> int:
    cursor = conn.execute(
        """INSERT INTO fetch_observations
           (run_id, company_id, source, reporting_scope,
            requested_source_period, resolved_source_period,
            requested_url, final_url, fetched_at_utc, http_status, etag,
            last_modified, auth_mode, blob_id, fetch_status, resource_kind)
           VALUES (?, ?, 'screener', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            company_id,
            scope,
            requested_source_period,
            resolved_source_period,
            response.requested_url,
            response.final_url,
            fetched_at_utc,
            response.status_code,
            response.etag,
            response.last_modified,
            response.auth_mode,
            blob_id,
            response.fetch_status,
            resource_kind,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_or_create_parse_artifact(
    conn: sqlite3.Connection,
    blob_id: int,
    parser_name: str,
    parser_version: str,
    manifest_version: str,
) -> tuple[int, str]:
    row = conn.execute(
        """SELECT parse_id, parse_status FROM parse_artifacts
           WHERE blob_id=? AND parser_name=? AND parser_version=? AND manifest_version=?""",
        (blob_id, parser_name, parser_version, manifest_version),
    ).fetchone()
    if row:
        return int(row["parse_id"]), str(row["parse_status"])
    cursor = conn.execute(
        """INSERT INTO parse_artifacts
           (blob_id, parser_name, parser_version, manifest_version, parsed_at_utc,
            parse_status, validation_status)
           VALUES (?, ?, ?, ?, ?, 'pending', 'pending')""",
        (blob_id, parser_name, parser_version, manifest_version, utc_now()),
    )
    conn.commit()
    return int(cursor.lastrowid), "pending"


def _insert_section(
    conn: sqlite3.Connection,
    parse_id: int,
    section_code: str,
    heading: str,
    position: int,
) -> int:
    cursor = conn.execute(
        """INSERT INTO source_sections
           (parse_id, section_code, heading_raw, position, parse_status)
           VALUES (?, ?, ?, ?, 'ok')""",
        (parse_id, section_code, heading, position),
    )
    return int(cursor.lastrowid)


def link_supplemental_parse(
    conn: sqlite3.Connection,
    parent_parse_id: int,
    component_code: str,
    supplemental_parse_id: int,
) -> None:
    conn.execute(
        """INSERT INTO supplemental_parse_links
           (parent_parse_id, component_code, supplemental_parse_id)
           VALUES (?, ?, ?)
           ON CONFLICT(parent_parse_id, component_code) DO UPDATE SET
             supplemental_parse_id=excluded.supplemental_parse_id""",
        (parent_parse_id, component_code, supplemental_parse_id),
    )
    conn.commit()


def store_parsed_peer_table(
    conn: sqlite3.Connection,
    parse_id: int,
    parsed: ParsedPeerTable,
    reporting_scope: str,
    first_observed_at_utc: str,
) -> None:
    artifact = conn.execute(
        "SELECT parse_status FROM parse_artifacts WHERE parse_id=?", (parse_id,)
    ).fetchone()
    if artifact is None or artifact["parse_status"] != "pending":
        raise ValueError("Only a pending peer parse artifact can receive observations")
    try:
        with conn:
            section_id = _insert_section(
                conn, parse_id, "peer_table", parsed.heading, 4
            )
            conn.executemany(
                """INSERT INTO source_table_cells
                   (section_id, table_position, row_position, column_position,
                    row_label_raw, column_label_raw, raw_text,
                    normalized_display_text, link_url, cell_role,
                    visibility_state, rowspan, colspan)
                   VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?)""",
                [
                    (
                        section_id,
                        cell.row,
                        cell.column,
                        cell.row_label,
                        cell.column_label,
                        cell.raw_text,
                        cell.display_text,
                        cell.link_url,
                        cell.role,
                        cell.rowspan,
                        cell.colspan,
                    )
                    for cell in parsed.cells
                ],
            )
            conn.executemany(
                """INSERT INTO peer_observations
                   (parse_id, reporting_scope, row_kind,
                    peer_source_company_id, peer_name_raw, peer_source_url,
                    is_subject_company, row_position, column_position,
                    metric_code, metric_label_raw, display_value, value_decimal,
                    unit_code, scale_multiplier, missing_kind,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           NULL, ?, 'first_observed')""",
                [
                    (
                        parse_id,
                        reporting_scope,
                        obs.row_kind,
                        obs.peer_source_company_id,
                        obs.peer_name_raw,
                        obs.peer_source_url,
                        1 if obs.is_subject_company else 0,
                        obs.row,
                        obs.column,
                        obs.metric_code,
                        obs.metric_label_raw,
                        obs.display_value,
                        obs.value_decimal,
                        obs.unit_code,
                        obs.scale_multiplier,
                        obs.missing_kind,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for obs in parsed.observations
                ],
            )
            conn.execute(
                "UPDATE parse_artifacts SET parse_status='ok' WHERE parse_id=?",
                (parse_id,),
            )
    except Exception:
        conn.execute(
            """UPDATE parse_artifacts
               SET parse_status='failed', validation_status='failed'
               WHERE parse_id=?""",
            (parse_id,),
        )
        conn.commit()
        raise


def store_parsed_company(
    conn: sqlite3.Connection,
    parse_id: int,
    parsed: ParsedCompany,
    first_observed_at_utc: str,
) -> None:
    artifact = conn.execute(
        "SELECT parse_status FROM parse_artifacts WHERE parse_id=?", (parse_id,)
    ).fetchone()
    if artifact is None or artifact["parse_status"] != "pending":
        raise ValueError("Only a pending parse artifact can receive observations")

    try:
        with conn:
            identity_section = _insert_section(conn, parse_id, "identity", "Identity and quote", 0)
            profile_section = _insert_section(
                conn, parse_id, "company_profile", parsed.profile_heading, 1
            )
            ratio_section = _insert_section(conn, parse_id, "ratio_panel", "Ratio panel", 2)
            analysis_section = _insert_section(
                conn, parse_id, "pros_cons", parsed.analysis_heading, 3
            )
            quarter_section = _insert_section(
                conn, parse_id, "quarterly_results", parsed.quarter_heading, 5
            )
            pnl_section = _insert_section(
                conn, parse_id, "profit_loss", parsed.pnl_heading, 6
            )
            balance_section = _insert_section(
                conn, parse_id, "balance_sheet", parsed.balance_heading, 7
            )
            cash_section = _insert_section(
                conn, parse_id, "cash_flow", parsed.cash_heading, 8
            )
            ratios_section = _insert_section(
                conn, parse_id, "ratios", parsed.ratios_heading, 9
            )
            growth_section = _insert_section(
                conn, parse_id, "growth", parsed.growth_heading, 10
            )
            shareholding_section = _insert_section(
                conn, parse_id, "shareholding_quarterly", parsed.shareholding_heading, 11
            )
            shareholding_yearly_section = _insert_section(
                conn, parse_id, "shareholding_yearly", parsed.shareholding_heading, 12
            )
            documents_section = _insert_section(
                conn, parse_id, "document_links", parsed.documents_heading, 13
            )

            for section_id, fields in (
                (identity_section, parsed.identity_fields),
                (profile_section, parsed.profile_fields),
                (ratio_section, parsed.ratio_fields),
                (analysis_section, parsed.analysis_fields),
                (documents_section, parsed.document_fields),
            ):
                conn.executemany(
                    """INSERT INTO source_fields
                       (section_id, field_position, field_key_raw, raw_text,
                        normalized_display_text, link_url, field_role, visibility_state)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            section_id,
                            item.position,
                            item.key_raw,
                            item.raw_text,
                            item.display_text,
                            item.link_url,
                            item.role,
                            item.visibility_state,
                        )
                        for item in fields
                    ],
                )

            for frequency in parsed.shareholding_unavailable_frequencies:
                section_id = shareholding_section if frequency == "quarterly" else shareholding_yearly_section
                notice = "Shareholding pattern is currently not available for this company."
                conn.execute(
                    """INSERT INTO source_fields
                       (section_id, field_position, field_key_raw, raw_text,
                        normalized_display_text, link_url, field_role, visibility_state)
                       VALUES (?, 1, 'Availability', ?, ?, NULL, 'source_unavailable', 'visible')""",
                    (section_id, notice, notice),
                )

            # The source classification warning is stored with the shareholding
            # data (not merely logged) as a source-faithful field.
            if parsed.shareholding_classification_warning is not None:
                conn.execute(
                    """INSERT INTO source_fields
                       (section_id, field_position, field_key_raw, raw_text,
                        normalized_display_text, link_url, field_role, visibility_state)
                       VALUES (?, 0, 'Classification Warning', ?, ?, NULL,
                               'classification_warning', 'visible')""",
                    (
                        shareholding_section,
                        parsed.shareholding_classification_warning,
                        parsed.shareholding_classification_warning,
                    ),
                )

            for section_id, table_cells in (
                (quarter_section, parsed.quarter_cells),
                (pnl_section, parsed.pnl_cells),
                (balance_section, parsed.balance_cells),
                (cash_section, parsed.cash_cells),
                (ratios_section, parsed.ratios_cells),
                (shareholding_section, parsed.shareholding_q_cells),
                (shareholding_yearly_section, parsed.shareholding_y_cells),
            ):
                conn.executemany(
                    """INSERT INTO source_table_cells
                       (section_id, table_position, row_position, column_position,
                        row_label_raw, column_label_raw, raw_text,
                        normalized_display_text, link_url, cell_role,
                        visibility_state, rowspan, colspan)
                       VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?)""",
                    [
                        (
                            section_id,
                            cell.row,
                            cell.column,
                            cell.row_label,
                            cell.column_label,
                            cell.raw_text,
                            cell.display_text,
                            cell.link_url,
                            cell.role,
                            cell.rowspan,
                            cell.colspan,
                        )
                        for cell in table_cells
                    ],
                )

            # Growth mini-tables share one section but keep distinct table
            # positions, so each of the four blocks stays addressable.
            conn.executemany(
                """INSERT INTO source_table_cells
                   (section_id, table_position, row_position, column_position,
                    row_label_raw, column_label_raw, raw_text,
                    normalized_display_text, link_url, cell_role,
                    visibility_state, rowspan, colspan)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?)""",
                [
                    (
                        growth_section,
                        cell.table,
                        cell.row,
                        cell.column,
                        cell.row_label,
                        cell.column_label,
                        cell.raw_text,
                        cell.display_text,
                        cell.link_url,
                        cell.role,
                        cell.rowspan,
                        cell.colspan,
                    )
                    for cell in parsed.growth_cells
                ],
            )

            quote = parsed.quote
            conn.execute(
                """INSERT INTO quote_observations
                   (parse_id, reporting_scope, price_display_value,
                    price_decimal, currency,
                    quote_timestamp_raw, quote_timestamp_utc, quote_timezone,
                    quote_timestamp_status, first_observed_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parse_id,
                    parsed.reporting_scope,
                    quote.display_price,
                    quote.price_decimal,
                    quote.currency,
                    quote.timestamp_raw,
                    quote.timestamp_utc,
                    quote.timezone,
                    quote.timestamp_status,
                    first_observed_at_utc,
                ),
            )
            conn.executemany(
                """INSERT INTO company_metric_observations
                   (parse_id, reporting_scope, metric_code, label_raw, position,
                    display_value, value_decimal, value_json, unit_code,
                    scale_multiplier, missing_kind, first_observed_at_utc,
                    source_published_at_utc, available_at_utc,
                    availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,
                           'first_observed')""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        metric.metric_code,
                        metric.label_raw,
                        metric.position,
                        metric.display_value,
                        metric.value_decimal,
                        metric.value_json,
                        metric.unit_code,
                        metric.scale_multiplier,
                        metric.missing_kind,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for metric in parsed.metrics
                ],
            )
            conn.executemany(
                """INSERT INTO financial_observations
                   (parse_id, reporting_scope, section_code, line_item_code,
                    line_item_raw, row_position, period_label_raw, period_end,
                    period_type, fiscal_year, column_position,
                    first_observed_at_utc, source_published_at_utc, available_at_utc,
                    availability_basis, display_value, value_decimal, unit_code,
                    scale_multiplier, missing_kind, is_expandable, schedule_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, NULL, ?, 'first_observed', ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        obs.section_code,
                        obs.line_item_code,
                        obs.line_item_raw,
                        obs.row,
                        obs.period_label_raw,
                        obs.period_end,
                        obs.period_type,
                        obs.fiscal_year,
                        obs.column,
                        first_observed_at_utc,
                        first_observed_at_utc,
                        obs.display_value,
                        obs.value_decimal,
                        obs.unit_code,
                        obs.scale_multiplier,
                        obs.missing_kind,
                        1 if obs.is_expandable else 0,
                        obs.schedule_key,
                    )
                    for obs in (
                        *parsed.financials,
                        *parsed.pnl_financials,
                        *parsed.balance_financials,
                        *parsed.cash_financials,
                        *parsed.ratios_financials,
                    )
                ],
            )
            conn.executemany(
                """INSERT INTO growth_observations
                   (parse_id, reporting_scope, metric_code, metric_label_raw,
                    block_position, row_position, period_code, period_label_raw,
                    display_value, value_decimal, unit_code, missing_kind,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,
                           'first_observed')""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        obs.metric_code,
                        obs.metric_label_raw,
                        obs.block,
                        obs.row,
                        obs.period_code,
                        obs.period_label_raw,
                        obs.display_value,
                        obs.value_decimal,
                        obs.unit_code,
                        obs.missing_kind,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for obs in parsed.growth_observations
                ],
            )
            conn.executemany(
                """INSERT INTO shareholding_observations
                   (parse_id, reporting_scope, frequency, holder_code,
                    holder_label_raw, row_position, column_position,
                    period_label_raw, period_end, display_value, value_decimal,
                    unit_code, classification_regime, missing_kind,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,
                           'first_observed')""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        obs.frequency,
                        obs.holder_code,
                        obs.holder_label_raw,
                        obs.row,
                        obs.column,
                        obs.period_label_raw,
                        obs.period_end,
                        obs.display_value,
                        obs.value_decimal,
                        obs.unit_code,
                        obs.classification_regime,
                        obs.missing_kind,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for obs in (
                        *parsed.shareholding_q_observations,
                        *parsed.shareholding_y_observations,
                    )
                ],
            )
            conn.executemany(
                """INSERT INTO company_profile_links
                   (parse_id, reporting_scope, link_type, label_raw,
                    identifier_value, position, source_url,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'first_observed')""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        link.link_type,
                        link.label_raw,
                        link.identifier_value,
                        link.position,
                        link.source_url,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for link in parsed.profile_links
                ],
            )
            conn.executemany(
                """INSERT INTO company_text_observations
                   (parse_id, reporting_scope, text_type, heading_raw, position,
                    display_text, content_origin, is_source_generated,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'first_observed')""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        text.text_type,
                        text.heading_raw,
                        text.position,
                        text.display_text,
                        text.content_origin,
                        1 if text.is_source_generated else 0,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for text in (*parsed.profile_texts, *parsed.analysis_texts, *parsed.commentary_texts)
                ],
            )
            conn.executemany(
                """INSERT INTO document_links
                   (parse_id, reporting_scope, document_type, period_label_raw,
                    title_raw, position, source_url, publisher,
                    availability_status, first_observed_at_utc,
                    source_published_at_utc, available_at_utc,
                    availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,
                           'first_observed')""",
                [
                    (
                        parse_id,
                        parsed.reporting_scope,
                        link.document_type,
                        link.period_label_raw,
                        link.title_raw,
                        link.position,
                        link.source_url,
                        link.publisher,
                        link.availability_status,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for link in parsed.document_links
                ],
            )
            conn.execute(
                "UPDATE parse_artifacts SET parse_status='ok' WHERE parse_id=?", (parse_id,)
            )
    except Exception:
        conn.execute(
            "UPDATE parse_artifacts SET parse_status='failed', validation_status='failed' WHERE parse_id=?",
            (parse_id,),
        )
        conn.commit()
        raise
