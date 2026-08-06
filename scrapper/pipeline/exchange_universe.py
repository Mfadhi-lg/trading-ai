"""Exchange universe refresh and incremental exchange result discovery.

This layer replaces screen-based universe discovery. NSE and BSE security
masters define which companies exist; official result filings say which of them
have reported a new period. Only new companies and advanced reporting periods
reach the existing Screener company worker, which remains the only source of the
detailed company-page dataset.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..config import DB_PATH, SNAPSHOTS
from ..core.http import FetchResponse, fetch_exchange_dataset
from ..db.database import connect, init_db
from ..db.exchange_repo import (
    apply_corporate_changes,
    apply_security_master,
    enqueue_result_filings,
    ingest_result_filings,
    persist_snapshot,
    preview_filing_decisions,
    record_exchange_fetch,
    store_exchange_blob,
)
from ..db.repo import finish_run, start_run, utc_now
from ..sources.exchange import (
    CORPORATE_CHANGE_PARSER_NAME,
    EXCHANGE_PARSER_VERSION,
    RESULT_FILING_PARSER_NAME,
    SECURITY_MASTER_PARSER_NAME,
)
from ..sources.exchange.corporate_changes import (
    CORPORATE_CHANGE_DATASETS,
    parse_corporate_changes,
)
from ..sources.exchange.result_filings import (
    RESULT_FILING_DATASETS,
    parse_result_filings,
)
from ..sources.exchange.security_master import (
    SECURITY_MASTER_DATASETS,
    parse_security_master,
)
from ..sources.exchange.tabular import effective_date_or_today


EXCHANGE_SCHEMA_VERSION = "1.0.0"
UNIVERSE_DATASETS = SECURITY_MASTER_DATASETS + CORPORATE_CHANGE_DATASETS
ALL_DATASETS = UNIVERSE_DATASETS + RESULT_FILING_DATASETS
DATASET_EXCHANGE = {
    dataset: ("BSE" if dataset.startswith("bse_") else "NSE") for dataset in ALL_DATASETS
}


def _acquire(
    *,
    dataset: str,
    file: Path | None,
    url: str | None,
    fetcher: Callable[[str, str], FetchResponse],
) -> tuple[FetchResponse, str, str]:
    """Read an operator-supplied file or fetch one allowlisted official URL."""
    if (file is None) == (url is None):
        raise ValueError("Provide exactly one of --file or --url")
    exchange = DATASET_EXCHANGE[dataset]
    if file is not None:
        content = file.read_bytes()
        response = FetchResponse(
            requested_url=file.resolve().as_uri(),
            final_url=file.resolve().as_uri(),
            status_code=200,
            content=content,
            content_type="text/csv",
            encoding=None,
            etag=None,
            last_modified=None,
            auth_mode="operator_file",
            fetch_status="ok",
        )
        return response, "operator_file", str(file.resolve())
    response = fetcher(str(url), exchange)
    return response, "fetched", str(url)


def _parse(
    dataset: str, content: bytes, *, result_period: str | None = None
) -> Any:
    if dataset in SECURITY_MASTER_DATASETS:
        return parse_security_master(content, dataset)
    if dataset in CORPORATE_CHANGE_DATASETS:
        return parse_corporate_changes(content, dataset)
    return parse_result_filings(content, dataset, result_period=result_period)


def _parser_name(dataset: str) -> str:
    if dataset in SECURITY_MASTER_DATASETS:
        return SECURITY_MASTER_PARSER_NAME
    if dataset in CORPORATE_CHANGE_DATASETS:
        return CORPORATE_CHANGE_PARSER_NAME
    return RESULT_FILING_PARSER_NAME


def _readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rejected_payload(parsed: Any) -> list[dict[str, Any]]:
    return [
        {
            "row_position": item.row_position,
            "reason_code": item.reason_code,
            "reason_detail": item.reason_detail,
        }
        for item in parsed.rejected
    ]


def _ingest(
    *,
    dataset: str,
    file: Path | None,
    url: str | None,
    effective_date: str | None,
    dry_run: bool,
    db_path: Path,
    snapshot_root: Path,
    now_utc: Callable[[], str],
    fetcher: Callable[[str, str], FetchResponse],
    result_period: str | None = None,
) -> tuple[dict[str, Any], Any, sqlite3.Connection | None, int | None]:
    """Acquire, parse, and (unless dry-run) persist one dated snapshot."""
    if dataset not in ALL_DATASETS:
        raise ValueError(f"Unknown exchange dataset: {dataset!r}")
    observed_at = now_utc()
    effective = effective_date_or_today(effective_date, now_utc=observed_at)
    response, acquisition_mode, source_label = _acquire(
        dataset=dataset, file=file, url=url, fetcher=fetcher
    )
    if response.fetch_status != "ok":
        raise RuntimeError(
            "Exchange dataset rejected before parsing: "
            f"status={response.status_code} classification={response.fetch_status}"
        )
    parsed = _parse(dataset, response.content, result_period=result_period)
    base = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "mode": "dry_run" if dry_run else "persisted",
        "dataset": dataset,
        "exchange": DATASET_EXCHANGE[dataset],
        "effective_date": effective,
        "acquisition_mode": acquisition_mode,
        "source_label": source_label,
        "requested_url": url,
        "content_sha256": hashlib.sha256(response.content).hexdigest(),
        "source_encoding": parsed.encoding,
        "observed_at_utc": observed_at,
        "headers": list(parsed.headers),
        "accepted_row_count": parsed.accepted_row_count,
        "rejected_row_count": parsed.rejected_row_count,
        "excluded_row_count": getattr(parsed, "excluded_row_count", 0),
        "rejected_rows": _rejected_payload(parsed),
    }
    if dry_run:
        return base | {"snapshot_id": None, "created": False}, parsed, None, None

    conn = connect(db_path)
    init_db(conn)
    run_id = start_run(
        conn,
        __version__,
        {"source": DATASET_EXCHANGE[dataset], "mode": "exchange_universe", "dataset": dataset},
    )
    try:
        blob_id = store_exchange_blob(
            conn, response,
            exchange=DATASET_EXCHANGE[dataset], dataset=dataset,
            effective_date=effective, observed_at_utc=observed_at,
            snapshot_root=snapshot_root,
        )
        fetch_id = (
            record_exchange_fetch(
                conn, run_id, response,
                exchange=DATASET_EXCHANGE[dataset], blob_id=blob_id,
                fetched_at_utc=observed_at,
            )
            if acquisition_mode == "fetched"
            else None
        )
        snapshot_id, created = persist_snapshot(
            conn,
            exchange=DATASET_EXCHANGE[dataset],
            dataset=dataset,
            effective_date=effective,
            source_label=source_label,
            requested_url=url,
            acquisition_mode=acquisition_mode,
            fetch_id=fetch_id,
            blob_id=blob_id,
            parser_name=_parser_name(dataset),
            parser_version=EXCHANGE_PARSER_VERSION,
            source_encoding=parsed.encoding,
            observed_at_utc=observed_at,
            headers=parsed.headers,
            accepted_row_count=parsed.accepted_row_count,
            rejected=parsed.rejected,
        )
    except Exception:
        finish_run(conn, run_id, "error")
        conn.close()
        raise
    payload = base | {
        "run_id": run_id,
        "blob_id": blob_id,
        "fetch_id": fetch_id,
        "snapshot_id": snapshot_id,
        "created": created,
        "parser_name": _parser_name(dataset),
        "parser_version": EXCHANGE_PARSER_VERSION,
    }
    return payload, parsed, conn, run_id


def refresh_exchange_universe(
    *,
    dataset: str,
    file: Path | None = None,
    url: str | None = None,
    effective_date: str | None = None,
    dry_run: bool = False,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    now_utc: Callable[[], str] = utc_now,
    fetcher: Callable[[str, str], FetchResponse] = fetch_exchange_dataset,
) -> dict[str, Any]:
    """Fold one dated security master or corporate-change file into the registry."""
    if dataset not in UNIVERSE_DATASETS:
        raise ValueError(
            f"{dataset!r} is not a universe dataset; use exchange-discover-results"
        )
    payload, parsed, conn, run_id = _ingest(
        dataset=dataset, file=file, url=url, effective_date=effective_date,
        dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root,
        now_utc=now_utc, fetcher=fetcher,
    )
    if conn is None or run_id is None:
        return payload | {"registry": None}
    try:
        if dataset in SECURITY_MASTER_DATASETS:
            registry = apply_security_master(
                conn, parsed,
                snapshot_id=int(payload["snapshot_id"]),
                effective_date=str(payload["effective_date"]),
                now_utc=str(payload["observed_at_utc"]),
            )
        else:
            registry = apply_corporate_changes(
                conn, parsed,
                snapshot_id=int(payload["snapshot_id"]),
                now_utc=str(payload["observed_at_utc"]),
            )
        finish_run(conn, run_id, "ok")
        return payload | {"registry": registry}
    except Exception:
        finish_run(conn, run_id, "error")
        raise
    finally:
        conn.close()


def discover_exchange_results(
    *,
    dataset: str,
    file: Path | None = None,
    url: str | None = None,
    effective_date: str | None = None,
    result_period: str | None = None,
    dry_run: bool = False,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    now_utc: Callable[[], str] = utc_now,
    fetcher: Callable[[str, str], FetchResponse] = fetch_exchange_dataset,
) -> dict[str, Any]:
    """Ingest one official result feed and queue only advanced reporting periods."""
    if dataset not in RESULT_FILING_DATASETS:
        raise ValueError(
            f"{dataset!r} is not a result dataset; use exchange-refresh-universe"
        )
    payload, parsed, conn, run_id = _ingest(
        dataset=dataset, file=file, url=url, effective_date=effective_date,
        dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root,
        now_utc=now_utc, fetcher=fetcher, result_period=result_period,
    )
    periods = sorted({item.result_period for item in parsed.filings})
    payload = payload | {
        "result_periods": periods,
        "max_broadcast_at_utc": parsed.max_broadcast_at_utc,
    }
    if conn is None or run_id is None:
        readonly = _readonly(db_path)
        try:
            planned = (
                preview_filing_decisions(readonly, parsed) if readonly is not None else {}
            )
        finally:
            if readonly is not None:
                readonly.close()
        return payload | {"filings": None, "enqueue": planned}
    try:
        filings = ingest_result_filings(
            conn, parsed,
            snapshot_id=int(payload["snapshot_id"]),
            now_utc=str(payload["observed_at_utc"]),
        )
        enqueue = enqueue_result_filings(
            conn,
            snapshot_id=int(payload["snapshot_id"]),
            now_utc=str(payload["observed_at_utc"]),
        )
        finish_run(conn, run_id, "ok")
        return payload | {"filings": filings, "enqueue": enqueue}
    except Exception:
        finish_run(conn, run_id, "error")
        raise
    finally:
        conn.close()
