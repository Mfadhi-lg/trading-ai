"""Authenticated result discovery and bounded incremental company workers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .. import __version__
from ..config import (
    DB_PATH,
    EXPORTS,
    RESULT_SCREEN_PARSER_NAME,
    RESULT_SCREEN_PARSER_VERSION,
    SCREENER_BASE,
    SNAPSHOTS,
)
from ..core.log import get_logger, log_event
from ..core.http import FetchResponse, ScreenerHostClient, fetch_screener_result_screen
from ..db.database import connect, init_db
from ..db.repo import finish_run, record_fetch, start_run, store_blob, utc_now
from ..db.screener_discovery_repo import (
    ClaimedCompany,
    claim_due_companies,
    complete_company,
    enqueue_screen_candidates,
    fail_company,
    mark_running,
    persist_screen,
    persist_rejected_screen,
    preview_due_companies,
    preview_incomplete_metric_candidates,
    preview_requeue_candidates,
    release_claims,
    requeue_failed_companies,
    requeue_incomplete_metric_companies,
    skip_if_already_accepted,
)
from ..sources.screener.result_screen import canonical_query, parse_result_screen
from .screener_vertical import VerticalSliceResult, scrape_screener_company_path


DISCOVERY_SCHEMA_VERSION = "1.0.0"
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def result_screen_url(query: str) -> str:
    return f"{SCREENER_BASE}/screen/raw/?{urlencode({'query': canonical_query(query)})}"


def _candidate_payload(parsed: Any) -> list[dict[str, Any]]:
    return [
        {
            "source_position": row.source_position,
            "screen_row_company_id": row.screen_row_company_id,
            "company_name_raw": row.company_name_raw,
            "company_href_raw": row.company_href_raw,
            "company_path_normalized": row.company_path_normalized,
            "reporting_scope_hint": row.reporting_scope_hint,
            "last_result_date_display": row.last_result_date_display,
            "result_period": row.result_period,
        }
        for row in parsed.rows
    ]


def discover_screener_results(
    *,
    query_role: str,
    query: str,
    fixture: Path | None = None,
    dry_run: bool = False,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    now_utc: Callable[[], str] = utc_now,
    fetcher: Callable[[str], FetchResponse] = fetch_screener_result_screen,
) -> dict[str, Any]:
    """Parse an offline fixture or perform one permitted first-page fetch."""
    if not _ROLE_RE.fullmatch(query_role):
        raise ValueError("query_role must be a lower-case configuration identifier")
    canonical = canonical_query(query)
    requested_url = result_screen_url(canonical)
    observed_at = now_utc()
    if fixture is not None:
        content = fixture.read_bytes()
        response = FetchResponse(
            requested_url=requested_url,
            final_url=requested_url,
            status_code=200,
            content=content,
            content_type="text/html",
            encoding="utf-8",
            etag=None,
            last_modified=None,
            auth_mode="operator_fixture",
            fetch_status="ok",
        )
    else:
        response = fetcher(requested_url)
    if dry_run:
        if response.fetch_status != "ok":
            raise RuntimeError(
                "Result screen rejected before parsing: "
                f"status={response.status_code} classification={response.fetch_status}"
            )
        parsed = parse_result_screen(response.content, canonical, requested_url)
        return {
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "mode": "dry_run",
            "query_role": query_role,
            "query_raw": canonical,
            "requested_url": requested_url,
            "content_sha256": hashlib.sha256(response.content).hexdigest(),
            "observed_at_utc": observed_at,
            "result_count": parsed.result_count,
            "page_number": parsed.page_number,
            "page_count": parsed.page_count,
            "captured_row_count": parsed.captured_row_count,
            "coverage_status": parsed.coverage_status,
            "is_complete_capture": parsed.is_complete_capture,
            "enqueue_allowed": parsed.is_complete_capture,
            "candidates": _candidate_payload(parsed),
            "screen_run_id": None,
            "created": False,
            "enqueue": None,
        }

    parsed = None
    base = {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "mode": "dry_run" if dry_run else "persisted",
        "query_role": query_role,
        "query_raw": canonical,
        "requested_url": requested_url,
        "content_sha256": hashlib.sha256(response.content).hexdigest(),
        "observed_at_utc": observed_at,
    }

    conn = connect(db_path)
    init_db(conn)
    run_id = start_run(
        conn,
        __version__,
        {"source": "screener", "mode": "result_discovery", "query_role": query_role, "query": canonical},
    )
    try:
        blob_id = store_blob(
            conn,
            response,
            "_RESULT_DISCOVERY",
            query_role,
            observed_at,
            snapshot_root,
            resource_kind="result_screen",
        )
        fetch_id = record_fetch(
            conn,
            run_id,
            None,
            response,
            "not_applicable",
            observed_at,
            blob_id,
            resource_kind="result_screen",
        )
        if response.fetch_status != "ok":
            persist_rejected_screen(
                conn,
                query_role=query_role,
                query_raw=canonical,
                requested_url=requested_url,
                fetch_id=fetch_id,
                blob_id=blob_id,
                observed_at_utc=observed_at,
                parser_name=RESULT_SCREEN_PARSER_NAME,
                parser_version=RESULT_SCREEN_PARSER_VERSION,
            )
            raise RuntimeError(
                "Result screen rejected before parsing: "
                f"status={response.status_code} classification={response.fetch_status}"
            )
        try:
            parsed = parse_result_screen(response.content, canonical, requested_url)
        except Exception:
            persist_rejected_screen(
                conn,
                query_role=query_role,
                query_raw=canonical,
                requested_url=requested_url,
                fetch_id=fetch_id,
                blob_id=blob_id,
                observed_at_utc=observed_at,
                parser_name=RESULT_SCREEN_PARSER_NAME,
                parser_version=RESULT_SCREEN_PARSER_VERSION,
            )
            raise
        screen_run_id, created = persist_screen(
            conn,
            parsed,
            query_role=query_role,
            fetch_id=fetch_id,
            blob_id=blob_id,
            observed_at_utc=observed_at,
            parser_name=RESULT_SCREEN_PARSER_NAME,
            parser_version=RESULT_SCREEN_PARSER_VERSION,
        )
        enqueue = enqueue_screen_candidates(conn, screen_run_id, now_utc=observed_at)
        finish_run(conn, run_id, "ok")
        return {
            **base,
            "result_count": parsed.result_count,
            "page_number": parsed.page_number,
            "page_count": parsed.page_count,
            "captured_row_count": parsed.captured_row_count,
            "coverage_status": parsed.coverage_status,
            "is_complete_capture": parsed.is_complete_capture,
            "enqueue_allowed": parsed.is_complete_capture,
            "candidates": _candidate_payload(parsed),
            "run_id": run_id,
            "fetch_id": fetch_id,
            "blob_id": blob_id,
            "screen_run_id": screen_run_id,
            "created": created,
            "enqueue": enqueue,
        }
    except Exception:
        finish_run(conn, run_id, "error")
        raise
    finally:
        conn.close()


def _safe_error(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    message = re.sub(
        r"(?i)(sessionid|csrftoken|cookie)\s*[=:]\s*[^\s;,]+",
        r"\1=<redacted>",
        message,
    )
    return {"type": type(exc).__name__, "message": message[:2000],
            **{name: getattr(exc, name) for name in ("market_cap_crore", "floor_crore", "blob_id") if hasattr(exc, name)}}


def _failure_policy(exc: Exception, origin: str = "screener_screen") -> tuple[bool, bool]:
    message = str(exc).lower()
    stop_host = any(
        marker in message
        for marker in ("status=401", "status=403", "status=429", "captcha", "login", "access-denied")
    )
    terminal_markers = [
        "status=404",
        "description is missing",
        "profile carries no bse or nse identifier",
        "headers are missing",
        "table has no observations",
        "not above screener market-cap scope floor",
        "below screener market-cap scope floor",
        "screen result period is absent",
        "reporting scope differs",
        "identity",
        "manifest",
        "failed artifact",
    ]
    if origin == "exchange_filing":
        # An exchange has published the filing but Screener may not have loaded
        # the quarter yet. That lag is expected and must stay retryable.
        terminal_markers.remove("screen result period is absent")
    terminal = any(marker in message for marker in terminal_markers)
    return not terminal, stop_host


def requeue_failed_screener_companies(
    *,
    statuses: tuple[str, ...] = ("failed_terminal",),
    paths: tuple[str, ...] = (),
    limit: int | None = None,
    dry_run: bool = False,
    reason: str = "operator_requeue",
    db_path: Path = DB_PATH,
    now_utc: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Move settled failures back to `pending` after a parser or rule fix.

    `failed_terminal` items are never picked up by the worker again on their
    own, so a fix that makes them collectable needs an explicit requeue. The
    report names every item and the error it last failed with.
    """
    checked_at = now_utc()
    if dry_run:
        items: tuple[dict[str, Any], ...] = ()
        if db_path.exists():
            readonly = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
            readonly.row_factory = sqlite3.Row
            try:
                items = preview_requeue_candidates(
                    readonly, statuses=statuses, paths=paths, limit=limit
                )
            finally:
                readonly.close()
    else:
        conn = connect(db_path)
        try:
            init_db(conn)
            items = requeue_failed_companies(
                conn,
                now_utc=checked_at,
                statuses=statuses,
                paths=paths,
                limit=limit,
                reason=reason,
            )
        finally:
            conn.close()
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "mode": "dry_run" if dry_run else "execute",
        "checked_at_utc": checked_at,
        "requested_statuses": list(statuses),
        "summary": {
            "requeued_count": len(items),
            "by_status": dict(sorted(Counter(item["status"] for item in items).items())),
            "by_error_type": dict(
                sorted(
                    Counter(
                        str(item["last_error"].get("type") or "none") for item in items
                    ).items()
                )
            ),
        },
        "items": list(items),
    }


