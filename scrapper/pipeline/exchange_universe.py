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
from ..config import (
    DB_PATH,
    EXCHANGE_GUARD_ABSENCE_VOLUME_FLOOR,
    EXCHANGE_GUARD_ABSENCE_VOLUME_RATIO,
    EXCHANGE_GUARD_DUPLICATE_ISIN_RATE,
    EXCHANGE_GUARD_REJECTED_ROW_RATE,
    EXCHANGE_GUARD_ROW_COUNT_DROP_RATIO,
    EXCHANGE_GUARD_ROW_FLOOR,
    SNAPSHOTS,
)
from ..core.http import FetchResponse, fetch_exchange_dataset
from ..db.database import connect, init_db
from ..db.exchange_repo import (
    absence_preview,
    apply_corporate_changes,
    apply_security_master,
    enqueue_result_filings,
    ingest_result_filings,
    latest_passed_snapshot,
    persist_snapshot,
    preview_filing_decisions,
    record_exchange_fetch,
    record_snapshot_validation,
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
from ..sources.exchange.datasets import (
    DATASET_KEYS,
    authoritative_segments,
    dataset_config,
    default_url_for,
)
from ..sources.exchange.result_filings import (
    RESULT_FILING_DATASETS,
    parse_result_filings,
)
from ..sources.exchange.security_master import (
    SECURITY_MASTER_DATASETS,
    parse_security_master,
)
from ..sources.exchange.snapshot_guard import (
    GuardThresholds,
    GuardVerdict,
    SnapshotMetrics,
    evaluate_snapshot,
    isin_metrics,
    override,
)
from ..sources.exchange.tabular import (
    decompress_source,
    effective_date_or_today,
    is_json_payload,
    read_delimited,
    read_json_records,
)


EXCHANGE_SCHEMA_VERSION = "1.2.0"


class DatasetNotPublished(RuntimeError):
    """The exchange answered that the dated artifact does not exist.

    Distinct from every other acquisition failure: nothing is wrong with the
    fetch, the pipeline or the endpoint — there is simply no file for this day
    or this period yet. It is the only sound evidence of non-publication, which
    is why it is a type and not a message an outer layer has to parse.
    """


class ExchangeSourceAccessBlocked(RuntimeError):
    """A BSE HTTP 403 denied this machine's source request."""


UNIVERSE_DATASETS =SECURITY_MASTER_DATASETS + CORPORATE_CHANGE_DATASETS
ALL_DATASETS = UNIVERSE_DATASETS + RESULT_FILING_DATASETS
DATASET_EXCHANGE = {dataset: dataset_config(dataset).exchange for dataset in DATASET_KEYS}


def resolve_url(dataset: str, url: str | None, *, result_period: str | None) -> str | None:
    """The URL to fetch: the operator's if given, else the registry's default."""
    if url is not None:
        return url
    return default_url_for(dataset, result_period=result_period)


def _acquire(
    *,
    dataset: str,
    file: Path | None,
    url: str | None,
    fetcher: Callable[[str, str], FetchResponse],
) -> tuple[FetchResponse, str, str]:
    """Read an operator-supplied file or fetch one allowlisted official URL."""
    if file is not None and url is not None:
        raise ValueError("Provide at most one of --file or --url")
    if file is None and url is None:
        raise ValueError(
            f"{dataset} has no registry default URL; pass --file or --url"
        )
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
) -> tuple[Any, str]:
    """Expand any compression, then parse. The caller keeps the served bytes.

    Decompression happens here and nowhere else, so the blob written to the
    snapshot store stays byte-identical to what the exchange published while the
    parser still sees a plain table.
    """
    payload, compression = decompress_source(content)
    if dataset in SECURITY_MASTER_DATASETS:
        return parse_security_master(payload, dataset), compression
    if dataset in CORPORATE_CHANGE_DATASETS:
        return parse_corporate_changes(payload, dataset), compression
    return (
        parse_result_filings(payload, dataset, result_period=result_period),
        compression,
    )


