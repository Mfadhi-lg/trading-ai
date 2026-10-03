"""Run D03 basic extraction for every acquired document in an explicit scope."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import DB_PATH, assert_configured_writable_targets
from ..db.database import connect, init_db
from ..documents.downloader import PDF, PPT, PPTX
from ..documents.html import HTML
from .extract_document import extract_document


def _selected_contents(
    conn: Any,
    scope_id: str,
    *,
    pending_only: bool = False,
    limit: int | None = None,
    artifact_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    status_filter = " AND extraction_status='not_started'" if pending_only else ""
    type_filter = ""
    query_parameters: list[Any] = [scope_id]
    if artifact_types:
        type_filter = " AND artifact.artifact_type IN ({})".format(
            ",".join("?" for _ in artifact_types)
        )
        query_parameters.extend(artifact_types)
    limit_clause = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        query_parameters.append(limit)
    return [
        dict(row)
        for row in conn.execute(
            f"""WITH ranked AS (
                 SELECT artifact.artifact_id,artifact.artifact_type,
                        content.content_id,content.media_type,
                        content.extraction_status,
                        ROW_NUMBER() OVER (
                          PARTITION BY artifact.artifact_id
                          ORDER BY (content.content_id=artifact.preferred_content_id) DESC,
                                   content.is_official DESC,content.content_id DESC
                        ) AS position
                   FROM artifact_download_scope_artifacts AS selected
                   JOIN document_artifacts AS artifact USING (artifact_id)
                  JOIN artifact_contents AS content USING (artifact_id)
                  WHERE selected.scope_id=? {type_filter}
               ) SELECT artifact_id,artifact_type,content_id,media_type
                   FROM ranked WHERE position=1 {status_filter}
                   ORDER BY artifact_id{limit_clause}""",
            tuple(query_parameters),
        )
    ]


def run_scope_extraction(
    *,
    scope_id: str,
    db_path: Path,
    output_dir: Path,
    pending_only: bool = False,
    limit: int | None = None,
    report_prefix: str = "07_P0_EXTRACTION_REPORT",
    artifact_types: tuple[str, ...] = (),
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        init_db(conn)
        selected = _selected_contents(
            conn,
            scope_id,
            pending_only=pending_only,
            limit=limit,
            artifact_types=artifact_types,
        )
    finally:
        conn.close()
    results: list[dict[str, Any]] = []
    supported = {PDF, PPT, PPTX, HTML}
    for item in selected:
        if item["media_type"] not in supported or (
            item["media_type"] == HTML and item["artifact_type"] != "credit_rating"
        ):
            marker = connect(db_path)
            try:
                with marker:
                    marker.execute(
                        """UPDATE artifact_contents SET extraction_status='failed'
                            WHERE content_id=? AND extraction_status='not_started'""",
                        (int(item["content_id"]),),
                    )
            finally:
                marker.close()
            results.append(
                {
                    **item,
                    "status": "unusable",
                    "error": f"unsupported media type {item['media_type']}",
                }
            )
            continue
        try:
            payload = extract_document(
                artifact_id=int(item["artifact_id"]),
                content_id=int(item["content_id"]),
                db_path=db_path,
                output=(
                    output_dir
                    / "extraction-payloads"
                    / scope_id
                    / f"artifact-{item['artifact_id']}-content-{item['content_id']}.json"
                ),
            )
            extraction = payload["extraction"]
            results.append(
                {
                    **item,
                    "status": extraction["extraction_status"],
                    "reused": payload["reused"],
                    "extraction_id": extraction["extraction_id"],
                    "page_or_slide_count": extraction["page_or_slide_count"],
                    "native_page_count": extraction["native_page_count"],
                    "ocr_page_count": extraction["ocr_page_count"],
                    "unusable_page_count": extraction["unusable_page_count"],
                    "page_element_count": sum(
                        len(page["elements"]) for page in extraction["pages"]
                    ),
                    "error": extraction["error"],
                }
            )
        except Exception as exc:
            results.append(
                {
                    **item,
                    "status": "failed",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
    conn = connect(db_path)
    try:
        rating_evidence = [
            dict(row)
            for row in conn.execute(
                """WITH ranked_classification AS (
                     SELECT classification.*,
                            ROW_NUMBER() OVER (
                              PARTITION BY artifact_source_id,content_id,classifier_name
                              ORDER BY classification_id DESC
                            ) AS position
                       FROM artifact_source_evidence_classifications AS classification
                   )
                   SELECT source.artifact_id,classification.content_id,
                          classification.content_class,
                          recovery.status AS recovery_status,
                          recovery.basic_rating_coverage,
                          recovery.full_rationale_coverage
                     FROM artifact_download_scope_artifacts AS selected
                     JOIN artifact_sources AS source USING (artifact_id)
                     JOIN ranked_classification AS classification
                       USING (artifact_source_id)
                     LEFT JOIN artifact_recovery_targets AS recovery
                       ON recovery.artifact_id=source.artifact_id
                    WHERE selected.scope_id=? AND classification.position=1
                    ORDER BY source.artifact_id,
                          classification.classification_id""",
                (scope_id,),
            )
        ]
    finally:
        conn.close()
    by_type: dict[str, dict[str, Any]] = {}
    for artifact_type in ("transcript", "presentation", "credit_rating"):
        items = [item for item in results if item["artifact_type"] == artifact_type]
        states = Counter(str(item["status"]) for item in items)
        by_type[artifact_type] = {
            "attempted": len(items),
            "ok": states.get("ok", 0),
            "partial": states.get("partial", 0),
            "failed": states.get("failed", 0),
            "unusable": states.get("unusable", 0),
            "native_pages": sum(int(item.get("native_page_count", 0)) for item in items),
            "ocr_pages": sum(int(item.get("ocr_page_count", 0)) for item in items),
            "page_or_slide_count": sum(int(item.get("page_or_slide_count", 0)) for item in items),
            "page_element_count": sum(int(item.get("page_element_count", 0)) for item in items),
        }
    evidence_counts = Counter(str(item["content_class"]) for item in rating_evidence)
    report = {
        "schema_version": "1.0.0",
        "scope_id": scope_id,
        "by_artifact_type": by_type,
        "rating_evidence_class_counts": dict(sorted(evidence_counts.items())),
        "rating_evidence": rating_evidence,
        "results": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{report_prefix}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = ["# P0 D03 Extraction Report", "", f"Scope: `{scope_id}`", ""]
    for artifact_type, values in by_type.items():
        lines.extend(
            [
                f"## {artifact_type}",
                "",
                *[f"- {key}: {value}" for key, value in values.items()],
                "",
            ]
        )
    lines.extend(
        [
            "## Rating evidence classes",
            "",
            *[f"- {key}: {value}" for key, value in sorted(evidence_counts.items())],
        ]
    )
    (output_dir / f"{report_prefix}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pending-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report-prefix", default="07_P0_EXTRACTION_REPORT")
    parser.add_argument(
        "--artifact-type",
        action="append",
        choices=("transcript", "presentation", "credit_rating"),
        default=[],
    )
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    report = run_scope_extraction(
        scope_id=args.scope_id,
        db_path=args.db,
        output_dir=args.output_dir,
        pending_only=args.pending_only,
        limit=args.limit,
        report_prefix=args.report_prefix,
        artifact_types=tuple(args.artifact_type),
    )
    print(json.dumps(report["by_artifact_type"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
