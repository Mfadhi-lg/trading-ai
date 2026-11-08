"""Persistence for the generic download queue's existing host-health state."""
from __future__ import annotations
import sqlite3
from ...documents.download.contracts import DownloadResult
from ...documents.download.retry import _iso_after, _retry_after_seconds

def _record_host_block(
    conn: sqlite3.Connection,
    *,
    host: str,
    result: DownloadResult,
    scope_id: str | None,
    now_utc: str,
    _iso_after=_iso_after,
    _retry_after_seconds=_retry_after_seconds,
) -> str:
    if result.http_status in {401, 403}:
        default_seconds = 24 * 60 * 60
    elif result.http_status == 429:
        default_seconds = 6 * 60 * 60
    else:
        default_seconds = 60 * 60
    seconds = _retry_after_seconds(result, now_utc=now_utc) or default_seconds
    blocked_until = _iso_after(now_utc, seconds)
    raw_retry_after = result.response_headers.get("retry-after")
    with conn:
        conn.execute(
            """INSERT INTO artifact_download_host_health
               (host, blocked_until_utc, last_http_status, consecutive_blocks,
                last_result_status, retry_after_raw, updated_at_utc)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(host) DO UPDATE SET
                 blocked_until_utc=excluded.blocked_until_utc,
                 last_http_status=excluded.last_http_status,
                 consecutive_blocks=artifact_download_host_health.consecutive_blocks+1,
                 last_result_status=excluded.last_result_status,
                 retry_after_raw=excluded.retry_after_raw,
                 updated_at_utc=excluded.updated_at_utc""",
            (
                host,
                blocked_until,
                result.http_status,
                result.result_status,
                raw_retry_after,
                now_utc,
            ),
        )
    return blocked_until


def _record_host_success(
    conn: sqlite3.Connection, *, host: str, result: DownloadResult, now_utc: str
) -> None:
    with conn:
        conn.execute(
            """UPDATE artifact_download_host_health
                  SET blocked_until_utc=NULL, last_http_status=?,
                      consecutive_blocks=0, last_result_status=?,
                      retry_after_raw=NULL, updated_at_utc=?
                WHERE host=?""",
            (result.http_status, result.result_status, now_utc, host),
        )


def _record_transport_host_failure(
    conn: sqlite3.Connection,
    *,
    host: str,
    result: DownloadResult,
    scope_id: str | None,
    now_utc: str,
    threshold: int = 3,
    _iso_after=_iso_after,
) -> str | None:
    """Open a bounded circuit only after repeated transport/server failures."""

    if threshold < 1:
        raise ValueError("Host failure threshold must be positive")
    current = conn.execute(
        "SELECT consecutive_blocks,last_result_status FROM artifact_download_host_health WHERE host=?",
        (host,),
    ).fetchone()
    previous = (
        int(current["consecutive_blocks"])
        if current is not None
        and str(current["last_result_status"] or "")
        in {
            "network_error", "timed_out", "rejected_http_status",
            "rejected_unexpected_html",
        }
        else 0
    )
    failures = previous + 1
    blocked_until = _iso_after(now_utc, 60 * 60) if failures >= threshold else None
    with conn:
        conn.execute(
            """INSERT INTO artifact_download_host_health
               (host,blocked_until_utc,last_http_status,consecutive_blocks,
                last_result_status,retry_after_raw,updated_at_utc)
               VALUES (?,?,?,?,?,NULL,?)
               ON CONFLICT(host) DO UPDATE SET
                 blocked_until_utc=excluded.blocked_until_utc,
                 last_http_status=excluded.last_http_status,
                 consecutive_blocks=excluded.consecutive_blocks,
                 last_result_status=excluded.last_result_status,
                 retry_after_raw=NULL,updated_at_utc=excluded.updated_at_utc""",
            (
                host,
                blocked_until,
                result.http_status,
                failures,
                result.result_status,
                now_utc,
            ),
        )
    return blocked_until