def requeue_incomplete_metric_screener_companies(
    *,
    paths: tuple[str, ...] = (),
    limit: int | None = None,
    dry_run: bool = False,
    reason: str = "quick_ratios_backfill",
    db_path: Path = DB_PATH,
    now_utc: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Requeue acceptances whose run predates the custom ratio panel fetch.

    Those companies hold only Screener's nine public ratios and the queue will
    never revisit them on its own, because acceptance is keyed to a result
    period that has not advanced. The report names every company and the metric
    count its current acceptance carries.
    """
    checked_at = now_utc()
    if dry_run:
        items: tuple[dict[str, Any], ...] = ()
        if db_path.exists():
            readonly = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
            readonly.row_factory = sqlite3.Row
            try:
                items = preview_incomplete_metric_candidates(
                    readonly, paths=paths, limit=limit
                )
            finally:
                readonly.close()
    else:
        conn = connect(db_path)
        try:
            init_db(conn)
            items = requeue_incomplete_metric_companies(
                conn, now_utc=checked_at, paths=paths, limit=limit, reason=reason
            )
        finally:
            conn.close()
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "mode": "dry_run" if dry_run else "execute",
        "checked_at_utc": checked_at,
        "missing_resource_kind": "quick_ratios",
        "summary": {
            "requeued_count": len(items),
            "by_metric_count": dict(
                sorted(Counter(int(item["metric_count"]) for item in items).items())
            ),
        },
        "items": list(items),
    }


def run_screener_company_worker(
    *,
    due: bool,
    limit: int,
    dry_run: bool = False,
    db_path: Path = DB_PATH,
    snapshot_root: Path = SNAPSHOTS,
    export_root: Path = EXPORTS,
    worker_id: str | None = None,
    now_utc: Callable[[], str] = utc_now,
    scrape: Callable[..., VerticalSliceResult] = scrape_screener_company_path,
    host_client: ScreenerHostClient | None = None,
) -> dict[str, Any]:
    if not due:
        raise ValueError("Screener company worker currently requires --due")
    if limit < 1:
        raise ValueError("limit must be positive")
    checked_at = now_utc()
    worker_id = worker_id or f"screener-worker-{uuid.uuid4().hex}"
    logger = get_logger(__name__)
    if dry_run:
        due_rows: tuple[ClaimedCompany, ...] = ()
        if db_path.exists():
            readonly = sqlite3.connect(
                f"file:{db_path.resolve()}?mode=ro", uri=True
            )
            readonly.row_factory = sqlite3.Row
            try:
                due_rows = preview_due_companies(
                    readonly, now_utc=checked_at, limit=limit
                )
            finally:
                readonly.close()
        return {
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "mode": "dry_run",
            "worker_id": None,
            "checked_at_utc": checked_at,
            "summary": {
                "due_count": len(due_rows),
                "claimed_count": 0,
                "outcome_counts": {},
            },
            "due": [row.to_dict() | {"claim_token": None} for row in due_rows],
            "outcomes": [],
        }
    conn = connect(db_path)
    claimed: tuple[ClaimedCompany, ...] = ()
    completed: set[int] = set()
    outcomes: list[dict[str, Any]] = []
    try:
        init_db(conn)
        if scrape is scrape_screener_company_path and host_client is None:
            host_client = ScreenerHostClient()
        claimed = claim_due_companies(
            conn, now_utc=checked_at, limit=limit, worker_id=worker_id
        )
        log_event(logger, "screener_queue_claimed", worker_id=worker_id,
                  claimed_count=len(claimed), due_at_utc=checked_at)
        stop_host = False
        for position, item in enumerate(claimed):
            if stop_host:
                break
            started_at = now_utc()
            if skip_if_already_accepted(
                conn, item, worker_id=worker_id, now_utc=started_at
            ):
                completed.add(item.queue_item_id)
                outcomes.append(
                    {"queue_item_id": item.queue_item_id, "status": "skipped_already_accepted"}
                )
                log_event(logger, "screener_queue_skipped", worker_id=worker_id,
                          source="screener", host="www.screener.in",
                          queue_item_id=item.queue_item_id,
                          reporting_scope=item.reporting_scope,
                          attempt=item.attempt_count + 1,
                          rejection_code="already_accepted")
                continue
            mark_running(conn, item, worker_id=worker_id, now_utc=started_at)
            try:
                result = scrape(
                    item.company_path_normalized,
                    item.reporting_scope,
                    expected_result_period=item.result_period,
                    db_path=db_path,
                    snapshot_root=snapshot_root,
                    export_root=export_root,
                    **({"host_client": host_client} if host_client is not None else {}),
                )
                complete_company(
                    conn,
                    item,
                    parse_id=result.parse_id,
                    company_id=result.company_id,
                    export_path=str(result.export_path.resolve()),
                    worker_id=worker_id,
                    now_utc=now_utc(),
                )
                completed.add(item.queue_item_id)
                outcomes.append(
                    {
                        "queue_item_id": item.queue_item_id,
                        "status": "accepted",
                        "parse_id": result.parse_id,
                        "company_id": result.company_id,
                        "export_path": str(result.export_path.resolve()),
                    }
                )
                log_event(logger, "screener_queue_accepted", worker_id=worker_id,
                          source="screener", host="www.screener.in",
                          queue_item_id=item.queue_item_id, company_id=result.company_id,
                          reporting_scope=item.reporting_scope,
                          attempt=item.attempt_count + 1)
            except Exception as exc:
                retryable, stop_host = _failure_policy(exc, item.origin)
                error = _safe_error(exc)
                fail_company(
                    conn,
                    item,
                    worker_id=worker_id,
                    now_utc=now_utc(),
                    error=error,
                    retryable=retryable,
                    stop_host=stop_host,
                )
                completed.add(item.queue_item_id)
                outcomes.append(
                    {
                        "queue_item_id": item.queue_item_id,
                        "status": "failed_retryable" if retryable else "failed_terminal",
                        "stop_host": stop_host,
                        "error": error,
                    }
                )
                log_event(logger, "screener_queue_rejected", worker_id=worker_id,
                          source="screener", host="www.screener.in",
                          queue_item_id=item.queue_item_id,
                          reporting_scope=item.reporting_scope,
                          attempt=item.attempt_count + 1, retryable=retryable,
                          retry_reason=error.get("type"),
                          policy_rejection=("standalone" in error.get("message", "").lower()))
    finally:
        remaining = tuple(item for item in claimed if item.queue_item_id not in completed)
        if remaining:
            release_claims(conn, remaining, worker_id=worker_id, now_utc=now_utc())
        conn.close()
    counts = Counter(item["status"] for item in outcomes)
    log_event(logger, "screener_queue_throughput", worker_id=worker_id,
              source="screener", host="www.screener.in",
              claimed_count=len(claimed), completed_count=len(outcomes),
              accepted_count=counts.get("accepted", 0), rejected_count=len(outcomes) - counts.get("accepted", 0))
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "mode": "execute",
        "worker_id": worker_id,
        "checked_at_utc": checked_at,
        "summary": {
            "due_count": len(claimed),
            "claimed_count": len(claimed),
            "completed_count": len(outcomes),
            "outcome_counts": dict(sorted(counts.items())),
        },
        "claimed": [item.to_dict() | {"claim_token": "<redacted>"} for item in claimed],
        "outcomes": outcomes,
    }
