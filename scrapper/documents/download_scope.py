"""Explicit, auditable document download scopes.

DA-05 never treats the full artifact catalogue as an implicit work list.  A
scope records the selected companies and artifact types before queue rows are
materialized.  The P0 contract is intentionally PDF-only and excludes
recordings.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..db.repo import utc_now
from ..db.repository_backend import atomic_repository


P0_ARTIFACT_TYPES = ("transcript", "presentation", "credit_rating")
SUPPORTED_SCOPE_KINDS = ("P0", "P1", "P2", "P3", "custom")


@dataclass(frozen=True)
class ScopeCompany:
    symbol: str
    outcome_label: str | None = None
    archetype: str | None = None
    company_id: int | None = None


def _normalize_companies(
    companies: Iterable[ScopeCompany | str | Mapping[str, Any]],
) -> tuple[ScopeCompany, ...]:
    normalized: dict[str, ScopeCompany] = {}
    for item in companies:
        if isinstance(item, ScopeCompany):
            company = item
        elif isinstance(item, str):
            company = ScopeCompany(item)
        else:
            company = ScopeCompany(
                str(item.get("symbol", "")),
                str(item["outcome_label"]) if item.get("outcome_label") else None,
                str(item["archetype"]) if item.get("archetype") else None,
                int(item["company_id"]) if item.get("company_id") is not None else None,
            )
        symbol = company.symbol.strip().upper()
        if company.company_id is not None and company.company_id < 1:
            raise ValueError("company_id must be a positive integer")
        if not symbol and company.company_id is None:
            raise ValueError("Every download-scope company needs company_id or an NSE symbol")
        candidate = ScopeCompany(
            symbol,
            company.outcome_label.strip().lower() if company.outcome_label else None,
            company.archetype.strip() if company.archetype else None,
            company.company_id,
        )
        key = f"company_id:{company.company_id}" if company.company_id else f"symbol:{symbol}"
        previous = normalized.get(key)
        if previous is not None and previous != candidate:
            raise ValueError(f"Conflicting scope metadata for {key}")
        normalized[key] = candidate
    if not normalized:
        raise ValueError("A download scope must contain at least one company")
    return tuple(normalized[key] for key in sorted(normalized))


def _validate_p0(
    companies: tuple[ScopeCompany, ...],
    artifact_types: tuple[str, ...],
    artifact_ids: tuple[int, ...],
    *,
    enforce_pilot_composition: bool,
) -> None:
    if set(artifact_types) - set(P0_ARTIFACT_TYPES):
        raise ValueError("P0 permits only transcript, presentation, and credit_rating")
    if not enforce_pilot_composition:
        return
    if not 30 <= len(companies) <= 50:
        raise ValueError("A production P0 scope must contain 30 to 50 companies")
    if not artifact_ids:
        raise ValueError(
            "A production P0 scope must explicitly list acquisition artifact_ids"
        )


@atomic_repository('download-scope')
def register_download_scope(
    conn: sqlite3.Connection,
    *,
    scope_id: str,
    companies: Iterable[ScopeCompany | str | Mapping[str, Any]],
    scope_kind: str = "P0",
    artifact_types: Iterable[str] = P0_ARTIFACT_TYPES,
    artifact_ids: Iterable[int] = (),
    description: str | None = None,
    enforce_pilot_composition: bool = True,
    require_explicit_artifacts: bool = True,
    recording_fallback_only: bool | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create or verify one immutable scope definition.

    Re-registering the same definition is idempotent. Changing membership or
    policy under an existing ``scope_id`` fails closed so queue provenance does
    not drift after downloads have started.
    """

    scope_id = scope_id.strip()
    if not scope_id:
        raise ValueError("scope_id cannot be empty")
    if scope_kind not in SUPPORTED_SCOPE_KINDS:
        raise ValueError(f"Unsupported scope kind: {scope_kind}")
    selected_types = tuple(dict.fromkeys(str(value) for value in artifact_types))
    if not selected_types:
        raise ValueError("A download scope must select at least one artifact type")
    allowed_types = set(P0_ARTIFACT_TYPES) | {"recording"}
    unknown_types = set(selected_types) - allowed_types
    if unknown_types:
        raise ValueError(f"Unsupported artifact types: {sorted(unknown_types)}")
    selected_companies = _normalize_companies(companies)
    selected_artifact_ids = tuple(sorted({int(value) for value in artifact_ids}))
    if any(value < 1 for value in selected_artifact_ids):
        raise ValueError("artifact_ids must be positive integers")
    if scope_kind == "P0":
        _validate_p0(
            selected_companies,
            selected_types,
            selected_artifact_ids,
            enforce_pilot_composition=enforce_pilot_composition,
        )
    if require_explicit_artifacts and not selected_artifact_ids:
        raise ValueError(
            "A production download scope must explicitly list artifact_ids"
        )
    fallback_only = (
        scope_kind in {"P0", "P1", "P2", "P3"}
        if recording_fallback_only is None
        else bool(recording_fallback_only)
    )
    if "recording" in selected_types and not fallback_only:
        raise ValueError(
            "Recording scopes must explicitly enforce recording_fallback_only"
        )
    now = created_at_utc or utc_now()
    artifact_types_json = json.dumps(selected_types, separators=(",", ":"))
    policy = {
        "pdf_only": scope_kind == "P0",
        "recording_fallback_only": fallback_only,
    }
    policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"))

    resolved: list[tuple[int, ScopeCompany]] = []
    for item in selected_companies:
        if item.company_id is not None:
            company = conn.execute(
                "SELECT company_id FROM companies WHERE company_id=?",
                (item.company_id,),
            ).fetchone()
            if company is None:
                raise ValueError(f"Unknown company_id: {item.company_id}")
            identifier_rows = conn.execute(
                """SELECT identifier_value FROM security_identifiers
                    WHERE company_id=? AND identifier_type='NSE' AND is_current=1
                    ORDER BY security_identifier_id""",
                (item.company_id,),
            ).fetchall()
            if item.symbol:
                if identifier_rows and item.symbol not in {
                    str(row["identifier_value"]).strip().upper()
                    for row in identifier_rows
                }:
                    raise ValueError(
                        f"NSE symbol {item.symbol} does not belong to company_id "
                        f"{item.company_id}"
                    )
                symbol = item.symbol
            else:
                symbol = (
                    str(identifier_rows[0]["identifier_value"]).strip().upper()
                    if identifier_rows
                    else f"COMPANY:{item.company_id}"
                )
            resolved.append(
                (
                    item.company_id,
                    ScopeCompany(
                        symbol,
                        item.outcome_label,
                        item.archetype,
                        item.company_id,
                    ),
                )
            )
            continue
        rows = conn.execute(
            """SELECT DISTINCT company_id FROM security_identifiers
                WHERE identifier_type='NSE' AND identifier_value=? AND is_current=1""",
            (item.symbol,),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                f"Expected exactly one current company for NSE symbol {item.symbol}; "
                f"found {len(rows)}"
            )
        resolved.append(
            (
                int(rows[0]["company_id"]),
                ScopeCompany(
                    item.symbol,
                    item.outcome_label,
                    item.archetype,
                    int(rows[0]["company_id"]),
                ),
            )
        )

    resolved_company_ids = {company_id for company_id, _ in resolved}
    if selected_artifact_ids:
        placeholders = ",".join("?" for _ in selected_artifact_ids)
        artifact_rows = conn.execute(
            f"""SELECT artifact_id, company_id, artifact_type
                  FROM document_artifacts
                 WHERE artifact_id IN ({placeholders})""",
            selected_artifact_ids,
        ).fetchall()
        if len(artifact_rows) != len(selected_artifact_ids):
            found = {int(row["artifact_id"]) for row in artifact_rows}
            missing = sorted(set(selected_artifact_ids) - found)
            raise ValueError(f"Unknown scoped artifact_ids: {missing}")
        for row in artifact_rows:
            if int(row["company_id"]) not in resolved_company_ids:
                raise ValueError(
                    f"Artifact {row['artifact_id']} does not belong to a scoped company"
                )
            if row["artifact_type"] not in selected_types:
                raise ValueError(
                    f"Artifact {row['artifact_id']} has unselected type "
                    f"{row['artifact_type']}"
                )

    expected_definition = {
        "scope_kind": scope_kind,
        "description": description,
        "artifact_types_json": artifact_types_json,
        "policy_json": policy_json,
    }
    existing = conn.execute(
        "SELECT * FROM artifact_download_scopes WHERE scope_id=?", (scope_id,)
    ).fetchone()
    if existing is not None:
        actual_definition = {key: existing[key] for key in expected_definition}
        actual_members = {
            (
                int(row["company_id"]),
                row["symbol"],
                row["outcome_label"],
                row["archetype"],
            )
            for row in conn.execute(
                """SELECT company_id, symbol, outcome_label, archetype
                     FROM artifact_download_scope_companies WHERE scope_id=?""",
                (scope_id,),
            )
        }
        expected_members = {
            (company_id, item.symbol, item.outcome_label, item.archetype)
            for company_id, item in resolved
        }
        actual_artifact_ids = {
            int(row["artifact_id"])
            for row in conn.execute(
                """SELECT artifact_id FROM artifact_download_scope_artifacts
                    WHERE scope_id=?""",
                (scope_id,),
            )
        }
        if (
            actual_definition != expected_definition
            or actual_members != expected_members
            or actual_artifact_ids != set(selected_artifact_ids)
        ):
            raise ValueError(f"Download scope {scope_id!r} already has a different definition")
        return {
            "scope_id": scope_id,
            "scope_kind": scope_kind,
            "company_count": len(resolved),
            "artifact_count": len(selected_artifact_ids),
            "artifact_types": list(selected_types),
            "created": False,
            "policy": policy,
        }

    with conn:
        conn.execute(
            """INSERT INTO artifact_download_scopes
               (scope_id, scope_kind, description, artifact_types_json,
                policy_json, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                scope_id,
                scope_kind,
                description,
                artifact_types_json,
                policy_json,
                now,
                now,
            ),
        )
        conn.executemany(
            """INSERT INTO artifact_download_scope_companies
               (scope_id, company_id, symbol, outcome_label, archetype)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (scope_id, company_id, item.symbol, item.outcome_label, item.archetype)
                for company_id, item in resolved
            ],
        )
        conn.executemany(
            """INSERT INTO artifact_download_scope_artifacts
               (scope_id, artifact_id) VALUES (?, ?)""",
            [(scope_id, artifact_id) for artifact_id in selected_artifact_ids],
        )
    return {
        "scope_id": scope_id,
        "scope_kind": scope_kind,
        "company_count": len(resolved),
        "artifact_count": len(selected_artifact_ids),
        "artifact_types": list(selected_types),
        "created": True,
        "policy": policy,
    }


def load_scope_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the JSON-facing shape used by ``queue-document-downloads``."""

    if not isinstance(payload.get("companies"), list):
        raise ValueError("Scope manifest must contain a companies list")
    return {
        "scope_id": str(payload.get("scope_id", "")),
        "scope_kind": str(payload.get("scope_kind", "P0")),
        "description": payload.get("description"),
        "artifact_types": payload.get("artifact_types", list(P0_ARTIFACT_TYPES)),
        "artifact_ids": payload.get("artifact_ids", []),
        "companies": payload["companies"],
        "recording_fallback_only": payload.get("recording_fallback_only"),
    }
