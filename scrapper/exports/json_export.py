"""Export an accepted parse with both source-faithful and normalized layers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .writers import write_json_payload

from ..config import EXPORTS


def _rows(conn: sqlite3.Connection, query: str, params: tuple) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params)]


def build_export(conn: sqlite3.Connection, company_id: int, parse_id: int) -> dict:
    company = conn.execute(
        "SELECT * FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    identifiers = _rows(
        conn,
        """SELECT identifier_type, identifier_value, valid_from, valid_to, is_current
           FROM security_identifiers WHERE company_id=? ORDER BY security_identifier_id""",
        (company_id,),
    )
    source_identifiers = _rows(
        conn,
        """SELECT source, source_company_id, source_symbol, source_slug,
                  canonical_url, mapping_status, verified_at_utc
           FROM source_identifiers WHERE company_id=? ORDER BY source_identifier_id""",
        (company_id,),
    )
    artifact = conn.execute(
        """SELECT pa.*, rb.content_sha256, rb.local_path, rb.first_observed_at_utc,
                  rb.byte_size
           FROM parse_artifacts pa JOIN response_blobs rb ON rb.blob_id=pa.blob_id
           WHERE pa.parse_id=?""",
        (parse_id,),
    ).fetchone()
    fetch = conn.execute(
        """SELECT fo.* FROM fetch_observations fo
           JOIN parse_artifacts pa ON pa.blob_id=fo.blob_id
           WHERE pa.parse_id=? ORDER BY fo.fetched_at_utc DESC, fo.fetch_id DESC LIMIT 1""",
        (parse_id,),
    ).fetchone()
    accepted = conn.execute(
        "SELECT * FROM accepted_page_versions WHERE parse_id=?", (parse_id,)
    ).fetchone()
    acceptance_history = _rows(
        conn,
        """SELECT acceptance_id, source, reporting_scope,
                  resolved_source_period, parse_id, action, accepted_at_utc,
                  accepted_by, acceptance_mode, reason_json
           FROM acceptance_events WHERE company_id=? ORDER BY acceptance_id""",
        (company_id,),
    )
    sections = _rows(
        conn,
        "SELECT * FROM source_sections WHERE parse_id=? ORDER BY position",
        (parse_id,),
    )
    for section in sections:
        section_id = section["section_id"]
        section["fields"] = _rows(
            conn,
            "SELECT * FROM source_fields WHERE section_id=? ORDER BY field_position",
            (section_id,),
        )
        section["cells"] = _rows(
            conn,
            """SELECT * FROM source_table_cells WHERE section_id=?
               ORDER BY table_position, row_position, column_position""",
            (section_id,),
        )
    supplemental_resources: list[dict] = []
    supplemental_links = _rows(
        conn,
        """SELECT spl.component_code, spl.supplemental_parse_id,
                  pa.parser_name, pa.parser_version, pa.manifest_version,
                  pa.parse_status, pa.validation_status, pa.parsed_at_utc,
                  rb.blob_id, rb.content_sha256, rb.content_type, rb.encoding,
                  rb.byte_size, rb.local_path, rb.first_observed_at_utc
           FROM supplemental_parse_links spl
           JOIN parse_artifacts pa ON pa.parse_id=spl.supplemental_parse_id
           JOIN response_blobs rb ON rb.blob_id=pa.blob_id
           WHERE spl.parent_parse_id=? ORDER BY spl.component_code""",
        (parse_id,),
    )
    peer_parse_id: int | None = None
    for supplemental in supplemental_links:
        supplemental_parse_id = int(supplemental["supplemental_parse_id"])
        if supplemental["component_code"] == "peer_table":
            peer_parse_id = supplemental_parse_id
        supplemental["fetch"] = dict(
            conn.execute(
                """SELECT * FROM fetch_observations
                   WHERE blob_id=?
                   ORDER BY fetched_at_utc DESC, fetch_id DESC LIMIT 1""",
                (supplemental["blob_id"],),
            ).fetchone()
        )
        supplemental["quality"] = _rows(
            conn,
            """SELECT * FROM validation_results
               WHERE parse_id=? ORDER BY validation_id""",
            (supplemental_parse_id,),
        )
        supplemental_sections = _rows(
            conn,
            """SELECT * FROM source_sections
               WHERE parse_id=? ORDER BY position""",
            (supplemental_parse_id,),
        )
        for section in supplemental_sections:
            section_id = section["section_id"]
            section["fields"] = _rows(
                conn,
                """SELECT * FROM source_fields
                   WHERE section_id=? ORDER BY field_position""",
                (section_id,),
            )
            section["cells"] = _rows(
                conn,
                """SELECT * FROM source_table_cells WHERE section_id=?
                   ORDER BY table_position, row_position, column_position""",
                (section_id,),
            )
        sections.extend(supplemental_sections)
        supplemental_resources.append(supplemental)
    sections.sort(key=lambda item: (int(item["position"]), int(item["parse_id"])))
    return {
        "provenance": {
            "company": dict(company) if company else None,
            "identifiers": identifiers,
            "source_identifiers": source_identifiers,
            "fetch": dict(fetch) if fetch else None,
            "response_blob": dict(artifact) if artifact else None,
            "accepted_version": dict(accepted) if accepted else None,
            "acceptance_history": acceptance_history,
            "supplemental_resources": supplemental_resources,
        },
        "source_faithful": {"sections": sections},
        "normalized": {
            "quote": dict(
                conn.execute(
                    "SELECT * FROM quote_observations WHERE parse_id=?", (parse_id,)
                ).fetchone()
            ),
            "metrics": _rows(
                conn,
                """SELECT * FROM company_metric_observations
                   WHERE parse_id=? ORDER BY position""",
                (parse_id,),
            ),
            "company_profile_links": _rows(
                conn,
                """SELECT * FROM company_profile_links
                   WHERE parse_id=? ORDER BY position""",
                (parse_id,),
            ),
            "company_text": _rows(
                conn,
                """SELECT * FROM company_text_observations
                   WHERE parse_id=?
                   ORDER BY CASE text_type
                       WHEN 'company_description' THEN 0
                       WHEN 'about' THEN 1
                       WHEN 'pro' THEN 2
                       WHEN 'con' THEN 3
                       WHEN 'commentary_disclosure' THEN 4
                       WHEN 'commentary_methodology' THEN 5
                       ELSE 99 END,
                       position""",
                (parse_id,),
            ),
            "quarterly_results": _rows(
                conn,
                """SELECT * FROM financial_observations
                   WHERE parse_id=? AND section_code='quarters'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "profit_loss": _rows(
                conn,
                """SELECT * FROM financial_observations
                   WHERE parse_id=? AND section_code='pnl'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "balance_sheet": _rows(
                conn,
                """SELECT * FROM financial_observations
                   WHERE parse_id=? AND section_code='balance_sheet'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "cash_flow": _rows(
                conn,
                """SELECT * FROM financial_observations
                   WHERE parse_id=? AND section_code='cash_flow'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "annual_ratios": _rows(
                conn,
                """SELECT * FROM financial_observations
                   WHERE parse_id=? AND section_code='ratios'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "growth": _rows(
                conn,
                """SELECT * FROM growth_observations
                   WHERE parse_id=? ORDER BY block_position, row_position""",
                (parse_id,),
            ),
            "shareholding_quarterly": _rows(
                conn,
                """SELECT * FROM shareholding_observations
                   WHERE parse_id=? AND frequency='quarterly'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "shareholding_yearly": _rows(
                conn,
                """SELECT * FROM shareholding_observations
                   WHERE parse_id=? AND frequency='yearly'
                   ORDER BY row_position, column_position""",
                (parse_id,),
            ),
            "document_links": _rows(
                conn,
                """SELECT * FROM document_links
                   WHERE parse_id=? ORDER BY position""",
                (parse_id,),
            ),
            "peers": (
                _rows(
                    conn,
                    """SELECT * FROM peer_observations
                       WHERE parse_id=? ORDER BY row_position, column_position""",
                    (peer_parse_id,),
                )
                if peer_parse_id is not None
                else []
            ),
        },
        "quality": _rows(
            conn,
            "SELECT * FROM validation_results WHERE parse_id=? ORDER BY validation_id",
            (parse_id,),
        ),
    }


def write_export(
    conn: sqlite3.Connection,
    run_id: int,
    company_id: int,
    parse_id: int,
    symbol: str,
    export_root: Path = EXPORTS,
) -> Path:
    payload = build_export(conn, company_id, parse_id)
    destination = export_root / str(run_id) / "screener" / f"{symbol.upper()}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_payload(payload, destination, ensure_ascii=False)
    return destination
