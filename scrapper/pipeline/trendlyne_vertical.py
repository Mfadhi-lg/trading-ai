"""Trendlyne monthly-MF ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..config import (
    DB_PATH,
    EXPORTS,
    SNAPSHOTS,
    TRENDLYNE_MANIFEST_VERSION,
    TRENDLYNE_PARSER_NAME,
    TRENDLYNE_PARSER_VERSION,
)
from ..core.http import FetchResponse, fetch_trendlyne
from ..db.database import connect, init_db
from ..db.repo import (
    finish_run,
    get_or_create_parse_artifact,
    start_run,
    utc_now,
)
from ..db.trendlyne_repo import (
    ensure_trendlyne_company,
    record_trendlyne_fetch,
    store_parsed_trendlyne_page,
    store_trendlyne_blob,
)
from ..exports.trendlyne_json_export import write_trendlyne_export
from ..sources.trendlyne.monthly_mf import parse_monthly_mf_page
from ..sources.trendlyne.urls import (
    monthly_mf_url,
    source_period_for_url,
    verified_mapping,
)
from ..validation.trendlyne import accept_trendlyne_parse, validate_trendlyne_parse


@dataclass(frozen=True)
class TrendlyneSliceResult:
    run_id: int
    fetch_id: int
    blob_id: int
    parse_id: int
    company_id: int
    requested_source_period: str
    resolved_source_period: str
    export_path: Path
    reused_parse: bool


def ingest_trendlyne_response(
    symbol: str,
    response: FetchResponse,
    *,
    requested_source_period: str = "latest",
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    export_root: Path = EXPORTS,
    fetched_at_utc: str | None = None,
) -> TrendlyneSliceResult:
    source_period_for_url(requested_source_period)
    mapping = verified_mapping(symbol)
    fetched_at = fetched_at_utc or utc_now()
    conn = connect(db_path)
    init_db(conn)
    run_id = start_run(
        conn,
        __version__,
        {
            "source": "trendlyne",
            "symbol": mapping.source_symbol,
            "requested_source_period": requested_source_period,
        },
    )
    try:
        company_id = ensure_trendlyne_company(conn, mapping)
        blob_id = store_trendlyne_blob(
            conn, response, mapping, fetched_at, snapshot_root
        )
        fetch_id = record_trendlyne_fetch(
            conn,
            run_id,
            company_id,
            response,
            requested_source_period,
            "unknown",
            fetched_at,
            blob_id,
        )
        if response.fetch_status != "ok":
            raise RuntimeError(
                f"Response rejected before parsing: status={response.status_code} "
                f"classification={response.fetch_status}"
            )

        parsed = parse_monthly_mf_page(response.content)
        resolved = parsed.resolved_source_period or "unknown"
        conn.execute(
            """UPDATE fetch_observations SET resolved_source_period=?
               WHERE fetch_id=?""",
            (resolved, fetch_id),
        )
        conn.commit()
        if (
            requested_source_period != "latest"
            and requested_source_period != resolved
        ):
            raise ValueError(
                "Trendlyne response resolved to a different month than requested: "
                f"requested={requested_source_period!r} resolved={resolved!r}"
            )

        parse_id, parse_status = get_or_create_parse_artifact(
            conn,
            blob_id,
            TRENDLYNE_PARSER_NAME,
            TRENDLYNE_PARSER_VERSION,
            TRENDLYNE_MANIFEST_VERSION,
        )
        reused_parse = parse_status == "ok"
        if parse_status == "pending":
            store_parsed_trendlyne_page(conn, parse_id, parsed)
        elif parse_status != "ok":
            raise RuntimeError(
                "This Trendlyne blob/parser version has a failed artifact; bump "
                "the parser version after fixing it"
            )

        artifact = conn.execute(
            "SELECT validation_status FROM parse_artifacts WHERE parse_id=?",
            (parse_id,),
        ).fetchone()
        if artifact["validation_status"] == "pending":
            validate_trendlyne_parse(conn, parse_id, mapping)
        resolved = accept_trendlyne_parse(conn, company_id, parse_id, mapping)
        export_path = write_trendlyne_export(
            conn,
            run_id,
            company_id,
            parse_id,
            mapping.source_symbol,
            resolved,
            export_root,
        )
        finish_run(conn, run_id, "ok")
        return TrendlyneSliceResult(
            run_id=run_id,
            fetch_id=fetch_id,
            blob_id=blob_id,
            parse_id=parse_id,
            company_id=company_id,
            requested_source_period=requested_source_period,
            resolved_source_period=resolved,
            export_path=export_path,
            reused_parse=reused_parse,
        )
    except Exception:
        finish_run(conn, run_id, "error")
        raise
    finally:
        conn.close()


def scrape_trendlyne_company(
    symbol: str,
    *,
    requested_source_period: str = "latest",
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    export_root: Path = EXPORTS,
) -> TrendlyneSliceResult:
    mapping = verified_mapping(symbol)
    response = fetch_trendlyne(
        monthly_mf_url(mapping, source_period_for_url(requested_source_period))
    )
    return ingest_trendlyne_response(
        symbol,
        response,
        requested_source_period=requested_source_period,
        db_path=db_path,
        snapshot_root=snapshot_root,
        export_root=export_root,
    )
