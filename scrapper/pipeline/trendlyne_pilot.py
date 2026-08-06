"""P03 deterministic and resumable Trendlyne month-pilot orchestration."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import DB_PATH, THROTTLE_JITTER, THROTTLE_SECONDS
from ..db.database import connect, init_db
from ..db.repo import utc_now
from ..db.trendlyne_repo import ensure_trendlyne_company
from ..sources.trendlyne.urls import monthly_mf_url, source_period_for_url, verified_mapping
from .trendlyne_vertical import TrendlyneSliceResult, scrape_trendlyne_company


PILOT_CODE = "P03"
PILOT_SYMBOLS = ("RBA",)
MIN_MONTHS = 6
MAX_MONTHS = 12
MAX_BATCH_SIZE = 3


@dataclass(frozen=True)
class TrendlynePilotRequest:
    position: int
    source_company_id: str
    source_symbol: str
    source_slug: str
    requested_source_period: str
    requested_url: str
    status: str
    accepted_parse_id: int | None


@dataclass(frozen=True)
class TrendlynePilotPlan:
    pilot_code: str
    plan_key: str
    end_source_period: str
    month_count: int
    company_count: int
    request_count: int
    accepted_request_count: int
    pending_request_count: int
    batch_size: int
    next_batch_request_count: int
    requests: tuple[TrendlynePilotRequest, ...]

    def to_dict(self) -> dict:
        return {
            **{key: value for key, value in asdict(self).items() if key != "requests"},
            "requests": [asdict(request) for request in self.requests],
        }


@dataclass(frozen=True)
class TrendlynePilotRunResult:
    plan_id: int
    plan_key: str
    attempted_request_count: int
    accepted_request_count: int
    failed_request_count: int
    remaining_request_count: int
    status: str
    errors: tuple[dict, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical_periods(end_source_period: str, month_count: int) -> tuple[str, ...]:
    source_period_for_url(end_source_period)
    if end_source_period == "latest":
        raise ValueError("P03 requires an exact --end-period YYYY-MM, never latest")
    if not MIN_MONTHS <= month_count <= MAX_MONTHS:
        raise ValueError(f"P03 month count must be between {MIN_MONTHS} and {MAX_MONTHS}")
    end = datetime.strptime(end_source_period, "%Y-%m")
    periods: list[str] = []
    for offset in range(month_count):
        ordinal = end.year * 12 + end.month - 1 - offset
        year, zero_based_month = divmod(ordinal, 12)
        periods.append(f"{year:04d}-{zero_based_month + 1:02d}")
    return tuple(periods)


def _pilot_mappings(symbols: tuple[str, ...]) -> tuple:
    normalized = tuple(symbol.strip().upper() for symbol in symbols)
    if not normalized or any(not symbol for symbol in normalized):
        raise ValueError("P03 requires at least one pilot symbol")
    if len(set(normalized)) != len(normalized):
        raise ValueError("P03 pilot symbols must be unique")
    outside_pilot = sorted(set(normalized) - set(PILOT_SYMBOLS))
    if outside_pilot:
        raise ValueError(
            "P03 cannot expand beyond its verified pilot allowlist: "
            + ", ".join(outside_pilot)
        )
    return tuple(verified_mapping(symbol) for symbol in normalized)


def _accepted_parse_id(
    conn: sqlite3.Connection, source_company_id: str, source_period: str
) -> int | None:
    row = conn.execute(
        """SELECT apv.parse_id
           FROM accepted_page_versions AS apv
           JOIN source_identifiers AS si
             ON si.company_id=apv.company_id AND si.source='trendlyne'
           WHERE apv.source='trendlyne'
             AND apv.reporting_scope='not_applicable'
             AND apv.resolved_source_period=?
             AND si.source_company_id=?""",
        (source_period, source_company_id),
    ).fetchone()
    return int(row["parse_id"]) if row else None


def build_trendlyne_pilot_plan(
    conn: sqlite3.Connection,
    *,
    end_source_period: str,
    month_count: int = MAX_MONTHS,
    batch_size: int = MAX_BATCH_SIZE,
    symbols: tuple[str, ...] = PILOT_SYMBOLS,
) -> TrendlynePilotPlan:
    """Return the complete exact URL plan without changing database state."""
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"P03 batch size must be between 1 and {MAX_BATCH_SIZE}")
    periods = _canonical_periods(end_source_period, month_count)
    mappings = _pilot_mappings(symbols)
    request_specs = [
        {
            "source_company_id": mapping.source_company_id,
            "source_symbol": mapping.source_symbol,
            "source_slug": mapping.source_slug,
            "requested_source_period": period,
            "requested_url": monthly_mf_url(
                mapping, source_period_for_url(period)
            ),
        }
        for mapping in mappings
        for period in periods
    ]
    canonical_scope = {
        "pilot_code": PILOT_CODE,
        "end_source_period": end_source_period,
        "month_count": month_count,
        "symbols": [mapping.source_symbol for mapping in mappings],
        "requests": request_specs,
    }
    plan_key = hashlib.sha256(
        json.dumps(
            canonical_scope, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    requests: list[TrendlynePilotRequest] = []
    for position, spec in enumerate(request_specs):
        parse_id = _accepted_parse_id(
            conn, spec["source_company_id"], spec["requested_source_period"]
        )
        requests.append(
            TrendlynePilotRequest(
                position=position,
                **spec,
                status="accepted" if parse_id is not None else "pending",
                accepted_parse_id=parse_id,
            )
        )
    accepted_count = sum(request.status == "accepted" for request in requests)
    pending_count = len(requests) - accepted_count
    return TrendlynePilotPlan(
        pilot_code=PILOT_CODE,
        plan_key=plan_key,
        end_source_period=end_source_period,
        month_count=month_count,
        company_count=len(mappings),
        request_count=len(requests),
        accepted_request_count=accepted_count,
        pending_request_count=pending_count,
        batch_size=batch_size,
        next_batch_request_count=min(batch_size, pending_count),
        requests=tuple(requests),
    )


def _persist_plan(conn: sqlite3.Connection, plan: TrendlynePilotPlan) -> int:
    now = utc_now()
    immutable_plan = plan.to_dict()
    for key in (
        "accepted_request_count",
        "pending_request_count",
        "next_batch_request_count",
    ):
        immutable_plan.pop(key)
    for request in immutable_plan["requests"]:
        request.pop("status")
        request.pop("accepted_parse_id")
    plan_json = json.dumps(immutable_plan, sort_keys=True, separators=(",", ":"))
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO trendlyne_backfill_plans
               (plan_key, pilot_code, end_source_period, month_count,
                company_count, request_count, batch_size, plan_json, status,
                created_at_utc, updated_at_utc)
               VALUES (?, 'P03', ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                plan.plan_key,
                plan.end_source_period,
                plan.month_count,
                plan.company_count,
                plan.request_count,
                plan.batch_size,
                plan_json,
                now,
                now,
            ),
        )
        row = conn.execute(
            """SELECT plan_id, batch_size, plan_json
               FROM trendlyne_backfill_plans WHERE plan_key=?""",
            (plan.plan_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist P03 request plan")
        if int(row["batch_size"]) != plan.batch_size or row["plan_json"] != plan_json:
            raise ValueError("Existing P03 plan differs from the requested immutable plan")
        plan_id = int(row["plan_id"])
        for request in plan.requests:
            mapping = verified_mapping(request.source_symbol)
            company_id = ensure_trendlyne_company(conn, mapping)
            conn.execute(
                """INSERT OR IGNORE INTO trendlyne_backfill_items
                   (plan_id, request_position, company_id, source_company_id,
                    source_symbol, source_slug, requested_source_period,
                    requested_url, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    request.position,
                    company_id,
                    request.source_company_id,
                    request.source_symbol,
                    request.source_slug,
                    request.requested_source_period,
                    request.requested_url,
                    request.status,
                ),
            )
            if request.accepted_parse_id is not None:
                conn.execute(
                    """UPDATE trendlyne_backfill_items
                       SET status='accepted', parse_id=?, completed_at_utc=COALESCE(completed_at_utc, ?),
                           last_error_json=NULL
                       WHERE plan_id=? AND request_position=?""",
                    (request.accepted_parse_id, now, plan_id, request.position),
                )
    return plan_id


