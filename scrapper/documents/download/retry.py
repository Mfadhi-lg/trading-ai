"""Generic download retry decisions and bounded publisher cooldown timing."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from .contracts import DownloadResult

def _retryable(result: DownloadResult) -> bool:
    if result.result_status in {"network_error", "timed_out", "rejected_unexpected_html"}:
        return True
    return result.result_status == "rejected_http_status" and (
        result.http_status in {401, 403, 408, 425, 429}
        or bool(result.http_status and result.http_status >= 500)
    )


def _blocks_host(result: DownloadResult) -> bool:
    return (
        result.result_status == "rejected_http_status"
        and result.http_status in {401, 403, 429}
    )


def _transport_host_failure(result: DownloadResult) -> bool:
    if result.error_code in {'redirect_limit_exceeded','TooManyRedirects'}:
        return False  # A local request-budget boundary is not a host outage.
    return result.result_status in {
        "network_error", "timed_out", "rejected_unexpected_html"
    } or (
        result.result_status == "rejected_http_status"
        and bool(result.http_status and result.http_status >= 500)
    )


def _iso_after(now_utc: str, seconds: int,
    *,
    datetime=datetime,
    timedelta=timedelta,
    timezone=timezone,
) -> str:
    parsed = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _retry_after_seconds(result: DownloadResult, *, now_utc: str,
    datetime=datetime,
    parsedate_to_datetime=parsedate_to_datetime,
    timezone=timezone,
) -> int | None:
    raw = result.response_headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = int(raw.strip())
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            seconds = int((target - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return max(60, min(24 * 60 * 60, seconds))


def _error_text(result: DownloadResult) -> str | None:
    if not result.error_code and not result.error_message:
        return None
    return ": ".join(
        value for value in (result.error_code, result.error_message) if value
    )