def _parser_name(dataset: str) -> str:
    if dataset in SECURITY_MASTER_DATASETS:
        return SECURITY_MASTER_PARSER_NAME
    if dataset in CORPORATE_CHANGE_DATASETS:
        return CORPORATE_CHANGE_PARSER_NAME
    return RESULT_FILING_PARSER_NAME


def thresholds_for_dataset(dataset: str) -> GuardThresholds:
    """Guard limits for one dataset.

    A security master is a full statement of the universe, so its size and its
    ISIN uniqueness are meaningful against the previous one. A result or
    corporate-change feed is incremental: it carries whatever happened that day,
    and one company filing two scopes repeats its ISIN legitimately.
    """
    full_universe = dataset in SECURITY_MASTER_DATASETS
    floor = (
        dataset_config(dataset).row_floor
        if dataset in DATASET_KEYS
        else EXCHANGE_GUARD_ROW_FLOOR
    )
    return GuardThresholds(
        row_count_drop_ratio=(
            EXCHANGE_GUARD_ROW_COUNT_DROP_RATIO if full_universe else None
        ),
        row_floor=floor,
        rejected_row_rate=EXCHANGE_GUARD_REJECTED_ROW_RATE,
        duplicate_isin_rate=(
            EXCHANGE_GUARD_DUPLICATE_ISIN_RATE if full_universe else None
        ),
        absence_volume_floor=EXCHANGE_GUARD_ABSENCE_VOLUME_FLOOR,
        absence_volume_ratio=EXCHANGE_GUARD_ABSENCE_VOLUME_RATIO,
    )


def _isins(parsed: Any) -> list[str | None]:
    """Every ISIN this file claims, in source order, including repeats."""
    items = getattr(parsed, "records", None)
    if items is None:
        items = getattr(parsed, "filings", ())
    return [getattr(item, "isin", None) for item in items]


def _evaluate(
    conn: sqlite3.Connection,
    parsed: Any,
    *,
    dataset: str,
    effective_date: str,
    observed_at_utc: str,
    thresholds: GuardThresholds,
    override_validation: bool,
    exclude_snapshot_id: int | None = None,
) -> GuardVerdict:
    """Measure the candidate snapshot and judge it against the last accepted one."""
    distinct, duplicates, non_null = isin_metrics(_isins(parsed))
    scopes = (
        absence_preview(
            conn, parsed,
            effective_date=effective_date,
            authoritative_segments=authoritative_segments(dataset),
        )
        if dataset in SECURITY_MASTER_DATASETS
        else ()
    )
    would_absent = sum(scope.would_absent_count for scope in scopes)
    present = sum(scope.present_count for scope in scopes)
    metrics = SnapshotMetrics(
        accepted_row_count=parsed.accepted_row_count,
        rejected_row_count=parsed.rejected_row_count,
        headers=tuple(parsed.headers),
        distinct_isin_count=distinct,
        duplicate_isin_row_count=duplicates,
        non_null_isin_count=non_null,
        effective_date=effective_date,
        would_absent_count=would_absent,
        present_count=present,
        scope_metrics=scopes,
    )
    baseline = latest_passed_snapshot(
        conn, dataset, before_snapshot_id=exclude_snapshot_id
    )
    verdict = evaluate_snapshot(
        metrics, baseline, thresholds, today=observed_at_utc[:10]
    )
    return override(verdict) if override_validation else verdict


def _guard(
    conn: sqlite3.Connection,
    parsed: Any,
    *,
    dataset: str,
    snapshot_id: int,
    effective_date: str,
    observed_at_utc: str,
    thresholds: GuardThresholds,
    override_validation: bool,
) -> GuardVerdict:
    """Judge the stored snapshot before it is allowed to change current state."""
    verdict = _evaluate(
        conn, parsed,
        dataset=dataset,
        effective_date=effective_date,
        observed_at_utc=observed_at_utc,
        thresholds=thresholds,
        override_validation=override_validation,
        exclude_snapshot_id=snapshot_id,
    )
    record_snapshot_validation(
        conn, snapshot_id=snapshot_id, verdict=verdict, evaluated_at_utc=observed_at_utc
    )
    return verdict


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


