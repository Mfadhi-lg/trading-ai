"""Named BSE/NSE, issuer-IR and debt recovery strategies with bounded orchestration."""

from __future__ import annotations

import os
import socket
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from ...config import DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS, THROTTLE_SECONDS
from ...core.http import (
    FetchResponse,
    fetch_exchange_recovery_surface,
    validate_exchange_dataset_url,
)
from ...db.documents.recovery.attempts import (
    RECOVERY_SOURCE_GROUPS,
    claim_recovery_target,
    finish_recovery_attempt,
)
from ...db.documents.recovery.candidates import (
    store_recovery_discovery_blob,
    persist_recovery_candidates,
    attach_recovery_candidate,
)
from ...db.repo import utc_now
from ..recovery_matching import match_candidate
from ...sources.documents.bse_recovery import BSECorporateRatingResolver
from ...sources.documents.debt_recovery import DebtIssueDocumentResolver
from ...sources.documents.issuer_ir_recovery import IssuerIRResolver
from ...sources.documents.nse_recovery import NSECorporateAnnouncementResolver
from ...sources.documents.recovery_common import ISSUE_DOCUMENT_TERMS


from ...db.documents.recovery.alternate_sources import (
    list_saved_issuer_pages, list_target_candidates,
)

def _exchange_observations(
    *,
    conn: Any,
    target: Any,
    source: str,
    discovered_at_utc: str,
    fetcher: Callable[..., FetchResponse],
    sleep: Callable[[float], None],
    throttle_seconds: float,
    discovery_snapshot_root: Path,
    _hooks: Any = None,
) -> tuple[list[tuple[Any, int]], dict[str, Any]]:
    Counter = getattr(_hooks, "Counter", globals()["Counter"])
    BSECorporateRatingResolver = getattr(_hooks, "BSECorporateRatingResolver", globals()["BSECorporateRatingResolver"])
    DebtIssueDocumentResolver = getattr(_hooks, "DebtIssueDocumentResolver", globals()["DebtIssueDocumentResolver"])
    ISSUE_DOCUMENT_TERMS = getattr(_hooks, "ISSUE_DOCUMENT_TERMS", globals()["ISSUE_DOCUMENT_TERMS"])
    NSECorporateAnnouncementResolver = getattr(_hooks, "NSECorporateAnnouncementResolver", globals()["NSECorporateAnnouncementResolver"])
    store_recovery_discovery_blob = getattr(_hooks, "store_recovery_discovery_blob", globals()["store_recovery_discovery_blob"])
    validate_exchange_dataset_url = getattr(_hooks, "validate_exchange_dataset_url", globals()["validate_exchange_dataset_url"])
    resolver = (
        NSECorporateAnnouncementResolver()
        if source == "nse"
        else BSECorporateRatingResolver()
    )
    exchange = source.upper()
    queries = resolver.build_queries(target)
    found: list[tuple[Any, int]] = []
    fetch_statuses: Counter[str] = Counter()
    for position, query in enumerate(queries):
        response = fetcher(query.url, exchange)
        fetch_statuses[response.fetch_status] += 1
        # Redirects may not move discovery onto an unreviewed or blocked host.
        validate_exchange_dataset_url(response.final_url, exchange)
        blob_id = store_recovery_discovery_blob(
            conn,
            content=response.content,
            source=source,
            requested_url=query.url,
            content_type=response.content_type or "application/octet-stream",
            observed_at_utc=discovered_at_utc,
            snapshot_root=discovery_snapshot_root,
        )
        if response.fetch_status == "rejected":
            raise ValueError(
                f"{exchange} official surface requires interaction or denied automation "
                f"(HTTP {response.status_code})"
            )
        if response.fetch_status == "error":
            raise RuntimeError(
                f"{exchange} official surface failed (HTTP {response.status_code})"
            )
        if response.fetch_status == "ok":
            ordinary = resolver.parse_response(
                response.content, query=query, discovered_at_utc=discovered_at_utc
            )
            issue = resolver.parse_response(
                response.content,
                query=query,
                discovered_at_utc=discovered_at_utc,
                terms=ISSUE_DOCUMENT_TERMS,
            )
            debt = DebtIssueDocumentResolver().select(issue, exchange=exchange)
            for observation in (*ordinary, *debt):
                found.append((observation, blob_id))
        if position + 1 < len(queries) and throttle_seconds:
            sleep(throttle_seconds)
    return found, {
        "query_count": len(queries),
        "fetch_status_counts": dict(sorted(fetch_statuses.items())),
    }


