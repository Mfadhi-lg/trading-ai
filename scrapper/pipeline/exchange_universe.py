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
from ..db.exchange.filings import (
    ingest_result_filings,
    preview_filing_decisions,
    enqueue_result_filings,
)
from ..db.exchange.securities import (
    absence_preview,
    apply_corporate_changes,
    apply_security_master,
)
from ..db.exchange.snapshots import (
    store_exchange_blob,
    record_exchange_fetch,
    persist_snapshot,
    latest_passed_snapshot,
    record_snapshot_validation,
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


from ..workflows.exchange.universe import DatasetNotPublished, ExchangeSourceAccessBlocked


UNIVERSE_DATASETS =SECURITY_MASTER_DATASETS + CORPORATE_CHANGE_DATASETS
ALL_DATASETS = UNIVERSE_DATASETS + RESULT_FILING_DATASETS
DATASET_EXCHANGE = {dataset: dataset_config(dataset).exchange for dataset in DATASET_KEYS}


def resolve_url(dataset: str, url: str | None, *, result_period: str | None) -> str | None:
    from ..workflows.exchange import universe as _owner
    return _owner.resolve_url(dataset, url, result_period=result_period, default_url_for=default_url_for)


def _acquire(
    *,
    dataset: str,
    file: Path | None,
    url: str | None,
    fetcher: Callable[[str, str], FetchResponse],
) -> tuple[FetchResponse, str, str]:
    from ..workflows.exchange import universe as _owner
    return _owner._acquire(dataset=dataset, file=file, url=url, fetcher=fetcher, DATASET_EXCHANGE=DATASET_EXCHANGE, FetchResponse=FetchResponse)


def _parse(
    dataset: str, content: bytes, *, result_period: str | None = None
) -> tuple[Any, str]:
    from ..workflows.exchange import universe as _owner
    return _owner._parse(dataset, content, result_period=result_period, CORPORATE_CHANGE_DATASETS=CORPORATE_CHANGE_DATASETS, SECURITY_MASTER_DATASETS=SECURITY_MASTER_DATASETS, decompress_source=decompress_source, parse_corporate_changes=parse_corporate_changes, parse_result_filings=parse_result_filings, parse_security_master=parse_security_master)


def _parser_name(dataset: str) -> str:
    from ..workflows.exchange import universe as _owner
    return _owner._parser_name(dataset, CORPORATE_CHANGE_DATASETS=CORPORATE_CHANGE_DATASETS, CORPORATE_CHANGE_PARSER_NAME=CORPORATE_CHANGE_PARSER_NAME, RESULT_FILING_PARSER_NAME=RESULT_FILING_PARSER_NAME, SECURITY_MASTER_DATASETS=SECURITY_MASTER_DATASETS, SECURITY_MASTER_PARSER_NAME=SECURITY_MASTER_PARSER_NAME)


def thresholds_for_dataset(dataset: str) -> GuardThresholds:
    from ..workflows.exchange import universe as _owner
    return _owner.thresholds_for_dataset(dataset, DATASET_KEYS=DATASET_KEYS, EXCHANGE_GUARD_ABSENCE_VOLUME_FLOOR=EXCHANGE_GUARD_ABSENCE_VOLUME_FLOOR, EXCHANGE_GUARD_ABSENCE_VOLUME_RATIO=EXCHANGE_GUARD_ABSENCE_VOLUME_RATIO, EXCHANGE_GUARD_DUPLICATE_ISIN_RATE=EXCHANGE_GUARD_DUPLICATE_ISIN_RATE, EXCHANGE_GUARD_REJECTED_ROW_RATE=EXCHANGE_GUARD_REJECTED_ROW_RATE, EXCHANGE_GUARD_ROW_COUNT_DROP_RATIO=EXCHANGE_GUARD_ROW_COUNT_DROP_RATIO, EXCHANGE_GUARD_ROW_FLOOR=EXCHANGE_GUARD_ROW_FLOOR, GuardThresholds=GuardThresholds, SECURITY_MASTER_DATASETS=SECURITY_MASTER_DATASETS, dataset_config=dataset_config)


def _isins(parsed: Any) -> list[str | None]:
    from ..workflows.exchange import universe as _owner
    return _owner._isins(parsed)


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
    from ..workflows.exchange import universe as _owner
    return _owner._evaluate(conn, parsed, dataset=dataset, effective_date=effective_date, observed_at_utc=observed_at_utc, thresholds=thresholds, override_validation=override_validation, exclude_snapshot_id=exclude_snapshot_id, SECURITY_MASTER_DATASETS=SECURITY_MASTER_DATASETS, SnapshotMetrics=SnapshotMetrics, _isins=_isins, absence_preview=absence_preview, authoritative_segments=authoritative_segments, evaluate_snapshot=evaluate_snapshot, isin_metrics=isin_metrics, latest_passed_snapshot=latest_passed_snapshot, override=override)


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
    from ..workflows.exchange import universe as _owner
    return _owner._guard(conn, parsed, dataset=dataset, snapshot_id=snapshot_id, effective_date=effective_date, observed_at_utc=observed_at_utc, thresholds=thresholds, override_validation=override_validation, _evaluate=_evaluate, record_snapshot_validation=record_snapshot_validation)


def _readonly(db_path: Path) -> sqlite3.Connection | None:
    from ..workflows.exchange import universe as _owner
    return _owner._readonly(db_path, sqlite3=sqlite3)


def _rejected_payload(parsed: Any) -> list[dict[str, Any]]:
    from ..workflows.exchange import universe as _owner
    return _owner._rejected_payload(parsed)


def _excluded_counts(parsed: Any) -> dict[str, int]:
    from ..workflows.exchange import universe as _owner
    return _owner._excluded_counts(parsed)


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
    from ..workflows.exchange import universe as _owner
    return _owner._ingest(dataset=dataset, file=file, url=url, effective_date=effective_date, dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, result_period=result_period, thresholds=thresholds, override_validation=override_validation, ALL_DATASETS=ALL_DATASETS, DATASET_EXCHANGE=DATASET_EXCHANGE, DatasetNotPublished=DatasetNotPublished, EXCHANGE_PARSER_VERSION=EXCHANGE_PARSER_VERSION, EXCHANGE_SCHEMA_VERSION=EXCHANGE_SCHEMA_VERSION, ExchangeSourceAccessBlocked=ExchangeSourceAccessBlocked, __version__=__version__, _acquire=_acquire, _evaluate=_evaluate, _excluded_counts=_excluded_counts, _guard=_guard, _parse=_parse, _parser_name=_parser_name, _readonly=_readonly, _rejected_payload=_rejected_payload, connect=connect, effective_date_or_today=effective_date_or_today, finish_run=finish_run, hashlib=hashlib, init_db=init_db, persist_snapshot=persist_snapshot, record_exchange_fetch=record_exchange_fetch, start_run=start_run, store_exchange_blob=store_exchange_blob, thresholds_for_dataset=thresholds_for_dataset)


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
    from ..workflows.exchange import universe as _owner
    return _owner.inspect_exchange_dataset(dataset=dataset, file=file, url=url, result_period=result_period, sample_rows=sample_rows, now_utc=now_utc, fetcher=fetcher, DATASET_EXCHANGE=DATASET_EXCHANGE, DATASET_KEYS=DATASET_KEYS, DatasetNotPublished=DatasetNotPublished, EXCHANGE_SCHEMA_VERSION=EXCHANGE_SCHEMA_VERSION, _acquire=_acquire, decompress_source=decompress_source, hashlib=hashlib, is_json_payload=is_json_payload, read_delimited=read_delimited, read_json_records=read_json_records, resolve_url=resolve_url)


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
    from ..workflows.exchange import universe as _owner
    return _owner.refresh_exchange_universe(dataset=dataset, file=file, url=url, effective_date=effective_date, dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, override_validation=override_validation, SECURITY_MASTER_DATASETS=SECURITY_MASTER_DATASETS, UNIVERSE_DATASETS=UNIVERSE_DATASETS, _ingest=_ingest, apply_corporate_changes=apply_corporate_changes, apply_security_master=apply_security_master, authoritative_segments=authoritative_segments, finish_run=finish_run, resolve_url=resolve_url)


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
    from ..workflows.exchange import universe as _owner
    return _owner.discover_exchange_results(dataset=dataset, file=file, url=url, effective_date=effective_date, result_period=result_period, dry_run=dry_run, db_path=db_path, snapshot_root=snapshot_root, now_utc=now_utc, fetcher=fetcher, thresholds=thresholds, override_validation=override_validation, RESULT_FILING_DATASETS=RESULT_FILING_DATASETS, _ingest=_ingest, _readonly=_readonly, enqueue_result_filings=enqueue_result_filings, finish_run=finish_run, ingest_result_filings=ingest_result_filings, preview_filing_decisions=preview_filing_decisions, resolve_url=resolve_url)
