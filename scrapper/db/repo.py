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


from .screener import observations as _observations
from .repository_backend import execute, generated_id, commit, is_postgresql, statement, require_transaction


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(conn: sqlite3.Connection, code_version: str, config: dict) -> int:
    cursor = execute(conn,
        """INSERT INTO scrape_runs
           (started_at_utc, code_version, config_json, status)
           VALUES (?, ?, ?, 'running')""",
        (utc_now(), code_version, json.dumps(config, sort_keys=True)),
        returning="run_id",
    )
    commit(conn)
    return generated_id(cursor, "run_id")


def finish_run(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    execute(conn,
        "UPDATE scrape_runs SET completed_at_utc=?, status=? WHERE run_id=?",
        (utc_now(), status, run_id),
    )
    commit(conn)


def ensure_company(conn: sqlite3.Connection, parsed: ParsedCompany) -> int:
    if is_postgresql(conn):
        require_transaction(conn)
        # Serialize all company identity decisions. Source/NSE/BSE aliases may
        # overlap across independent workers; a per-source lock is insufficient.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     ("trading-ai-company-identity",))
    matches: set[int] = set()
    if parsed.source_company_id:
        row = execute(conn,
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
        row = execute(conn,
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
        execute(conn,
            "UPDATE companies SET current_legal_name=? WHERE company_id=?",
            (parsed.company_name, company_id),
        )
    else:
        cursor = execute(conn,
            "INSERT INTO companies(current_legal_name, status) VALUES (?, 'active')",
            (parsed.company_name,),
            returning="company_id",
        )
        company_id = generated_id(cursor, "company_id")
    for identifier_type, identifier_value in (
        ("NSE", parsed.nse_symbol),
        ("BSE", parsed.bse_code),
    ):
        if identifier_value:
            execute(conn,
                statement(conn,
                    """INSERT OR IGNORE INTO security_identifiers
                       (company_id, identifier_type, identifier_value, is_current)
                       VALUES (?, ?, ?, 1)""",
                    """INSERT INTO security_identifiers
                       (company_id, identifier_type, identifier_value, is_current)
                       VALUES (?, ?, ?, 1) ON CONFLICT DO NOTHING"""),
                (company_id, identifier_type, identifier_value),
            )
    return company_id


def ensure_source_identifier(
    conn: sqlite3.Connection,
    company_id: int,
    parsed: ParsedCompany,
    canonical_url: str,
) -> None:
    return _observations.ensure_source_identifier(conn, company_id, parsed, canonical_url, utc_now=utc_now)


def store_blob(
    conn: sqlite3.Connection,
    response: FetchResponse,
    symbol: str,
    scope: str,
    observed_at_utc: str,
    snapshot_root: Path = SNAPSHOTS,
    resource_kind: str = "company_page",
) -> int:
    return _observations.store_blob(conn, response, symbol, scope, observed_at_utc, snapshot_root, resource_kind, utc_now=utc_now)


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
    return _observations.record_fetch(conn, run_id, company_id, response, scope, fetched_at_utc, blob_id, requested_source_period, resolved_source_period, resource_kind)


def get_or_create_parse_artifact(
    conn: sqlite3.Connection,
    blob_id: int,
    parser_name: str,
    parser_version: str,
    manifest_version: str,
) -> tuple[int, str]:
    return _observations.get_or_create_parse_artifact(conn, blob_id, parser_name, parser_version, manifest_version, utc_now=utc_now)


def _insert_section(
    conn: sqlite3.Connection,
    parse_id: int,
    section_code: str,
    heading: str,
    position: int,
) -> int:
    return _observations._insert_section(conn, parse_id, section_code, heading, position)


def link_supplemental_parse(
    conn: sqlite3.Connection,
    parent_parse_id: int,
    component_code: str,
    supplemental_parse_id: int,
) -> None:
    return _observations.link_supplemental_parse(conn, parent_parse_id, component_code, supplemental_parse_id)


def store_parsed_peer_table(
    conn: sqlite3.Connection,
    parse_id: int,
    parsed: ParsedPeerTable,
    reporting_scope: str,
    first_observed_at_utc: str,
) -> None:
    return _observations.store_parsed_peer_table(conn, parse_id, parsed, reporting_scope, first_observed_at_utc, _insert_section=_insert_section)


def store_parsed_company(
    conn: sqlite3.Connection,
    parse_id: int,
    parsed: ParsedCompany,
    first_observed_at_utc: str,
) -> None:
    return _observations.store_parsed_company(conn, parse_id, parsed, first_observed_at_utc, _insert_section=_insert_section)
