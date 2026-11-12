"""Retry diagnosis classification and aggregation; no queue policy mutation."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from collections import Counter, defaultdict

from ...db.documents.recovery.reports import retryable_jobs, retryable_host_recency

# Ordered: the first pattern that matches a job's evidence wins.
_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("HOST_CIRCUIT_BREAKER", re.compile(r"circuit open", re.I)),
    ("HTTP_429_THROTTLING", re.compile(r"\b429\b|too many requests", re.I)),
    ("HTTP_403_FORBIDDEN", re.compile(r"\b403\b|forbidden", re.I)),
    ("HTTP_404_NOT_FOUND", re.compile(r"\b404\b|not found", re.I)),
    ("HTTP_5XX", re.compile(r"\b5\d{2}\b|server error", re.I)),
    ("TIMEOUT", re.compile(r"timed?[_ ]?out|timeout|ReadTimeout", re.I)),
    ("CONNECTION_RESET", re.compile(r"reset by peer|ConnectionReset", re.I)),
    ("TLS_ERROR", re.compile(r"SSLError|CERTIFICATE|TLS", re.I)),
    ("DNS_OR_NETWORK", re.compile(r"NameResolution|getaddrinfo|ConnectionError|Max retries", re.I)),
    ("UNEXPECTED_HTML", re.compile(r"html_response|unexpected_html", re.I)),
    ("INVALID_MEDIA_OR_SIGNATURE", re.compile(r"unknown_signature|declared_media_type|artifact_type", re.I)),
    ("TOO_LARGE", re.compile(r"too_large", re.I)),
    ("LEASE_EXPIRED", re.compile(r"LEASE_EXPIRED", re.I)),
]


def _family(last_result_status: str | None, last_error: str | None) -> str:
    blob = f"{last_result_status or ''} {last_error or ''}"
    for name, pattern in _FAMILIES:
        if pattern.search(blob):
            return name
    if last_result_status == "network_error":
        return "DNS_OR_NETWORK"
    return "OTHER"


def _host(url: str) -> str:
    match = re.match(r"[a-z]+://([^/]+)", url or "", re.I)
    return match.group(1).lower() if match else "(unparsed)"


def _verdict(
    *,
    total: int,
    never_attempted: int,
    dominant_family: str,
    dominant_family_n: int,
    dominant_host: str,
    host_detail: dict[str, dict],
    by_family_attempted: Counter[str],
) -> dict:
    """DA-FINAL-OPS §6: distinguish a deterministic code defect from normal source failure.

    A retryable job that was never dispatched (attempt_count = 0) consumed no retry
    budget and proves nothing about the source or the adapter — it only records that the
    host breaker was open when the job was polled. Those cannot evidence a code defect.
    Only jobs that actually issued a request can.
    """

    reasons: list[str] = []
    detail = host_detail.get(dominant_host, {})
    breaker_dominated = dominant_family == "HOST_CIRCUIT_BREAKER"
    host_healthy = bool(detail) and not detail.get("circuit_open_now", True) and (
        detail.get("validated_all_time", 0) > 0
    )

    if breaker_dominated:
        reasons.append(
            f"{dominant_family_n:,}/{total:,} jobs are host-circuit bounce-backs with "
            "attempt_count = 0: no request was issued and no retry budget was consumed."
        )
    if host_healthy:
        reasons.append(
            f"Dominant host {dominant_host} has its breaker CLOSED and "
            f"{detail.get('validated_all_time', 0):,}/{detail.get('attempts_all_time', 0):,} "
            f"validated (latest success {detail.get('latest_success_utc')}); it is draining normally."
        )
    attempted_total = sum(by_family_attempted.values())
    if attempted_total:
        spread = ", ".join(f"{k}={v}" for k, v in by_family_attempted.most_common(5))
        reasons.append(
            f"Only {attempted_total:,} jobs actually issued a request, spread across "
            f"normal transient families ({spread})."
        )

    code_defect_suspected = not (breaker_dominated and host_healthy)
    return {
        "classification": (
            "NORMAL_SOURCE_FAILURE_AND_HOST_THROTTLING"
            if not code_defect_suspected
            else "REQUIRES_INVESTIGATION"
        ),
        "code_change_required": code_defect_suspected,
        "action": (
            "No code change. Allow normal retry/backoff scheduling to continue draining."
            if not code_defect_suspected
            else "Investigate: a deterministic cause may dominate attempted jobs."
        ),
        "reasons": reasons,
    }


def diagnose(db_path: str, scope_id: str | None, *, _hooks=None) -> dict:
    dt = getattr(_hooks, "dt", globals()["dt"])
    sqlite3 = getattr(_hooks, "sqlite3", globals()["sqlite3"])
    _family = getattr(_hooks, "_family", globals()["_family"])
    _host = getattr(_hooks, "_host", globals()["_host"])
    _verdict = getattr(_hooks, "_verdict", globals()["_verdict"])
    from ...db.database import connect, selected_backend
    if selected_backend() == 'postgresql':
        conn = connect(db_path, read_only=True)
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    rows = retryable_jobs(conn, scope_id=scope_id)

    by_family: Counter[str] = Counter()
    by_family_attempted: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_attempt: Counter[int] = Counter()
    by_type: Counter[str] = Counter()
    by_host: Counter[str] = Counter()
    host_family: dict[str, Counter[str]] = defaultdict(Counter)
    host_attempt: dict[str, Counter[int]] = defaultdict(Counter)
    due_now = 0

    for row in rows:
        host = (row["target_host"] or _host(row["requested_url"])).lower()
        family = _family(row["last_result_status"], row["last_error"])
        by_family[family] += 1
        if (row["attempt_count"] or 0) > 0:
            by_family_attempted[family] += 1
        by_status[row["last_result_status"] or "(none)"] += 1
        by_attempt[row["attempt_count"] or 0] += 1
        by_type[row["artifact_type"]] += 1
        by_host[host] += 1
        host_family[host][family] += 1
        host_attempt[host][row["attempt_count"] or 0] += 1
        if (row["next_attempt_at_utc"] or "") <= now_iso:
            due_now += 1

    # Per-host success/failure recency comes from real attempt history.
    host_recency: dict[str, dict] = {}
    for host, _count in by_host.most_common(40):
        like = f"%//{host}/%"
        rec, health = retryable_host_recency(conn, like=like, host=host)
        host_recency[host] = {
            "retryable_jobs": by_host[host],
            "attempts_all_time": rec["attempts"] or 0,
            "validated_all_time": rec["validated"] or 0,
            "latest_success_utc": rec["latest_success"],
            "latest_failure_utc": rec["latest_failure"],
            "circuit_open_until_utc": health["blocked_until_utc"] if health else None,
            "circuit_open_now": bool(
                health and (health["blocked_until_utc"] or "") > now_iso
            ),
            "consecutive_blocks": health["consecutive_blocks"] if health else 0,
            "families": dict(host_family[host].most_common()),
            "attempt_counts": {str(k): v for k, v in sorted(host_attempt[host].items())},
        }

    total = len(rows)
    never_attempted = by_attempt.get(0, 0)
    dominant_host, dominant_host_n = (by_host.most_common(1) or [("", 0)])[0]
    dominant_family, dominant_family_n = (by_family.most_common(1) or [("", 0)])[0]

    verdict = _verdict(
        total=total,
        never_attempted=never_attempted,
        dominant_family=dominant_family,
        dominant_family_n=dominant_family_n,
        dominant_host=dominant_host,
        host_detail=host_recency,
        by_family_attempted=by_family_attempted,
    )

    return {
        "verdict": verdict,
        "generated_at_utc": now.isoformat(),
        "db_path": db_path,
        "scope_id": scope_id,
        "total_retryable": total,
        "due_now": due_now,
        "scheduled_future": total - due_now,
        "never_attempted": never_attempted,
        "attempted_at_least_once": total - never_attempted,
        "by_failure_family": dict(by_family.most_common()),
        "by_failure_family_attempted_only": dict(by_family_attempted.most_common()),
        "by_last_result_status": dict(by_status.most_common()),
        "by_attempt_count": {str(k): v for k, v in sorted(by_attempt.items())},
        "by_artifact_type": dict(by_type.most_common()),
        "by_host_top": dict(by_host.most_common(40)),
        "host_detail": host_recency,
        "dominant_host": {"host": dominant_host, "jobs": dominant_host_n,
                          "share": round(dominant_host_n / total, 4) if total else 0.0},
        "dominant_family": {"family": dominant_family, "jobs": dominant_family_n,
                            "share": round(dominant_family_n / total, 4) if total else 0.0},
    }