def _issuer_observations(
    conn: Any,
    *,
    target: Any,
    discovered_at_utc: str,
    _hooks: Any = None,
) -> tuple[list[tuple[Any, int]], dict[str, Any]]:
    Path = getattr(_hooks, "Path", globals()["Path"])
    IssuerIRResolver = getattr(_hooks, "IssuerIRResolver", globals()["IssuerIRResolver"])
    rows = list_saved_issuer_pages(conn, company_id=target.company_id)
    found: list[tuple[Any, int]] = []
    skipped = 0
    resolver = IssuerIRResolver()
    for row in rows:
        path = Path(str(row["local_path"]))
        if not path.is_file():
            skipped += 1
            continue
        try:
            observations = resolver.parse_saved_page(
                path.read_bytes(),
                discovery_page_url=str(row["discovery_page_url"]),
                company_id=target.company_id,
                discovered_at_utc=discovered_at_utc,
            )
        except ValueError:
            skipped += 1
            continue
        found.extend((observation, int(row["blob_id"])) for observation in observations)
    return found, {
        "saved_page_count": len(rows),
        "skipped_page_count": skipped,
        "network_request_count": 0,
    }


def _process_claim(
    conn: Any,
    *,
    target: Any,
    recovery_attempt_id: int,
    source: str,
    worker: str,
    fetcher: Callable[..., FetchResponse],
    now: Callable[[], str],
    sleep: Callable[[float], None],
    throttle_seconds: float,
    discovery_snapshot_root: Path,
    _hooks: Any = None,
) -> str:
    defaultdict = getattr(_hooks, "defaultdict", globals()["defaultdict"])
    _exchange_observations = getattr(_hooks, "_exchange_observations", globals()["_exchange_observations"])
    _issuer_observations = getattr(_hooks, "_issuer_observations", globals()["_issuer_observations"])
    attach_recovery_candidate = getattr(_hooks, "attach_recovery_candidate", globals()["attach_recovery_candidate"])
    finish_recovery_attempt = getattr(_hooks, "finish_recovery_attempt", globals()["finish_recovery_attempt"])
    match_candidate = getattr(_hooks, "match_candidate", globals()["match_candidate"])
    persist_recovery_candidates = getattr(_hooks, "persist_recovery_candidates", globals()["persist_recovery_candidates"])
    observed = now()
    if source in {"nse", "bse"}:
        observations, evidence = _exchange_observations(
            conn=conn,
            target=target,
            source=source,
            discovered_at_utc=observed,
            fetcher=fetcher,
            sleep=sleep,
            throttle_seconds=throttle_seconds,
            discovery_snapshot_root=discovery_snapshot_root,
        )
    else:
        observations, evidence = _issuer_observations(
            conn, target=target, discovered_at_utc=observed
        )
    grouped: dict[int, list[Any]] = defaultdict(list)
    for observation, blob_id in observations:
        grouped[blob_id].append(
            match_candidate(target, observation, discovered_at_utc=observed)
        )
    for blob_id, candidates in grouped.items():
        persist_recovery_candidates(
            conn,
            recovery_target_id=int(target.recovery_target_id),
            candidates=tuple(candidates),
            discovery_blob_id=blob_id,
            persisted_at_utc=observed,
        )
    rows = list_target_candidates(conn, recovery_target_id=target.recovery_target_id)
    eligible = [
        row for row in rows
        if row["confidence"] in {"EXACT", "HIGH"}
        and row["attached_source_id"] is None
    ]
    attached = None
    if len(eligible) == 1:
        attached = attach_recovery_candidate(
            conn, candidate_id=int(eligible[0]["candidate_id"]),
            attached_at_utc=observed,
        )
    ambiguous = any(row["confidence"] == "AMBIGUOUS" for row in rows)
    if attached is not None or any(row["attached_source_id"] for row in rows):
        status = "CANDIDATE_FOUND"
    elif ambiguous:
        status = "AMBIGUOUS"
    else:
        status = "NO_OFFICIAL_SOURCE_FOUND"
    finish_recovery_attempt(
        conn,
        recovery_attempt_id=recovery_attempt_id,
        worker=worker,
        status=status,
        completed_at_utc=now(),
        result_evidence={
            **evidence,
            "observation_count": len(observations),
            "persisted_candidate_count": len(rows),
            "attached_source_id": (
                attached["artifact_source_id"] if attached is not None else None
            ),
        },
    )
    return status


