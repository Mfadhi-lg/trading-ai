"""Persistence helpers for the Trendlyne monthly-page slices."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from ..config import SNAPSHOTS
from ..core.http import FetchResponse
from ..sources.trendlyne.monthly_mf import ParsedMonthlyMfPage, SourceField
from ..sources.trendlyne.urls import TrendlyneMapping
from .repo import utc_now


def ensure_trendlyne_company(
    conn: sqlite3.Connection, mapping: TrendlyneMapping
) -> int:
    row = conn.execute(
        """SELECT company_id FROM security_identifiers
           WHERE identifier_type='NSE' AND identifier_value=? AND is_current=1""",
        (mapping.source_symbol,),
    ).fetchone()
    if row:
        company_id = int(row["company_id"])
        conn.execute(
            "UPDATE companies SET current_legal_name=? WHERE company_id=?",
            (mapping.company_name, company_id),
        )
    else:
        cursor = conn.execute(
            "INSERT INTO companies(current_legal_name, status) VALUES (?, 'active')",
            (mapping.company_name,),
        )
        company_id = int(cursor.lastrowid)
        conn.execute(
            """INSERT INTO security_identifiers
               (company_id, identifier_type, identifier_value, is_current)
               VALUES (?, 'NSE', ?, 1)""",
            (company_id, mapping.source_symbol),
        )
    if mapping.bse_code:
        conn.execute(
            """INSERT OR IGNORE INTO security_identifiers
               (company_id, identifier_type, identifier_value, is_current)
               VALUES (?, 'BSE', ?, 1)""",
            (company_id, mapping.bse_code),
        )
    conn.execute(
        """INSERT INTO source_identifiers
           (company_id, source, source_company_id, source_symbol, source_slug,
            canonical_url, mapping_status, verified_at_utc)
           VALUES (?, 'trendlyne', ?, ?, ?, ?, 'verified', ?)
           ON CONFLICT(company_id, source) DO UPDATE SET
             source_company_id=excluded.source_company_id,
             source_symbol=excluded.source_symbol,
             source_slug=excluded.source_slug,
             canonical_url=excluded.canonical_url,
             mapping_status='verified',
             verified_at_utc=excluded.verified_at_utc""",
        (
            company_id,
            mapping.source_company_id,
            mapping.source_symbol,
            mapping.source_slug,
            mapping.canonical_url,
            utc_now(),
        ),
    )
    conn.commit()
    return company_id


def store_trendlyne_blob(
    conn: sqlite3.Connection,
    response: FetchResponse,
    mapping: TrendlyneMapping,
    observed_at_utc: str,
    snapshot_root: Path = SNAPSHOTS,
) -> int:
    sha256 = hashlib.sha256(response.content).hexdigest()
    destination = (
        snapshot_root
        / "trendlyne"
        / mapping.source_symbol
        / mapping.source_company_id
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
        raise RuntimeError("Failed to persist Trendlyne response blob")
    conn.commit()
    return int(row["blob_id"])


def record_trendlyne_fetch(
    conn: sqlite3.Connection,
    run_id: int,
    company_id: int,
    response: FetchResponse,
    requested_source_period: str,
    resolved_source_period: str,
    fetched_at_utc: str,
    blob_id: int,
) -> int:
    cursor = conn.execute(
        """INSERT INTO fetch_observations
           (run_id, company_id, source, reporting_scope,
            requested_source_period, resolved_source_period,
            requested_url, final_url, fetched_at_utc, http_status, etag,
            last_modified, auth_mode, blob_id, fetch_status, resource_kind)
           VALUES (?, ?, 'trendlyne', 'not_applicable', ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, 'monthly_mf_page')""",
        (
            run_id,
            company_id,
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
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def store_parsed_trendlyne_page(
    conn: sqlite3.Connection, parse_id: int, parsed: ParsedMonthlyMfPage
) -> None:
    artifact = conn.execute(
        """SELECT pa.parse_status, rb.first_observed_at_utc
           FROM parse_artifacts AS pa
           JOIN response_blobs AS rb ON rb.blob_id=pa.blob_id
           WHERE pa.parse_id=?""",
        (parse_id,),
    ).fetchone()
    if artifact is None or artifact["parse_status"] != "pending":
        raise ValueError("Only a pending Trendlyne parse can receive observations")
    first_observed_at_utc = str(artifact["first_observed_at_utc"])
    try:
        with conn:
            identity_cursor = conn.execute(
                """INSERT INTO source_sections
                   (parse_id, section_code, heading_raw, position, parse_status)
                   VALUES (?, 'trendlyne_identity', 'Company identity', 0, 'ok')""",
                (parse_id,),
            )
            period_cursor = conn.execute(
                """INSERT INTO source_sections
                   (parse_id, section_code, heading_raw, position, parse_status)
                   VALUES (?, 'trendlyne_selected_month', 'Selected month', 1, 'ok')""",
                (parse_id,),
            )
            summary_cursor = conn.execute(
                """INSERT INTO source_sections
                   (parse_id, section_code, heading_raw, position, parse_status)
                   VALUES (?, 'trendlyne_monthly_summary',
                           'Mutual fund monthly summary', 3, 'ok')""",
                (parse_id,),
            )
            history_cursor = conn.execute(
                """INSERT INTO source_sections
                   (parse_id, section_code, heading_raw, position, parse_status)
                   VALUES (?, 'trendlyne_month_history', 'Choose Month', 2, 'ok')""",
                (parse_id,),
            )
            fund_cursor = conn.execute(
                """INSERT INTO source_sections
                   (parse_id, section_code, heading_raw, position, parse_status)
                   VALUES (?, 'trendlyne_fund_table', 'Mutual fund holdings', 4,
                           'ok')""",
                (parse_id,),
            )
            for section_id, fields in (
                (int(identity_cursor.lastrowid), parsed.identity_fields),
                (int(period_cursor.lastrowid), parsed.period_fields),
                (int(summary_cursor.lastrowid), parsed.summary_fields),
                (
                    int(history_cursor.lastrowid),
                    tuple(
                        SourceField(
                            link.position,
                            "month_link",
                            link.raw_text,
                            link.display_text,
                            link.source_url,
                            "period_link",
                        )
                        for link in parsed.month_links
                    ),
                ),
            ):
                conn.executemany(
                    """INSERT INTO source_fields
                       (section_id, field_position, field_key_raw, raw_text,
                        normalized_display_text, link_url, field_role,
                        visibility_state)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            section_id,
                            field.position,
                            field.key,
                            field.raw_text,
                            field.display_text,
                            field.link_url,
                            field.role,
                            field.visibility_state,
                        )
                        for field in fields
                    ],
                )
            conn.executemany(
                """INSERT INTO trendlyne_month_links
                   (parse_id, link_position, period_label_raw, source_period,
                    source_url, source_url_period_raw, source_company_id,
                    source_symbol, source_slug, is_active,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?,
                           'first_observed')""",
                [
                    (
                        parse_id,
                        link.position,
                        link.display_text,
                        link.source_period,
                        link.source_url,
                        link.source_url_period_raw,
                        link.source_company_id,
                        link.source_symbol,
                        link.source_slug,
                        int(link.is_active),
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for link in parsed.month_links
                ],
            )
            fund_section_id = int(fund_cursor.lastrowid)
            conn.executemany(
                """INSERT INTO source_table_cells
                   (section_id, table_position, row_position, column_position,
                    row_label_raw, column_label_raw, raw_text,
                    normalized_display_text, link_url, cell_role,
                    visibility_state, rowspan, colspan)
                   VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        fund_section_id,
                        cell.row,
                        cell.column,
                        cell.row_label,
                        cell.column_label,
                        cell.raw_text,
                        cell.display_text,
                        cell.link_url,
                        cell.role,
                        cell.visibility_state,
                        cell.rowspan,
                        cell.colspan,
                    )
                    for cell in parsed.fund_cells
                ],
            )
            conn.executemany(
                """INSERT INTO trendlyne_fund_source_rows
                   (parse_id, table_position, row_position, source_scheme_id,
                    scheme_name_raw, source_url)
                   VALUES (?, 0, ?, ?, ?, ?)""",
                [
                    (
                        parse_id,
                        row.row,
                        row.source_scheme_id,
                        row.scheme_name_raw,
                        row.source_url,
                    )
                    for row in parsed.fund_rows
                ],
            )
            conn.executemany(
                """INSERT INTO trendlyne_fund_observations
                   (parse_id, reporting_scope, table_position, row_position,
                    column_position, source_scheme_id, scheme_name_raw,
                    metric_code, period_role, period_label_raw, source_period,
                    period_end, period_type, display_value, value_decimal,
                    unit_code, scale_multiplier, missing_kind,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, 'not_applicable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, NULL, ?, 'first_observed')""",
                [
                    (
                        parse_id,
                        observation.table,
                        observation.row,
                        observation.column,
                        observation.source_scheme_id,
                        observation.scheme_name_raw,
                        observation.metric_code,
                        observation.period_role,
                        observation.period_label_raw,
                        observation.source_period,
                        observation.period_end,
                        observation.period_type,
                        observation.display_value,
                        observation.value_decimal,
                        observation.unit_code,
                        observation.scale_multiplier,
                        observation.missing_kind,
                        first_observed_at_utc,
                        first_observed_at_utc,
                    )
                    for observation in parsed.fund_observations
                ],
            )
            summary = parsed.summary
            conn.execute(
                """INSERT INTO trendlyne_mf_page_summaries
                   (parse_id, reporting_scope, period_label_raw, source_period,
                    period_end, headline_raw, headline_display_text,
                    headline_period_label_raw, headline_buyers_display,
                    headline_buyers, headline_sellers_display, headline_sellers,
                    headline_net_change_display, headline_net_change,
                    headline_is_canonical, table_total_shares_display,
                    table_total_shares, table_total_change_display,
                    table_total_change, computed_buyers, computed_sellers,
                    computed_holders, computed_net_change,
                    computed_values_are_canonical, computation_basis_json,
                    first_observed_at_utc, source_published_at_utc,
                    available_at_utc, availability_basis)
                   VALUES (?, 'not_applicable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, ?,
                           'first_observed')""",
                (
                    parse_id,
                    summary.period_label_raw,
                    summary.source_period,
                    summary.period_end,
                    summary.headline_raw,
                    summary.headline_display_text,
                    summary.headline_period_label_raw,
                    summary.headline_buyers_display,
                    summary.headline_buyers,
                    summary.headline_sellers_display,
                    summary.headline_sellers,
                    summary.headline_net_change_display,
                    summary.headline_net_change,
                    summary.table_total_shares_display,
                    summary.table_total_shares,
                    summary.table_total_change_display,
                    summary.table_total_change,
                    summary.computed_buyers,
                    summary.computed_sellers,
                    summary.computed_holders,
                    summary.computed_net_change,
                    json.dumps(
                        {
                            "buyers": "count current month_change > 0",
                            "sellers": "count current month_change < 0",
                            "holders": "count current shares_held cells that are not missing",
                            "net_change": "sum non-missing current month_change values using Decimal",
                        },
                        sort_keys=True,
                    ),
                    first_observed_at_utc,
                    first_observed_at_utc,
                ),
            )
            conn.execute(
                """INSERT INTO trendlyne_monthly_pages
                   (parse_id, reporting_scope, source_company_id, source_symbol,
                    source_slug, company_name_raw, bse_code, canonical_url,
                    canonical_source_company_id, canonical_symbol,
                    canonical_period, selected_month_raw, active_month_raw,
                    active_month_url, active_url_period_raw, heading_raw,
                    heading_month_raw, resolved_source_period)
                   VALUES (?, 'not_applicable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?)""",
                (
                    parse_id,
                    parsed.source_company_id,
                    parsed.source_symbol,
                    parsed.source_slug,
                    parsed.company_name_raw,
                    parsed.bse_code,
                    parsed.canonical_url,
                    parsed.canonical_source_company_id,
                    parsed.canonical_symbol,
                    parsed.canonical_period,
                    parsed.selected_month_raw,
                    parsed.active_month_raw,
                    parsed.active_month_url,
                    parsed.active_url_period_raw,
                    parsed.heading_raw,
                    parsed.heading_month_raw,
                    parsed.resolved_source_period,
                ),
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
