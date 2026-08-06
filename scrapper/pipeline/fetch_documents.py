"""D02 batch orchestration for discovered document/media URLs."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

from ..config import (
    DB_PATH,
    DOCUMENT_CONTENT_SNAPSHOTS,
    DOCUMENT_DOWNLOAD_EXPORTS,
    THROTTLE_JITTER,
    THROTTLE_SECONDS,
)
from ..db.database import connect, init_db
from ..db.document_download_repo import persist_download_result
from ..db.repo import utc_now
from ..documents.downloader import ALLOWED_MEDIA_BY_ARTIFACT, DownloadPolicy, download_url


DOWNLOAD_REPORT_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class DownloadTarget:
    artifact_id: int
    artifact_type: str
    requested_url: str
    artifact_source_ids: tuple[int, ...]
    has_primary_source: bool


def _load_targets(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    artifact_types: Iterable[str] = (),
) -> tuple[DownloadTarget, ...]:
    requested_types = tuple(dict.fromkeys(artifact_types))
    unknown = set(requested_types) - set(ALLOWED_MEDIA_BY_ARTIFACT)
    if unknown:
        raise ValueError(f"Unsupported artifact types: {sorted(unknown)}")
    company = conn.execute(
        """SELECT company_id FROM security_identifiers
           WHERE identifier_type='NSE' AND identifier_value=? AND is_current=1""",
        (symbol.upper(),),
    ).fetchone()
    if company is None:
        raise ValueError(f"No discovered company found for NSE symbol {symbol.upper()}")
    parameters: list[Any] = [int(company["company_id"])]
    type_filter = ""
    if requested_types:
        placeholders = ",".join("?" for _ in requested_types)
        type_filter = f" AND artifact.artifact_type IN ({placeholders})"
        parameters.extend(requested_types)
    rows = conn.execute(
        f"""SELECT artifact.artifact_id, artifact.artifact_type,
                   source.artifact_source_id, source.document_url,
                   source.is_primary_source
            FROM document_artifacts AS artifact
            JOIN artifact_sources AS source USING (artifact_id)
            WHERE artifact.company_id=?
              AND source.availability_status NOT IN ('not_offered', 'not_applicable')
              {type_filter}
            ORDER BY artifact.artifact_id, source.document_url,
                     source.is_primary_source DESC, source.artifact_source_id""",
        parameters,
    ).fetchall()
    grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((int(row["artifact_id"]), row["document_url"]), []).append(row)
    targets = [
        DownloadTarget(
            artifact_id=artifact_id,
            artifact_type=group[0]["artifact_type"],
            requested_url=url,
            artifact_source_ids=tuple(int(row["artifact_source_id"]) for row in group),
            has_primary_source=any(bool(row["is_primary_source"]) for row in group),
        )
        for (artifact_id, url), group in grouped.items()
    ]
    # When distinct URLs return identical bytes, registering a primary path
    # first ensures the immutable content's official-source flag is conservative.
    return tuple(
        sorted(
            targets,
            key=lambda item: (
                item.artifact_id,
                not item.has_primary_source,
                item.requested_url,
            ),
        )
    )


def _default_output_path(symbol: str, fetched_at_utc: str) -> Path:
    safe_timestamp = fetched_at_utc.replace(":", "").replace("+", "_")
    return DOCUMENT_DOWNLOAD_EXPORTS / symbol.upper() / f"{safe_timestamp}.json"


def fetch_documents(
    *,
    symbol: str,
    artifact_types: Iterable[str] = (),
    db_path: Path = DB_PATH,
    output: Path | None = None,
    policy: DownloadPolicy | None = None,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    session_factory: Callable[[], Any] = requests.Session,
    now_utc: Callable[[], str] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    throttle_seconds: float = THROTTLE_SECONDS,
    throttle_jitter: float = THROTTLE_JITTER,
    random_uniform: Callable[[float, float], float] = random.uniform,
) -> dict[str, Any]:
    """Fetch all matching discovered URLs, once per artifact/URL pair.

    A client and rate limiter are maintained independently per host. A 401, 403,
    429, or unexpected HTML response stops further requests to that host for the
    run rather than attempting to evade the block.
    """

    if throttle_seconds < 0 or throttle_jitter < 0:
        raise ValueError("Throttle values cannot be negative")
    policy = policy or DownloadPolicy()
    artifact_types = tuple(artifact_types)
    conn = connect(db_path)
    sessions: dict[str, Any] = {}
    last_request_at: dict[str, float] = {}
    blocked_hosts: dict[str, str] = {}
    attempts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    first_fetch_time: str | None = None
    last_fetch_time: str | None = None
    try:
        init_db(conn)
        targets = _load_targets(
            conn, symbol=symbol, artifact_types=artifact_types
        )
        for target in targets:
            parsed = urlsplit(target.requested_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Unsupported document URL: {target.requested_url!r}")
            host = parsed.hostname.lower()
            if host in blocked_hosts:
                skipped.append(
                    {
                        "artifact_id": target.artifact_id,
                        "artifact_source_ids": list(target.artifact_source_ids),
                        "requested_url": target.requested_url,
                        "result_status": "skipped_host_blocked",
                        "reason": blocked_hosts[host],
                    }
                )
                continue
            previous = last_request_at.get(host)
            if previous is not None:
                required_delay = throttle_seconds + random_uniform(0, throttle_jitter)
                remaining = required_delay - (monotonic() - previous)
                if remaining > 0:
                    sleep(remaining)
            session = sessions.setdefault(host, session_factory())
            started_at = now_utc()
            first_fetch_time = first_fetch_time or started_at
            result = download_url(
                target.requested_url,
                artifact_type=target.artifact_type,
                policy=policy,
                session=session,
                monotonic=monotonic,
            )
            last_request_at[host] = monotonic()
            fetched_at = now_utc()
            last_fetch_time = fetched_at
            persisted = persist_download_result(
                conn,
                artifact_id=target.artifact_id,
                artifact_source_ids=target.artifact_source_ids,
                result=result,
                policy=policy,
                started_at_utc=started_at,
                fetched_at_utc=fetched_at,
                snapshot_root=snapshot_root,
            )
            attempts.append(persisted.to_dict())
            if (
                result.result_status == "rejected_unexpected_html"
                or result.result_status == "rejected_http_status"
                and result.http_status in {401, 403, 429}
            ):
                blocked_hosts[host] = result.result_status
        status_counts = Counter(row["result_status"] for row in attempts)
        summary = {
            "target_url_count": len(targets),
            "attempted_url_count": len(attempts),
            "skipped_url_count": len(skipped),
            "validated_url_count": status_counts.get("validated", 0),
            "rejected_url_count": len(attempts) - status_counts.get("validated", 0),
            "unique_content_count": len(
                {row["content_id"] for row in attempts if row["content_id"] is not None}
            ),
            "status_counts": dict(sorted(status_counts.items())),
        }
        payload: dict[str, Any] = {
            "schema_version": DOWNLOAD_REPORT_SCHEMA_VERSION,
            "symbol": symbol.upper(),
            "artifact_types": list(dict.fromkeys(artifact_types)),
            "started_at_utc": first_fetch_time,
            "completed_at_utc": last_fetch_time,
            "limits": policy.to_dict(),
            "summary": summary,
            "attempts": attempts,
            "skipped": skipped,
        }
        output_path = output or _default_output_path(
            symbol, last_fetch_time or now_utc()
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        payload["output"] = str(output_path.resolve())
        return payload
    finally:
        conn.close()
        for session in sessions.values():
            close = getattr(session, "close", None)
            if close is not None:
                close()
