"""Event and expectation registration persistence; no due-worker scheduling."""
from __future__ import annotations
import json
import sqlite3
from typing import Any, Iterable
from ...config import SCREENER_MIN_MARKET_CAP_CRORE
from ...documents.availability import RetryPolicy, parse_utc
RESULT_ARTIFACT_TYPES = ("presentation", "transcript", "recording", "credit_rating")
DELAYED_SCREENER_RESULT_TYPES = {"presentation", "transcript"}
def screener_market_cap_above_floor(
    conn: sqlite3.Connection, company_id: int,
    *,
    SCREENER_MIN_MARKET_CAP_CRORE=SCREENER_MIN_MARKET_CAP_CRORE,
) -> bool:
    """Use the latest accepted standalone Screener market cap for scope."""
    scope = conn.execute("SELECT eligibility FROM screener_collection_scope WHERE company_id=? ORDER BY observed_at_utc DESC LIMIT 1", (company_id,)).fetchone()
    if scope is not None:
        return scope[0] == "eligible"
    row = conn.execute(
        """SELECT metric.value_decimal
             FROM accepted_page_versions AS accepted
             JOIN company_metric_observations AS metric
               ON metric.parse_id=accepted.parse_id
            WHERE accepted.company_id=?
              AND accepted.source='screener'
              AND accepted.reporting_scope='standalone'
              AND metric.metric_code='market_cap'
              AND metric.value_decimal IS NOT NULL
            ORDER BY accepted.accepted_at_utc DESC, accepted.parse_id DESC
            LIMIT 1""",
        (company_id,),
    ).fetchone()
    if row is None:
        return False
    try:
        return float(row["value_decimal"]) >= SCREENER_MIN_MARKET_CAP_CRORE
    except (TypeError, ValueError):
        return False


