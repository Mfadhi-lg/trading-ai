"""Freeze the complete in-policy transcript/presentation/rating acquisition scope."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import DB_PATH, assert_configured_writable_targets
from ..core.access_policy import host_matches_policy
from ..db.database import connect, init_db
from ..db.document_download_queue_repo import blocked_terms_hosts
from ..documents.download_scope import P0_ARTIFACT_TYPES


def _valid_host(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname.lower()


def build_full_scope(conn: Any, *, scope_id: str) -> dict[str, Any]:
    policy_hosts = blocked_terms_hosts(conn)
    sources: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(
        """SELECT artifact_id,artifact_source_id,document_url,
                  automation_status,retrieval_source_type,recovery_target_id
             FROM artifact_sources ORDER BY artifact_id,artifact_source_id"""
    ):
        sources[int(row["artifact_id"])].append(dict(row))
    content_artifacts = {
        int(row[0]) for row in conn.execute("SELECT DISTINCT artifact_id FROM artifact_contents")
    }
    recovery = {
        int(row["artifact_id"]): str(row["status"])
        for row in conn.execute(
            "SELECT artifact_id,status FROM artifact_recovery_targets ORDER BY artifact_id"
        )
    }
    quarantined = {
        int(row[0]) for row in conn.execute("SELECT artifact_id FROM artifact_recovery_quarantine")
    }
    identity = {
        int(row["company_id"]): str(row["symbol"])
        for row in conn.execute(
            """SELECT company_id,MIN(identifier_value) AS symbol
                 FROM security_identifiers
                WHERE identifier_type='NSE' AND is_current=1 GROUP BY company_id"""
        )
    }
    artifact_rows = list(
        conn.execute(
            """SELECT artifact.artifact_id,artifact.company_id,
                      company.current_legal_name,artifact.artifact_type
                 FROM document_artifacts AS artifact
                 JOIN companies AS company USING(company_id)
                WHERE artifact.artifact_type IN
                      ('transcript','presentation','credit_rating')
                ORDER BY artifact.artifact_id"""
        )
    )
    accounting: list[dict[str, Any]] = []
    companies: dict[int, dict[str, Any]] = {}
    by_type = Counter()
    by_state = Counter()
    by_type_state: dict[str, Counter[str]] = defaultdict(Counter)
    for row in artifact_rows:
        artifact_id = int(row["artifact_id"])
        company_id = int(row["company_id"])
        artifact_type = str(row["artifact_type"])
        companies[company_id] = {
            "company_id": company_id,
            "symbol": identity.get(company_id, f"COMPANY:{company_id}"),
            "company_name": str(row["current_legal_name"]),
        }
        artifact_sources = sources.get(artifact_id, [])
        valid_sources = [
            (item, _valid_host(str(item["document_url"])))
            for item in artifact_sources
        ]
        valid_sources = [(item, host) for item, host in valid_sources if host]
        permitted = [
            (item, host)
            for item, host in valid_sources
            if not host_matches_policy(str(host), policy_hosts)
            and item["automation_status"] != "BLOCKED_TERMS"
        ]
        blocked = [
            (item, host)
            for item, host in valid_sources
            if host_matches_policy(str(host), policy_hosts)
            or item["automation_status"] == "BLOCKED_TERMS"
        ]
        recovery_status = recovery.get(artifact_id)
        if artifact_id in content_artifacts:
            state = "ALREADY_ACQUIRED"
        elif artifact_id in quarantined:
            state = "QUARANTINED"
        elif recovery_status and recovery_status not in {"RECOVERED", "ALREADY_SATISFIED"}:
            state = "RECOVERY_REQUIRED"
        elif permitted:
            state = "PENDING"
        elif blocked:
            state = "POLICY_BLOCKED_DIRECT"
        elif not valid_sources:
            state = "NO_VALID_URL"
        else:
            state = "OTHER"
        by_type[artifact_type] += 1
        by_state[state] += 1
        by_type_state[artifact_type][state] += 1
        accounting.append(
            {
                "artifact_id": artifact_id,
                "company_id": company_id,
                "artifact_type": artifact_type,
                "scope_state": state,
                "source_count": len(artifact_sources),
                "valid_url_count": len(valid_sources),
                "permitted_url_count": len(permitted),
                "blocked_url_count": len(blocked),
                "recovery_status": recovery_status,
            }
        )
    return {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope_id": scope_id,
        "scope_kind": "custom",
        "description": "Complete in-policy Document Acquisition corpus; not Focus ML P1",
        "artifact_types": list(P0_ARTIFACT_TYPES),
        "companies": [companies[key] for key in sorted(companies)],
        "company_ids": sorted(companies),
        "artifact_ids": [int(row["artifact_id"]) for row in artifact_rows],
        "counts": {
            "companies": len(companies),
            "artifacts": len(artifact_rows),
            "by_artifact_type": dict(sorted(by_type.items())),
            "by_scope_state": dict(sorted(by_state.items())),
            "by_artifact_type_and_state": {
                key: dict(sorted(value.items()))
                for key, value in sorted(by_type_state.items())
            },
        },
        "artifact_accounting": accounting,
        "invariants": {
            "automatic_types_only": set(by_type) <= set(P0_ARTIFACT_TYPES),
            "recordings_excluded": "recording" not in by_type,
            "focus_governance_fields_required": False,
            "every_artifact_accounted": len(accounting) == len(artifact_rows),
            "state_counts_reconcile": sum(by_state.values()) == len(artifact_rows),
        },
    }


def write_scope(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "09_FULL_ACQUISITION_SCOPE.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    counts = payload["counts"]
    lines = [
        "# Full High-Value Acquisition Scope",
        "",
        f"Scope: `{payload['scope_id']}`",
        "",
        f"Companies: **{counts['companies']}**",
        f"Artifacts: **{counts['artifacts']}**",
        "",
        "## By document type",
        "",
        *[f"- {key}: {value}" for key, value in counts["by_artifact_type"].items()],
        "",
        "## Mutually exclusive scope state",
        "",
        *[f"- {key}: {value}" for key, value in counts["by_scope_state"].items()],
        "",
        "## Invariants",
        "",
        *[f"- {key}: {'PASS' if value is True else value}" for key, value in payload["invariants"].items()],
        "",
        "Recordings, annual reports, DRHP, generic announcements, and Screener notes are excluded.",
    ]
    (output_dir / "09_FULL_ACQUISITION_SCOPE.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--scope-id", default="document-acquisition-full-v1")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    conn = connect(args.db)
    try:
        init_db(conn)
        payload = build_full_scope(conn, scope_id=args.scope_id)
    finally:
        conn.close()
    write_scope(payload, args.output_dir)
    print(json.dumps(payload["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