def _excluded_counts(parsed: Any) -> dict[str, int]:
    """How many valid rows this file carried that we deliberately do not collect."""
    counts: dict[str, int] = {}
    for item in getattr(parsed, "excluded", ()):
        counts[item.instrument_class] = counts.get(item.instrument_class, 0) + 1
    return dict(sorted(counts.items()))


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
    thresholds: GuardThresholds | None = None,
    override_validation: bool = False,
) -> tuple[dict[str, Any], Any, sqlite3.Connection | None, int | None]:
    """Acquire, parse, and (unless dry-run) persist one dated snapshot."""
    if dataset not in ALL_DATASETS:
        raise ValueError(f"Unknown exchange dataset: {dataset!r}")
    observed_at = now_utc()
    effective = effective_date_or_today(effective_date, now_utc=observed_at)
    response, acquisition_mode, source_label = _acquire(
        dataset=dataset, file=file, url=url, fetcher=fetcher
    )
    if response.fetch_status == "not_found":
        raise DatasetNotPublished(
            f"{dataset} has no published artifact at {source_label}: "
            f"status={response.status_code}"
        )
    if DATASET_EXCHANGE[dataset] == "BSE" and response.status_code == 403:
        raise ExchangeSourceAccessBlocked(
            f"SOURCE_ACCESS_BLOCKED: {dataset} status=403"
        )
    if response.fetch_status != "ok":
        raise RuntimeError(
            "Exchange dataset rejected before parsing: "
            f"status={response.status_code} classification={response.fetch_status}"
        )
    parsed, compression = _parse(dataset, response.content, result_period=result_period)
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
        "compression": compression,
        "observed_at_utc": observed_at,
        "headers": list(parsed.headers),
        "accepted_row_count": parsed.accepted_row_count,
        "rejected_row_count": parsed.rejected_row_count,
        "excluded_row_count": getattr(parsed, "excluded_row_count", 0),
        "excluded_rows_by_class": _excluded_counts(parsed),
        "rejected_rows": _rejected_payload(parsed),
    }
    if dry_run:
        # The verdict is projected against whatever state already exists, so a
        # dry run answers "would this file be accepted?" without storing it.
        readonly = _readonly(db_path)
        try:
            verdict = (
                _evaluate(
                    readonly, parsed,
                    dataset=dataset,
                    effective_date=effective,
                    observed_at_utc=observed_at,
                    thresholds=thresholds or thresholds_for_dataset(dataset),
                    override_validation=override_validation,
                )
                if readonly is not None
                else None
            )
        finally:
            if readonly is not None:
                readonly.close()
        return (
            base | {
                "snapshot_id": None,
                "created": False,
                "validation": verdict.as_dict() if verdict is not None else None,
            },
            parsed, None, None,
        )

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
            snapshot_root=snapshot_root, compression=compression,
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
            compression=compression,
            excluded=getattr(parsed, "excluded", ()),
        )
        # The snapshot and its blob are already durable: evidence of what the
        # exchange served is never discarded, whatever the guard decides.
        verdict = _guard(
            conn, parsed,
            dataset=dataset,
            snapshot_id=snapshot_id,
            effective_date=effective,
            observed_at_utc=observed_at,
            thresholds=thresholds or thresholds_for_dataset(dataset),
            override_validation=override_validation,
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
        "validation": verdict.as_dict(),
    }
    return payload, parsed, conn, run_id


