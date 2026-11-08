"""Build the label-free, deterministic Document Acquisition P0 scope."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..config import DB_PATH, assert_configured_writable_targets
from ..db.database import connect, init_db
from ..documents.download_scope import P0_ARTIFACT_TYPES


SCHEMA_VERSION = "1.0.0"
DEFAULT_SEED = "document-acquisition-p0-v1"
DEFAULT_COMPANY_COUNT = 40
FEATURE_ORDER = (
    "artifact:transcript",
    "artifact:presentation",
    "artifact:credit_rating",
    "source:direct_official",
    "source:nse_attachment",
    "source:bse_attachment",
    "source:issuer_hosted",
    "source:alternate_rating_recovery",
    "source:blocked_cra_original",
    "shape:small",
    "shape:large",
    "shape:multi_page",
    "shape:ocr",
    "shape:redirect",
    "shape:duplicate_content",
    "edge:retryable",
    "edge:terminal",
    "edge:missing_source",
    "edge:unknown_rating_evidence",
    "edge:valid_source",
)


@dataclass(frozen=True)
class ArtifactCandidate:
    artifact_id: int
    company_id: int
    company_name: str
    symbol: str
    artifact_type: str
    event_date: str | None
    period_end: str | None
    fiscal_year: int | None
    fiscal_quarter: int | None
    source_hosts: tuple[str, ...]
    retrieval_types: tuple[str, ...]
    queue_states: tuple[str, ...]
    content_ids: tuple[int, ...]
    blob_ids: tuple[int, ...]
    byte_size: int | None
    page_count: int | None
    ocr_page_count: int
    redirect_seen: bool
    recovery_status: str | None
    evidence_classes: tuple[str, ...]
    features: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "company_id": self.company_id,
            "artifact_type": self.artifact_type,
            "event_date": self.event_date,
            "period_end": self.period_end,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "source_hosts": list(self.source_hosts),
            "retrieval_types": list(self.retrieval_types),
            "queue_states": list(self.queue_states),
            "content_ids": list(self.content_ids),
            "blob_ids": list(self.blob_ids),
            "byte_size": self.byte_size,
            "page_count": self.page_count,
            "ocr_page_count": self.ocr_page_count,
            "redirect_seen": self.redirect_seen,
            "recovery_status": self.recovery_status,
            "evidence_classes": list(self.evidence_classes),
            "features": list(self.features),
        }


def _stable_key(seed: str, *values: object) -> str:
    material = "|".join((seed, *(str(value) for value in values)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _matches(host: str, base: str) -> bool:
    return host == base or host.endswith("." + base)


def _rows_by_artifact(
    conn: sqlite3.Connection, sql: str
) -> dict[int, list[sqlite3.Row]]:
    result: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute(sql):
        result[int(row["artifact_id"])].append(row)
    return result


def _load_candidates(conn: sqlite3.Connection) -> tuple[ArtifactCandidate, ...]:
    sources = _rows_by_artifact(
        conn,
        """SELECT artifact_id, document_url, discovered_from_source,
                  retrieval_source_type,
                  automation_status, recovery_target_id
             FROM artifact_sources ORDER BY artifact_id, artifact_source_id""",
    )
    contents = _rows_by_artifact(
        conn,
        """SELECT artifact_id, content_id, blob_id, byte_size
             FROM artifact_contents ORDER BY artifact_id, content_id""",
    )
    queue = _rows_by_artifact(
        conn,
        """SELECT artifact_id, status FROM artifact_download_queue
           UNION ALL
           SELECT artifact_id, status FROM credit_rating_download_queue
           ORDER BY artifact_id""",
    )
    extraction = {
        int(row["artifact_id"]): row
        for row in conn.execute(
            """SELECT content.artifact_id,
                      MAX(extraction.page_or_slide_count) AS page_count,
                      MAX(extraction.ocr_page_count) AS ocr_page_count
                 FROM document_extractions AS extraction
                 JOIN artifact_contents AS content USING (content_id)
                GROUP BY content.artifact_id"""
        )
    }
    recovery = {
        int(row["artifact_id"]): row
        for row in conn.execute(
            """SELECT artifact_id, status FROM artifact_recovery_targets
               ORDER BY artifact_id"""
        )
    }
    evidence = _rows_by_artifact(
        conn,
        """SELECT source.artifact_id, classification.content_class
             FROM artifact_source_evidence_classifications AS classification
             JOIN artifact_sources AS source USING (artifact_source_id)
            ORDER BY source.artifact_id, classification.classification_id""",
    )
    redirects = {
        int(row["artifact_id"])
        for row in conn.execute(
            """SELECT DISTINCT artifact_id FROM artifact_download_attempts
                WHERE redirect_chain_json NOT IN ('[]', '')"""
        )
    }
    duplicate_blobs = {
        int(row["blob_id"])
        for row in conn.execute(
            """SELECT blob_id FROM artifact_contents GROUP BY blob_id
               HAVING COUNT(DISTINCT artifact_id) > 1"""
        )
    }
    identity = {
        int(row["company_id"]): str(row["symbol"])
        for row in conn.execute(
            """SELECT company_id, MIN(identifier_value) AS symbol
                 FROM security_identifiers
                WHERE identifier_type='NSE' AND is_current=1
                GROUP BY company_id"""
        )
    }
    rows = conn.execute(
        """SELECT artifact.artifact_id, artifact.company_id,
                  company.current_legal_name, artifact.artifact_type,
                  artifact.event_date, artifact.period_end,
                  artifact.fiscal_year, artifact.fiscal_quarter
             FROM document_artifacts AS artifact
             JOIN companies AS company USING (company_id)
            WHERE artifact.artifact_type IN ('transcript','presentation','credit_rating')
            ORDER BY artifact.artifact_id"""
    )
    candidates: list[ArtifactCandidate] = []
    for row in rows:
        artifact_id = int(row["artifact_id"])
        source_rows = sources.get(artifact_id, [])
        content_rows = contents.get(artifact_id, [])
        hosts = tuple(sorted({_host(str(item["document_url"])) for item in source_rows if _host(str(item["document_url"]))}))
        retrieval_types = tuple(
            sorted(
                {
                    str(item["retrieval_source_type"])
                    for item in source_rows
                    if item["retrieval_source_type"]
                }
            )
        )
        states = tuple(sorted({str(item["status"]) for item in queue.get(artifact_id, [])}))
        content_ids = tuple(int(item["content_id"]) for item in content_rows)
        blob_ids = tuple(int(item["blob_id"]) for item in content_rows)
        sizes = [int(item["byte_size"]) for item in content_rows]
        extraction_row = extraction.get(artifact_id)
        evidence_classes = tuple(
            sorted({str(item["content_class"]) for item in evidence.get(artifact_id, [])})
        )
        features = {f"artifact:{row['artifact_type']}"}
        if any(_matches(host, "nseindia.com") for host in hosts) or any(
            item.startswith("NSE_") for item in retrieval_types
        ):
            features.add("source:nse_attachment")
        if any(_matches(host, "bseindia.com") for host in hosts) or any(
            item.startswith("BSE_") for item in retrieval_types
        ):
            features.add("source:bse_attachment")
        if "ISSUER_IR_ATTACHMENT" in retrieval_types or any(
            "issuer" in str(item["discovered_from_source"]).lower()
            or "company_ir" in str(item["discovered_from_source"]).lower()
            for item in source_rows
        ):
            features.add("source:issuer_hosted")
        if any(
            host
            and not any(
                _matches(host, base)
                for base in (
                    "screener.in", "nseindia.com", "bseindia.com",
                    "crisil.com", "crisilratings.com", "icra.in",
                )
            )
            for host in hosts
        ):
            features.add("source:direct_official")
        if any(item["recovery_target_id"] is not None for item in source_rows):
            features.add("source:alternate_rating_recovery")
        if any(
            str(item["automation_status"]) == "BLOCKED_TERMS"
            for item in source_rows
        ):
            features.add("source:blocked_cra_original")
        if not source_rows:
            features.add("edge:missing_source")
        if any(_host(str(item["document_url"])) for item in source_rows):
            features.add("edge:valid_source")
        if "RETRYABLE_FAILURE" in states:
            features.add("edge:retryable")
        if "TERMINAL_FAILURE" in states:
            features.add("edge:terminal")
        if "UNKNOWN_RATING_DOCUMENT" in evidence_classes:
            features.add("edge:unknown_rating_evidence")
        if sizes and min(sizes) <= 256 * 1024:
            features.add("shape:small")
        if sizes and max(sizes) >= 5 * 1024 * 1024:
            features.add("shape:large")
        if extraction_row is not None and int(extraction_row["page_count"] or 0) >= 10:
            features.add("shape:multi_page")
        if extraction_row is not None and int(extraction_row["ocr_page_count"] or 0) > 0:
            features.add("shape:ocr")
        if artifact_id in redirects:
            features.add("shape:redirect")
        if any(blob_id in duplicate_blobs for blob_id in blob_ids):
            features.add("shape:duplicate_content")
        candidates.append(
            ArtifactCandidate(
                artifact_id=artifact_id,
                company_id=int(row["company_id"]),
                company_name=str(row["current_legal_name"]),
                symbol=identity.get(int(row["company_id"]), f"COMPANY:{row['company_id']}"),
                artifact_type=str(row["artifact_type"]),
                event_date=row["event_date"],
                period_end=row["period_end"],
                fiscal_year=row["fiscal_year"],
                fiscal_quarter=row["fiscal_quarter"],
                source_hosts=hosts,
                retrieval_types=retrieval_types,
                queue_states=states,
                content_ids=content_ids,
                blob_ids=blob_ids,
                byte_size=max(sizes) if sizes else None,
                page_count=int(extraction_row["page_count"]) if extraction_row and extraction_row["page_count"] is not None else None,
                ocr_page_count=int(extraction_row["ocr_page_count"] or 0) if extraction_row else 0,
                redirect_seen=artifact_id in redirects,
                recovery_status=str(recovery[artifact_id]["status"]) if artifact_id in recovery else None,
                evidence_classes=evidence_classes,
                features=tuple(sorted(features)),
            )
        )
    return tuple(candidates)


def _select(
    candidates: Iterable[ArtifactCandidate], *, company_count: int, seed: str
) -> tuple[tuple[ArtifactCandidate, ...], dict[str, int]]:
    if not 30 <= company_count <= 50:
        raise ValueError("Production acquisition P0 requires 30 to 50 companies")
    candidates = tuple(candidates)
    by_company: dict[int, list[ArtifactCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_company[candidate.company_id].append(candidate)
    if len(by_company) < company_count:
        raise ValueError(
            f"Only {len(by_company)} companies have in-policy artifacts; "
            f"cannot select {company_count}"
        )
    selected_companies: set[int] = set()
    representatives: dict[int, ArtifactCandidate] = {}
    feature_counts: Counter[str] = Counter()
    for feature in FEATURE_ORDER:
        choices = [
            candidate
            for candidate in candidates
            if feature in candidate.features and candidate.company_id not in selected_companies
        ]
        if not choices:
            continue
        choice = min(
            choices,
            key=lambda item: _stable_key(seed, feature, item.company_id, item.artifact_id),
        )
        selected_companies.add(choice.company_id)
        representatives[choice.artifact_id] = choice
        feature_counts[feature] += 1
    ranked_companies = sorted(
        by_company,
        key=lambda company_id: (
            -len({feature for item in by_company[company_id] for feature in item.features}),
            -len({item.artifact_type for item in by_company[company_id]}),
            _stable_key(seed, "company", company_id),
        ),
    )
    for company_id in ranked_companies:
        if len(selected_companies) >= company_count:
            break
        selected_companies.add(company_id)
    selected: dict[int, ArtifactCandidate] = dict(representatives)
    for company_id in sorted(selected_companies):
        for artifact_type in P0_ARTIFACT_TYPES:
            choices = [
                item
                for item in by_company[company_id]
                if item.artifact_type == artifact_type
            ]
            if not choices:
                continue
            choice = min(
                choices,
                key=lambda item: (
                    0 if "PENDING" in item.queue_states else 1,
                    0 if "RETRYABLE_FAILURE" in item.queue_states else 1,
                    0 if not item.content_ids else 1,
                    _stable_key(seed, "artifact", company_id, artifact_type, item.artifact_id),
                ),
            )
            selected[choice.artifact_id] = choice
    rows = tuple(sorted(selected.values(), key=lambda item: (item.company_id, item.artifact_type, item.artifact_id)))
    if len({item.company_id for item in rows}) != company_count:
        raise RuntimeError("P0 selection lost a selected company")
    return rows, dict(sorted(feature_counts.items()))


def build_acquisition_p0(
    conn: sqlite3.Connection,
    *,
    company_count: int = DEFAULT_COMPANY_COUNT,
    seed: str = DEFAULT_SEED,
    scope_id: str = "document-acquisition-p0-v1",
) -> dict[str, Any]:
    candidates = _load_candidates(conn)
    selected, seeded_features = _select(candidates, company_count=company_count, seed=seed)
    rerun, _ = _select(candidates, company_count=company_count, seed=seed)
    membership = tuple(item.artifact_id for item in selected)
    if membership != tuple(item.artifact_id for item in rerun):
        raise RuntimeError("Deterministic P0 rerun changed artifact membership")
    companies_by_id = {
        item.company_id: {
            "company_id": item.company_id,
            "symbol": item.symbol,
            "company_name": item.company_name,
        }
        for item in selected
    }
    feature_inventory = Counter(
        feature for item in selected for feature in item.features
    )
    type_counts = Counter(item.artifact_type for item in selected)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope_id": scope_id,
        "scope_kind": "P0",
        "description": "Label-free Document Acquisition engineering-diversity pilot",
        "selection_seed": seed,
        "selection_policy": "engineering_diversity_only",
        "artifact_types": list(P0_ARTIFACT_TYPES),
        "companies": [companies_by_id[key] for key in sorted(companies_by_id)],
        "company_ids": sorted(companies_by_id),
        "artifact_ids": list(membership),
        "selected_artifacts": [item.to_dict() for item in selected],
        "counts": {
            "companies": len(companies_by_id),
            "artifacts": len(selected),
            "by_artifact_type": dict(sorted(type_counts.items())),
        },
        "feature_inventory": dict(sorted(feature_inventory.items())),
        "seeded_feature_representatives": seeded_features,
        "invariants": {
            "company_count_30_to_50": 30 <= len(companies_by_id) <= 50,
            "artifact_company_ownership": all(item.company_id in companies_by_id for item in selected),
            "automatic_types_only": set(type_counts) <= set(P0_ARTIFACT_TYPES),
            "recordings_excluded": "recording" not in type_counts,
            "focus_governance_fields_required": False,
            "deterministic_membership_rerun": True,
        },
    }


def write_acquisition_p0(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "P0_ACQUISITION_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_dir / "P0_ACQUISITION_MANIFEST.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fields = (
            "company_id", "symbol", "company_name", "artifact_id", "artifact_type",
            "event_date", "period_end", "source_hosts", "queue_states", "byte_size",
            "features",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        companies = {item["company_id"]: item for item in payload["companies"]}
        for artifact in payload["selected_artifacts"]:
            company = companies[artifact["company_id"]]
            writer.writerow(
                {
                    "company_id": artifact["company_id"],
                    "symbol": company["symbol"],
                    "company_name": company["company_name"],
                    "artifact_id": artifact["artifact_id"],
                    "artifact_type": artifact["artifact_type"],
                    "event_date": artifact["event_date"] or "",
                    "period_end": artifact["period_end"] or "",
                    "source_hosts": "|".join(artifact["source_hosts"]),
                    "queue_states": "|".join(artifact["queue_states"]),
                    "byte_size": artifact["byte_size"] or "",
                    "features": "|".join(artifact["features"]),
                }
            )
    counts = payload["counts"]
    features = payload["feature_inventory"]
    lines = [
        "# P0 Acquisition Selection Report",
        "",
        f"Scope: `{payload['scope_id']}`",
        f"Seed: `{payload['selection_seed']}`",
        "",
        f"Companies: **{counts['companies']}**",
        f"Artifacts: **{counts['artifacts']}**",
        "",
        "## Artifact types",
        "",
        *[f"- {key}: {value}" for key, value in counts["by_artifact_type"].items()],
        "",
        "## Engineering-diversity evidence",
        "",
        *[f"- {key}: {value}" for key, value in features.items()],
        "",
        "## Invariants",
        "",
        *[f"- {key}: {'PASS' if value is True else value}" for key, value in payload["invariants"].items()],
        "",
        "No Focus labels, decision-universe membership, archetypes, or ML features were used.",
    ]
    (output_dir / "P0_SELECTION_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--company-count", type=int, default=DEFAULT_COMPANY_COUNT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--scope-id", default="document-acquisition-p0-v1")
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    conn = connect(args.db)
    try:
        init_db(conn)
        payload = build_acquisition_p0(
            conn,
            company_count=args.company_count,
            seed=args.seed,
            scope_id=args.scope_id,
        )
    finally:
        conn.close()
    write_acquisition_p0(payload, args.output_dir)
    print(json.dumps(payload["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