def run_document_recovery_worker(
    conn: Any,
    *,
    sources: Iterable[str],
    limit: int = 25,
    lease_seconds: int = 600,
    throttle_seconds: float = THROTTLE_SECONDS,
    worker: str | None = None,
    fetcher: Callable[..., FetchResponse] = fetch_exchange_recovery_surface,
    now: Callable[[], str] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    discovery_snapshot_root: Path = DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS,
    _hooks: Any = None,
) -> dict[str, Any]:
    """Discover a bounded, resumable batch from explicitly selected sources."""
    Counter = getattr(_hooks, "Counter", globals()["Counter"])
    os = getattr(_hooks, "os", globals()["os"])
    socket = getattr(_hooks, "socket", globals()["socket"])
    RECOVERY_SOURCE_GROUPS = getattr(_hooks, "RECOVERY_SOURCE_GROUPS", globals()["RECOVERY_SOURCE_GROUPS"])
    _process_claim = getattr(_hooks, "_process_claim", globals()["_process_claim"])
    claim_recovery_target = getattr(_hooks, "claim_recovery_target", globals()["claim_recovery_target"])
    finish_recovery_attempt = getattr(_hooks, "finish_recovery_attempt", globals()["finish_recovery_attempt"])

    selected = tuple(dict.fromkeys(source.strip().lower() for source in sources))
    if not selected or any(source not in RECOVERY_SOURCE_GROUPS for source in selected):
        raise ValueError("At least one explicit nse, bse, or issuer-ir source is required")
    if limit < 1 or lease_seconds < 1 or throttle_seconds < 0:
        raise ValueError("limit/lease must be positive and throttle cannot be negative")
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    outcomes: Counter[str] = Counter()
    processed = 0
    source_position = 0
    while processed < limit:
        claim = None
        claimed_source = None
        for offset in range(len(selected)):
            source = selected[(source_position + offset) % len(selected)]
            claim = claim_recovery_target(
                conn,
                source=source,
                worker=worker,
                now_utc=now(),
                lease_seconds=lease_seconds,
            )
            if claim is not None:
                claimed_source = source
                source_position = (source_position + offset + 1) % len(selected)
                break
        if claim is None or claimed_source is None:
            break
        target, attempt_id = claim
        try:
            status = _process_claim(
                conn,
                target=target,
                recovery_attempt_id=attempt_id,
                source=claimed_source,
                worker=worker,
                fetcher=fetcher,
                now=now,
                sleep=sleep,
                throttle_seconds=throttle_seconds,
                discovery_snapshot_root=discovery_snapshot_root,
            )
        except ValueError as exc:
            finish_recovery_attempt(
                conn,
                recovery_attempt_id=attempt_id,
                worker=worker,
                status="INTERACTION_REQUIRED",
                completed_at_utc=now(),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            status = "INTERACTION_REQUIRED"
        except Exception as exc:
            finish_recovery_attempt(
                conn,
                recovery_attempt_id=attempt_id,
                worker=worker,
                status="RETRYABLE_FAILURE",
                completed_at_utc=now(),
                error={"type": type(exc).__name__, "message": str(exc)},
                retry_seconds=900,
            )
            status = "RETRYABLE_FAILURE"
        outcomes[f"{claimed_source}:{status}"] += 1
        processed += 1
    return {
        "worker": worker,
        "sources": list(selected),
        "processed_target_source_count": processed,
        "outcome_counts": dict(sorted(outcomes.items())),
    }