def inspect_exchange_dataset(
    *,
    dataset: str,
    file: Path | None = None,
    url: str | None = None,
    result_period: str | None = None,
    sample_rows: int = 3,
    now_utc: Callable[[], str] = utc_now,
    fetcher: Callable[[str, str], FetchResponse] = fetch_exchange_dataset,
) -> dict[str, Any]:
    """Report the real shape of a published dataset. Stores nothing.

    A parser written against declared aliases is only as good as the aliases, and
    guessing at a report nobody has looked at is exactly how a wrong column
    silently becomes a company. This shows what an endpoint actually publishes —
    headers, row count and a few whole rows — so the alias map can be corrected
    from evidence before the dataset is wired for unattended refresh.
    """
    if dataset not in DATASET_KEYS:
        raise ValueError(f"Unknown exchange dataset: {dataset!r}")
    if file is None:
        url = resolve_url(dataset, url, result_period=result_period)
    response, acquisition_mode, source_label = _acquire(
        dataset=dataset, file=file, url=url, fetcher=fetcher
    )
    if response.fetch_status == "not_found":
        raise DatasetNotPublished(
            f"{dataset} has no published artifact at {source_label}: "
            f"status={response.status_code}"
        )
    if response.fetch_status != "ok":
        raise RuntimeError(
            "Exchange dataset rejected before inspection: "
            f"status={response.status_code} classification={response.fetch_status}"
        )
    payload, compression = decompress_source(response.content)
    base = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "dataset": dataset,
        "exchange": DATASET_EXCHANGE[dataset],
        "acquisition_mode": acquisition_mode,
        "source_label": source_label,
        "requested_url": url,
        "observed_at_utc": now_utc(),
        "served_byte_size": len(response.content),
        "content_sha256": hashlib.sha256(response.content).hexdigest(),
        "compression": compression,
        "decompressed_byte_size": len(payload),
        "is_json": is_json_payload(payload),
    }
    # Inspection binds no fields: the point is to see the columns as published,
    # including the ones no alias currently knows about.
    table = (
        read_json_records(payload, required={})
        if is_json_payload(payload)
        else read_delimited(payload, required={})
    )
    return base | {
        "encoding": table.encoding,
        "headers": list(table.headers),
        "row_count": len(table.rows),
        "sample_rows": [
            dict(zip(table.headers, row.cells))
            for row in table.rows[: max(sample_rows, 0)]
        ],
    }


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
    thresholds: GuardThresholds | None = None,
    override_validation: bool = False,
) -> dict[str, Any]:
    """Fold one dated security master or corporate-change file into the registry."""
    if dataset not in UNIVERSE_DATASETS:
        raise ValueError(
            f"{dataset!r} is not a universe dataset; use exchange-discover-results"
        )
    if file is None:
        url = resolve_url(dataset, url, result_period=None)
    payload, parsed, conn, run_id = _ingest(
        dataset=dataset, file=file, url=url, effective_date=effective_date,
        dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root,
        now_utc=now_utc, fetcher=fetcher, thresholds=thresholds,
        override_validation=override_validation,
    )
    if conn is None or run_id is None:
        return payload | {"registry": None}
    if payload["validation"]["status"] == "failed":
        # The file is kept as evidence; current state is left exactly as it was.
        finish_run(conn, run_id, "ok")
        conn.close()
        return payload | {"registry": None}
    try:
        if dataset in SECURITY_MASTER_DATASETS:
            registry = apply_security_master(
                conn, parsed,
                snapshot_id=int(payload["snapshot_id"]),
                effective_date=str(payload["effective_date"]),
                now_utc=str(payload["observed_at_utc"]),
                acquisition_mode=str(payload["acquisition_mode"]),
                authoritative_segments=authoritative_segments(dataset),
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
    thresholds: GuardThresholds | None = None,
    override_validation: bool = False,
) -> dict[str, Any]:
    """Ingest one official result feed and queue only advanced reporting periods."""
    if dataset not in RESULT_FILING_DATASETS:
        raise ValueError(
            f"{dataset!r} is not a result dataset; use exchange-refresh-universe"
        )
    if file is None:
        url = resolve_url(dataset, url, result_period=result_period)
    payload, parsed, conn, run_id = _ingest(
        dataset=dataset, file=file, url=url, effective_date=effective_date,
        dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root,
        now_utc=now_utc, fetcher=fetcher, result_period=result_period,
        thresholds=thresholds, override_validation=override_validation,
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
    if payload["validation"]["status"] == "failed":
        # A rejected feed queues nothing; the previous watermark still stands.
        finish_run(conn, run_id, "ok")
        conn.close()
        return payload | {"filings": None, "enqueue": {}}
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