def _reconcile_plan(conn: sqlite3.Connection, plan_id: int) -> None:
    now = utc_now()
    items = conn.execute(
        """SELECT request_position, source_company_id, requested_source_period
           FROM trendlyne_backfill_items WHERE plan_id=?""",
        (plan_id,),
    ).fetchall()
    with conn:
        for item in items:
            parse_id = _accepted_parse_id(
                conn, item["source_company_id"], item["requested_source_period"]
            )
            if parse_id is not None:
                conn.execute(
                    """UPDATE trendlyne_backfill_items
                       SET status='accepted', parse_id=?, completed_at_utc=COALESCE(completed_at_utc, ?),
                           last_error_json=NULL
                       WHERE plan_id=? AND request_position=?""",
                    (parse_id, now, plan_id, item["request_position"]),
                )
        counts = {
            row["status"]: int(row["count"])
            for row in conn.execute(
                """SELECT status, COUNT(*) AS count
                   FROM trendlyne_backfill_items WHERE plan_id=? GROUP BY status""",
                (plan_id,),
            )
        }
        total = sum(counts.values())
        if counts.get("accepted", 0) == total:
            status = "complete"
        elif counts.get("failed", 0) and not counts.get("pending", 0) and not counts.get("running", 0):
            status = "failed"
        elif counts.get("accepted", 0) or counts.get("running", 0) or counts.get("failed", 0):
            status = "in_progress"
        else:
            status = "pending"
        conn.execute(
            """UPDATE trendlyne_backfill_plans SET status=?, updated_at_utc=?
               WHERE plan_id=?""",
            (status, now, plan_id),
        )


def register_trendlyne_pilot_plan(
    *,
    end_source_period: str,
    month_count: int = MAX_MONTHS,
    batch_size: int = MAX_BATCH_SIZE,
    symbols: tuple[str, ...] = PILOT_SYMBOLS,
    db_path: Path = DB_PATH,
) -> tuple[int, TrendlynePilotPlan]:
    """Persist and reconcile an exact plan without issuing any HTTP request."""
    conn = connect(db_path)
    init_db(conn)
    try:
        plan = build_trendlyne_pilot_plan(
            conn,
            end_source_period=end_source_period,
            month_count=month_count,
            batch_size=batch_size,
            symbols=symbols,
        )
        plan_id = _persist_plan(conn, plan)
        _reconcile_plan(conn, plan_id)
        return plan_id, plan
    finally:
        conn.close()


def run_trendlyne_pilot_batch(
    *,
    end_source_period: str,
    month_count: int = MAX_MONTHS,
    batch_size: int = MAX_BATCH_SIZE,
    symbols: tuple[str, ...] = PILOT_SYMBOLS,
    db_path: Path = DB_PATH,
    on_plan: Callable[[TrendlynePilotPlan], None] | None = None,
    execute_request: Callable[[str, str], TrendlyneSliceResult] | None = None,
    apply_live_throttle: bool = True,
) -> TrendlynePilotRunResult:
    """Print/callback the exact plan, then attempt no more than one bounded batch."""
    conn = connect(db_path)
    init_db(conn)
    try:
        plan = build_trendlyne_pilot_plan(
            conn,
            end_source_period=end_source_period,
            month_count=month_count,
            batch_size=batch_size,
            symbols=symbols,
        )
    finally:
        conn.close()
    if on_plan is not None:
        on_plan(plan)

    conn = connect(db_path)
    init_db(conn)
    try:
        plan_id = _persist_plan(conn, plan)
        _reconcile_plan(conn, plan_id)
        items = conn.execute(
            """SELECT request_position, source_symbol, requested_source_period
               FROM trendlyne_backfill_items
               WHERE plan_id=? AND status!='accepted'
               ORDER BY request_position LIMIT ?""",
            (plan_id, batch_size),
        ).fetchall()
    finally:
        conn.close()

    executor = execute_request or (
        lambda symbol, period: scrape_trendlyne_company(
            symbol, requested_source_period=period, db_path=db_path
        )
    )
    errors: list[dict] = []
    attempted = 0
    accepted = 0
    for item_index, item in enumerate(items):
        conn = connect(db_path)
        try:
            with conn:
                conn.execute(
                    """UPDATE trendlyne_backfill_items
                       SET status='running', attempt_count=attempt_count+1,
                           last_started_at_utc=?, last_error_json=NULL
                       WHERE plan_id=? AND request_position=?""",
                    (utc_now(), plan_id, item["request_position"]),
                )
        finally:
            conn.close()
        attempted += 1
        try:
            result = executor(item["source_symbol"], item["requested_source_period"])
            conn = connect(db_path)
            try:
                with conn:
                    conn.execute(
                        """UPDATE trendlyne_backfill_items
                           SET status='accepted', completed_at_utc=?, fetch_id=?,
                               parse_id=?, last_error_json=NULL
                           WHERE plan_id=? AND request_position=?""",
                        (
                            utc_now(),
                            result.fetch_id,
                            result.parse_id,
                            plan_id,
                            item["request_position"],
                        ),
                    )
                accepted += 1
            finally:
                conn.close()
        except Exception as exc:
            error = {
                "source_symbol": item["source_symbol"],
                "requested_source_period": item["requested_source_period"],
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            errors.append(error)
            conn = connect(db_path)
            try:
                with conn:
                    conn.execute(
                        """UPDATE trendlyne_backfill_items
                           SET status='failed', completed_at_utc=?, last_error_json=?
                           WHERE plan_id=? AND request_position=?""",
                        (
                            utc_now(),
                            json.dumps(error, sort_keys=True),
                            plan_id,
                            item["request_position"],
                        ),
                    )
            finally:
                conn.close()
            break
        if (
            execute_request is None
            and apply_live_throttle
            and item_index + 1 < len(items)
        ):
            time.sleep(THROTTLE_SECONDS + random.uniform(0, THROTTLE_JITTER))

    conn = connect(db_path)
    try:
        _reconcile_plan(conn, plan_id)
        counts = {
            row["status"]: int(row["count"])
            for row in conn.execute(
                """SELECT status, COUNT(*) AS count
                   FROM trendlyne_backfill_items WHERE plan_id=? GROUP BY status""",
                (plan_id,),
            )
        }
        status = str(
            conn.execute(
                "SELECT status FROM trendlyne_backfill_plans WHERE plan_id=?",
                (plan_id,),
            ).fetchone()["status"]
        )
    finally:
        conn.close()
    remaining = sum(counts.get(name, 0) for name in ("pending", "running", "failed"))
    return TrendlynePilotRunResult(
        plan_id=plan_id,
        plan_key=plan.plan_key,
        attempted_request_count=attempted,
        accepted_request_count=accepted,
        failed_request_count=len(errors),
        remaining_request_count=remaining,
        status=status,
        errors=tuple(errors),
    )