def canonical_json(value: Any, *, json=json) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def register_artifact_event(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    event_key: str,
    event_type: str,
    evidenced_at_utc: str,
    evidence_source: str,
    evidence: dict[str, Any],
    event_date: str | None = None,
    period_end: str | None = None,
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
    canonical_json=canonical_json, parse_utc=parse_utc,
) -> int:
    """Register one evidenced event without rewriting prior event identity."""

    parse_utc(evidenced_at_utc)
    if not event_key.strip() or not evidence_source.strip():
        raise ValueError("Event key and evidence source are required")
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO artifact_events
               (company_id, event_key, event_type, event_date, period_end,
                fiscal_year, fiscal_quarter, evidenced_at_utc, evidence_source,
                evidence_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                company_id,
                event_key,
                event_type,
                event_date,
                period_end,
                fiscal_year,
                fiscal_quarter,
                evidenced_at_utc,
                evidence_source,
                canonical_json(evidence),
            ),
        )
    row = conn.execute(
        "SELECT * FROM artifact_events WHERE company_id=? AND event_key=?",
        (company_id, event_key),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to register artifact event")
    for field, expected in (
        ("event_type", event_type),
        ("event_date", event_date),
        ("period_end", period_end),
        ("fiscal_year", fiscal_year),
        ("fiscal_quarter", fiscal_quarter),
    ):
        actual = row[field]
        if actual is not None and expected is not None and actual != expected:
            raise ValueError(
                f"Event identity conflict for {event_key}: {field} is {actual!r}, "
                f"not {expected!r}"
            )
    return int(row["event_id"])


def ensure_artifact_expectation(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    artifact_type: str,
    expectation_basis: str,
    expected_after_utc: str,
    created_at_utc: str,
    policy: RetryPolicy,
    canonical_json=canonical_json, parse_utc=parse_utc,
) -> int:
    event = conn.execute(
        "SELECT company_id FROM artifact_events WHERE event_id=?", (event_id,)
    ).fetchone()
    if event is None:
        raise ValueError(f"Unknown artifact event: {event_id}")
    if not expectation_basis.strip():
        raise ValueError("Expectation basis is required")
    parse_utc(created_at_utc)
    next_check = policy.check_at(expected_after_utc, 0)
    schedule_json = canonical_json(policy.to_dict())
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO artifact_expectations
               (company_id, artifact_type, event_id, expectation_basis,
                expected_after_utc, availability_status, next_check_at_utc,
                schedule_json, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, 'expected_not_yet_published', ?, ?, ?, ?)""",
            (
                int(event["company_id"]),
                artifact_type,
                event_id,
                expectation_basis,
                expected_after_utc,
                next_check,
                schedule_json,
                created_at_utc,
                created_at_utc,
            ),
        )
    row = conn.execute(
        """SELECT expectation_id, expectation_basis, expected_after_utc,
                  first_checked_at_utc, attempt_count, terminal_reason,
                  schedule_json
           FROM artifact_expectations
           WHERE company_id=? AND artifact_type=? AND event_id=?""",
        (int(event["company_id"]), artifact_type, event_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to register artifact expectation")
    rearmed_delayed_document = bool(
        row["expectation_basis"]
        == "accepted_exchange_result_filing_requires_primary_source_discovery"
        and parse_utc(str(row["expected_after_utc"]))
        > parse_utc(expected_after_utc)
    )
    if row["expected_after_utc"] != expected_after_utc and not rearmed_delayed_document:
        raise ValueError("Existing expectation has a conflicting expected-after time")
    if row["schedule_json"] != schedule_json:
        raise ValueError("Existing expectation has a different persisted retry policy")
    return int(row["expectation_id"])


def ensure_result_artifact_expectations(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    filing_id: int,
    exchange: str,
    exchange_key: str,
    result_period: str,
    period_end: str,
    broadcast_at_utc: str,
    created_at_utc: str,
    policy: RetryPolicy | None = None,
    DELAYED_SCREENER_RESULT_TYPES=DELAYED_SCREENER_RESULT_TYPES, RESULT_ARTIFACT_TYPES=RESULT_ARTIFACT_TYPES, RetryPolicy=RetryPolicy, ensure_artifact_expectation=ensure_artifact_expectation, register_artifact_event=register_artifact_event, screener_market_cap_above_floor=screener_market_cap_above_floor,
) -> tuple[int, ...]:
    """Require the four document surfaces for one accepted official result.

    A filing is authoritative evidence that the result event occurred.  It is
    not evidence that any particular document was published, so the resulting
    D09 rows remain ``expected_not_yet_published`` until D01 records a
    source-faithful document discovery.  This makes the follow-up durable
    without turning a result worker into a blind document crawler.
    """
    # Some exchange feeds use the explicit epoch sentinel when the filing row
    # cannot establish a reporting period. It is not a real event identity and
    # must not start a document retry campaign.
    if period_end == "1970-01-01":
        return ()
    delayed_screener_documents_allowed = screener_market_cap_above_floor(
        conn, company_id
    )
    policy = policy or RetryPolicy()
    event_id = register_artifact_event(
        conn,
        company_id=company_id,
        event_key=f"exchange-result-filing:{filing_id}",
        event_type="results",
        period_end=period_end,
        evidenced_at_utc=broadcast_at_utc,
        evidence_source="exchange_result_filing",
        evidence={
            "filing_id": filing_id,
            "exchange": exchange,
            "exchange_key": exchange_key,
            "result_period": result_period,
        },
    )
    basis = "accepted_exchange_result_filing_requires_primary_source_discovery"
    return tuple(
        ensure_artifact_expectation(
            conn,
            event_id=event_id,
            artifact_type=artifact_type,
            expectation_basis=basis,
            expected_after_utc=broadcast_at_utc,
            created_at_utc=created_at_utc,
            policy=policy,
        )
        for artifact_type in RESULT_ARTIFACT_TYPES
        if artifact_type not in DELAYED_SCREENER_RESULT_TYPES
        or delayed_screener_documents_allowed
    )


def backfill_result_artifact_expectations(
    conn: sqlite3.Connection,
    *,
    created_at_utc: str,
    limit: int | None = None,
    ensure_result_artifact_expectations=ensure_result_artifact_expectations,
) -> dict[str, int]:
    """Register requirements for earlier accepted exchange-result jobs.

    The operation is idempotent because the event key is tied to the immutable
    official filing and expectations are unique per event/type.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    sql = """
        SELECT queue.queue_item_id, accepted.company_id, filing.filing_id,
               filing.exchange, filing.exchange_key, filing.result_period,
               filing.period_end_date, filing.broadcast_at_utc
          FROM screener_company_queue AS queue
          JOIN exchange_result_filings AS filing
            ON filing.filing_id=queue.exchange_filing_id
          JOIN accepted_page_versions AS accepted
            ON accepted.parse_id=queue.accepted_parse_id
           AND accepted.source='screener'
         WHERE queue.origin='exchange_filing'
           AND queue.status='accepted'
           AND queue.accepted_parse_id IS NOT NULL
         ORDER BY queue.queue_item_id
    """
    parameters: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        parameters = (limit,)
    rows = conn.execute(sql, parameters).fetchall()
    event_count = 0
    expectation_count = 0
    for row in rows:
        before = conn.total_changes
        ensure_result_artifact_expectations(
            conn,
            company_id=int(row["company_id"]),
            filing_id=int(row["filing_id"]),
            exchange=str(row["exchange"]),
            exchange_key=str(row["exchange_key"]),
            result_period=str(row["result_period"]),
            period_end=str(row["period_end_date"]),
            broadcast_at_utc=str(row["broadcast_at_utc"]),
            created_at_utc=created_at_utc,
        )
        # One event plus four expectation rows on first registration.  Each
        # helper commits its own idempotent unit, so a large historical backfill
        # never monopolizes the shared SQLite database.
        changed = conn.total_changes - before
        event_count += int(changed >= 1)
        expectation_count += min(4, max(0, changed - 1))
    return {
        "accepted_result_count": len(rows),
        "registered_event_count": event_count,
        "registered_expectation_count": expectation_count,
    }


def ensure_discovered_artifact_expectations(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    artifact_ids: Iterable[int],
    observed_at_utc: str,
    policy: RetryPolicy | None = None,
    RetryPolicy=RetryPolicy, ensure_artifact_expectation=ensure_artifact_expectation, register_artifact_event=register_artifact_event,
) -> tuple[int, ...]:
    """Turn D01 artifact/event evidence into idempotent D09 queue rows."""

    policy = policy or RetryPolicy()
    unique_ids = tuple(dict.fromkeys(int(item) for item in artifact_ids))
    if not unique_ids:
        return ()
    placeholders = ",".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"""SELECT artifact_id, artifact_type, event_date, period_end,
                   fiscal_year, fiscal_quarter
            FROM document_artifacts
            WHERE company_id=? AND artifact_id IN ({placeholders})
            ORDER BY artifact_id""",
        (company_id, *unique_ids),
    ).fetchall()
    if len(rows) != len(unique_ids):
        raise ValueError("One or more discovered artifacts do not belong to company")

    groups: dict[
        tuple[str, str | None, str | None, int | None, int | None],
        list[sqlite3.Row],
    ] = {}
    for row in rows:
        if (
            row["event_date"] is None
            and row["period_end"] is None
            and row["fiscal_year"] is None
        ):
            # D09 requires an evidenced event identity. A bare document link
            # cannot safely create a cross-artifact expectation.
            continue
        artifact_type = row["artifact_type"]
        family = (
            "earnings_call"
            if artifact_type in {"transcript", "recording"}
            else "results"
            if artifact_type == "presentation"
            else "rating_review"
        )
        groups.setdefault(
            (
                family,
                row["event_date"],
                row["period_end"],
                row["fiscal_year"],
                row["fiscal_quarter"],
            ),
            [],
        ).append(row)

    expectation_ids: list[int] = []
    for (family, event_date, period_end, fiscal_year, fiscal_quarter), group in groups.items():
        exemplar = group[0]
        identity = ":".join(
            part
            for part in (
                f"event-{event_date}" if event_date else "",
                f"period-{period_end}" if period_end else "",
                f"fy-{fiscal_year}" if fiscal_year is not None else "",
                f"q-{fiscal_quarter}" if fiscal_quarter is not None else "",
            )
            if part
        )
        event_key = f"{family}:{identity}"
        event_id = register_artifact_event(
            conn,
            company_id=company_id,
            event_key=event_key,
            event_type=family,
            event_date=exemplar["event_date"],
            period_end=exemplar["period_end"],
            fiscal_year=exemplar["fiscal_year"],
            fiscal_quarter=exemplar["fiscal_quarter"],
            evidenced_at_utc=observed_at_utc,
            evidence_source="d01_artifact_discovery",
            evidence={"artifact_ids": [int(row["artifact_id"]) for row in group]},
        )
        event = conn.execute(
            "SELECT evidenced_at_utc FROM artifact_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        artifact_types = {row["artifact_type"] for row in group}
        if family == "earnings_call":
            artifact_types.update(("recording", "transcript"))
        for artifact_type in sorted(artifact_types):
            expectation_ids.append(
                ensure_artifact_expectation(
                    conn,
                    event_id=event_id,
                    artifact_type=artifact_type,
                    expectation_basis="discovered_event_or_artifact",
                    expected_after_utc=event["evidenced_at_utc"],
                    created_at_utc=event["evidenced_at_utc"],
                    policy=policy,
                )
            )
    return tuple(expectation_ids)

