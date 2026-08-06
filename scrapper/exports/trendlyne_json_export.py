"""Accepted JSON export for a Trendlyne monthly-MF page."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..config import EXPORTS


def _rows(conn: sqlite3.Connection, query: str, params: tuple) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params)]


def build_trendlyne_export(
    conn: sqlite3.Connection, company_id: int, parse_id: int
) -> dict:
    company = conn.execute(
        "SELECT * FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    artifact = conn.execute(
        """SELECT pa.*, rb.content_sha256, rb.content_type, rb.encoding,
                  rb.byte_size, rb.local_path, rb.first_observed_at_utc
           FROM parse_artifacts pa
           JOIN response_blobs rb ON rb.blob_id=pa.blob_id
           WHERE pa.parse_id=?""",
        (parse_id,),
    ).fetchone()
    fetch = conn.execute(
        """SELECT * FROM fetch_observations WHERE blob_id=?
           ORDER BY fetched_at_utc DESC, fetch_id DESC LIMIT 1""",
        (artifact["blob_id"],),
    ).fetchone()
    accepted = conn.execute(
        "SELECT * FROM accepted_page_versions WHERE parse_id=?", (parse_id,)
    ).fetchone()
    sections = _rows(
        conn,
        "SELECT * FROM source_sections WHERE parse_id=? ORDER BY position",
        (parse_id,),
    )
    for section in sections:
        section["fields"] = _rows(
            conn,
            "SELECT * FROM source_fields WHERE section_id=? ORDER BY field_position",
            (section["section_id"],),
        )
        section["cells"] = _rows(
            conn,
            """SELECT * FROM source_table_cells WHERE section_id=?
               ORDER BY table_position, row_position, column_position""",
            (section["section_id"],),
        )
    page = conn.execute(
        "SELECT * FROM trendlyne_monthly_pages WHERE parse_id=?", (parse_id,)
    ).fetchone()
    return {
        "provenance": {
            "company": dict(company) if company else None,
            "identifiers": _rows(
                conn,
                """SELECT identifier_type, identifier_value, valid_from, valid_to,
                          is_current FROM security_identifiers
                   WHERE company_id=? ORDER BY security_identifier_id""",
                (company_id,),
            ),
            "source_identifiers": _rows(
                conn,
                """SELECT source, source_company_id, source_symbol, source_slug,
                          canonical_url, mapping_status, verified_at_utc
                   FROM source_identifiers WHERE company_id=?
                   ORDER BY source_identifier_id""",
                (company_id,),
            ),
            "fetch": dict(fetch) if fetch else None,
            "response_blob": dict(artifact) if artifact else None,
            "accepted_version": dict(accepted) if accepted else None,
            "acceptance_history": _rows(
                conn,
                """SELECT acceptance_id, source, reporting_scope,
                          resolved_source_period, parse_id, action,
                          accepted_at_utc, accepted_by, acceptance_mode,
                          reason_json FROM acceptance_events
                   WHERE company_id=? AND source='trendlyne'
                   ORDER BY acceptance_id""",
                (company_id,),
            ),
        },
        "source_faithful": {
            "sections": sections,
            "fund_rows": _rows(
                conn,
                """SELECT table_position, row_position, source_scheme_id,
                          scheme_name_raw, source_url
                   FROM trendlyne_fund_source_rows WHERE parse_id=?
                   ORDER BY table_position, row_position""",
                (parse_id,),
            ),
        },
        "normalized": {
            "monthly_page": dict(page) if page else None,
            "month_history": _rows(
                conn,
                """SELECT * FROM trendlyne_month_links WHERE parse_id=?
                   ORDER BY link_position""",
                (parse_id,),
            ),
            "monthly_summary": (
                dict(summary)
                if (
                    summary := conn.execute(
                        "SELECT * FROM trendlyne_mf_page_summaries WHERE parse_id=?",
                        (parse_id,),
                    ).fetchone()
                )
                else None
            ),
            "fund_observations": _rows(
                conn,
                """SELECT * FROM trendlyne_fund_observations
                   WHERE parse_id=?
                   ORDER BY table_position, row_position, column_position""",
                (parse_id,),
            ),
        },
        "quality": _rows(
            conn,
            "SELECT * FROM validation_results WHERE parse_id=? ORDER BY validation_id",
            (parse_id,),
        ),
    }


def write_trendlyne_export(
    conn: sqlite3.Connection,
    run_id: int,
    company_id: int,
    parse_id: int,
    symbol: str,
    resolved_source_period: str,
    export_root: Path = EXPORTS,
) -> Path:
    destination = (
        export_root
        / str(run_id)
        / "trendlyne"
        / f"{symbol.upper()}-{resolved_source_period}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            build_trendlyne_export(conn, company_id, parse_id),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination
