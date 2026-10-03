"""Fail-closed, point-in-time ML feature dataset construction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from datetime import datetime, timezone

from ..core.reporting_scope import STANDALONE_SCOPE, preferred_scope

TRANSFORMATION_VERSION = "screener_ml_contract_v2_standalone"


class MLContractError(ValueError):
    """Raised when feature provenance is incomplete or scope is ambiguous."""


@dataclass(frozen=True)
class MLFeature:
    company_id: int
    period: str
    reporting_scope: str
    available_at: str
    source_artifact_id: int
    transformation_version: str
    feature_code: str
    value: str | None


def build_screener_ml_features(
    conn: sqlite3.Connection, *, feature_codes: Iterable[str] | None = None,
    transformation_version: str = TRANSFORMATION_VERSION,
) -> tuple[MLFeature, ...]:
    """Build only accepted standalone, point-in-time observations.

    A missing period, timestamp, blob, or a scope mixed within the selected
    company-period is a data-quality error.  It is intentionally not imputed.
    """
    if transformation_version != TRANSFORMATION_VERSION:
        raise MLContractError(
            f"incompatible transformation version: {transformation_version!r}"
        )
    wanted = tuple(feature_codes or ())
    clause = ""
    parameters: tuple[object, ...] = ()
    if wanted:
        clause = " AND metric.metric_code IN (%s)" % ",".join("?" for _ in wanted)
        parameters = wanted
    # Fail before selecting data if an allegedly standalone accepted page has a
    # mixed or unresolved financial observation.  Filtering it out would hide
    # a provenance breach from the model-building caller.
    invalid = conn.execute(
        """SELECT page.company_id, page.resolved_source_period, page.parse_id,
                  financial.reporting_scope
             FROM accepted_page_versions AS page
             JOIN financial_observations AS financial ON financial.parse_id=page.parse_id
            WHERE page.source='screener' AND page.reporting_scope='standalone'
              AND (financial.reporting_scope <> 'standalone'
                   OR financial.available_at_utc IS NULL
                   OR trim(financial.available_at_utc)='')
            LIMIT 1"""
    ).fetchone()
    if invalid is not None:
        raise MLContractError(
            "invalid financial observation at ML boundary "
            f"company_id={invalid['company_id']} period={invalid['resolved_source_period']} "
            f"parse_id={invalid['parse_id']} scope={invalid['reporting_scope']}"
        )
    rows = conn.execute(
        """SELECT page.company_id, page.resolved_source_period AS period,
                  page.reporting_scope, page.parse_id, artifact.blob_id,
                  metric.metric_code, metric.value_decimal, metric.available_at_utc
             FROM accepted_page_versions AS page
             JOIN parse_artifacts AS artifact ON artifact.parse_id=page.parse_id
             JOIN company_metric_observations AS metric ON metric.parse_id=page.parse_id
            WHERE page.source='screener'
              AND page.reporting_scope='standalone'
              AND page.resolved_source_period NOT IN ('', 'not_applicable')""" + clause +
        " ORDER BY page.company_id, page.resolved_source_period, page.reporting_scope, metric.metric_code",
        parameters,
    ).fetchall()
    grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((int(row["company_id"]), str(row["period"])), []).append(row)
    features: list[MLFeature] = []
    for (company_id, period), observations in grouped.items():
        scopes = {str(row["reporting_scope"]) for row in observations}
        selected_scope = preferred_scope(scopes)
        selected = [row for row in observations if row["reporting_scope"] == selected_scope]
        # Each metric must have exactly one accepted value in the selected view.
        seen: set[str] = set()
        for row in selected:
            code = row["metric_code"]
            if not code or code in seen or not row["available_at_utc"] or row["blob_id"] is None:
                raise MLContractError(
                    f"unresolved ML observation company_id={company_id} period={period} scope={selected_scope}"
                )
            try:
                available_at = datetime.fromisoformat(str(row["available_at_utc"]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise MLContractError("ML observation has invalid available_at") from exc
            if available_at.tzinfo is None or available_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
                raise MLContractError("ML observation has invalid point-in-time availability")
            seen.add(str(code))
            features.append(MLFeature(
                company_id, period, selected_scope, str(row["available_at_utc"]),
                int(row["blob_id"]), transformation_version, str(code), row["value_decimal"],
            ))
    return tuple(features)
