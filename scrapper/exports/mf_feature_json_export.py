"""M06 stock-month feature export with exact delta and workbook lineage."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..db.mf_feature_repo import FEATURE_SCOPES


def build_mf_feature_export(
    conn: sqlite3.Connection, *, scope: str, through_month: str,
    publisher: str | None = None,
) -> dict:
    if scope not in FEATURE_SCOPES:
        raise ValueError(f"Unknown mutual-fund feature scope: {scope}")
    filters = ["feature.feature_scope=?", "feature.disclosure_month<=?"]
    params: list[object] = [scope, through_month]
    if publisher is not None:
        filters.append("feature.publisher=?")
        params.append(publisher)
    features = conn.execute(
        f"""SELECT feature.*
            FROM mf_current_stock_monthly_features AS feature
            WHERE {' AND '.join(filters)}
            ORDER BY feature.publisher, feature.disclosure_month,
                     feature.isin_normalized""",
        params,
    ).fetchall()
    feature_ids = [int(row["stock_month_feature_id"]) for row in features]
    lineage: dict[int, list[dict]] = {feature_id: [] for feature_id in feature_ids}
    if feature_ids:
        placeholders = ",".join("?" for _ in feature_ids)
        for row in conn.execute(
            f"""SELECT lineage.stock_month_feature_id,
                       change.scheme_security_change_id, change.change_set_id,
                       change.scheme_id, scheme.scheme_code,
                       classification.scheme_management_type,
                       classification.is_equity_scheme,
                       classification.scheme_category,
                       classification.classification_source,
                       classification.classification_rule_version,
                       classification.scheme_classification_id,
                       change.change_class, change.previous_row_presence,
                       change.current_row_presence,
                       change.previous_quantity_decimal,
                       change.current_quantity_decimal,
                       change.quantity_change_decimal,
                       change.previous_weight_decimal,
                       change.current_weight_decimal,
                       change.portfolio_weight_change_decimal,
                       change.previous_import_file_id,
                       change.current_import_file_id,
                       lineage.previous_content_sha256,
                       lineage.current_content_sha256
                FROM mf_stock_month_feature_lineage AS lineage
                JOIN mf_scheme_security_month_changes AS change
                  ON change.scheme_security_change_id=lineage.scheme_security_change_id
                JOIN mf_schemes AS scheme ON scheme.scheme_id=change.scheme_id
                JOIN mf_scheme_classifications AS classification
                  ON classification.scheme_classification_id=lineage.scheme_classification_id
                WHERE lineage.stock_month_feature_id IN ({placeholders})
                ORDER BY lineage.stock_month_feature_id, change.scheme_id""",
            feature_ids,
        ):
            feature_id = int(row["stock_month_feature_id"])
            item = dict(row)
            item.pop("stock_month_feature_id")
            item["is_equity_scheme"] = bool(item["is_equity_scheme"])
            lineage[feature_id].append(item)
    set_ids = sorted({int(row["feature_set_id"]) for row in features})
    feature_sets: list[dict] = []
    workbooks: dict[tuple[int, str], dict] = {}
    for feature_set_id in set_ids:
        feature_set = conn.execute(
            """SELECT * FROM mf_current_stock_month_feature_sets
               WHERE feature_set_id=?""", (feature_set_id,)
        ).fetchone()
        inputs = []
        for row in conn.execute(
            """SELECT input.*, scheme.scheme_code,
                      classification.scheme_management_type,
                      classification.is_equity_scheme,
                      classification.classification_rule_version
               FROM mf_stock_month_feature_inputs AS input
               JOIN mf_schemes AS scheme ON scheme.scheme_id=input.scheme_id
               JOIN mf_scheme_classifications AS classification
                 ON classification.scheme_classification_id=input.scheme_classification_id
               WHERE input.feature_set_id=? ORDER BY input.scheme_id""",
            (feature_set_id,),
        ):
            item = dict(row)
            item["is_equity_scheme"] = bool(item["is_equity_scheme"])
            inputs.append(item)
            for side in ("previous", "current"):
                import_id = int(row[f"{side}_import_file_id"])
                content_hash = str(row[f"{side}_content_sha256"])
                workbooks[(import_id, content_hash)] = {
                    "import_file_id": import_id,
                    "content_sha256": content_hash,
                }
        validations = [
            {
                **{
                    key: row[key]
                    for key in ("rule_code", "severity", "validation_status")
                },
                "details": json.loads(row["details_json"]),
            }
            for row in conn.execute(
                """SELECT * FROM mf_stock_month_feature_validations
                   WHERE feature_set_id=? ORDER BY rule_code""",
                (feature_set_id,),
            )
        ]
        feature_sets.append(
            {
                **{
                    key: feature_set[key]
                    for key in (
                        "feature_set_id", "publisher", "previous_month",
                        "current_month", "aggregation_status", "reason_code",
                        "engine_version", "input_fingerprint", "generated_at_utc",
                    )
                },
                "completeness_evidence": json.loads(
                    feature_set["completeness_evidence_json"]
                ),
                "inputs": inputs,
                "validations": validations,
            }
        )
    return {
        "schema_version": "1.0.0",
        "selection": {
            "publisher": publisher,
            "scope": scope,
            "through_month": through_month,
        },
        "provenance": {
            "official_workbooks": [
                workbooks[key] for key in sorted(workbooks)
            ]
        },
        "features": [
            {
                **dict(row),
                "number_of_funds_increasing": row["funds_increasing_quantity"],
                "lineage": lineage[int(row["stock_month_feature_id"])],
            }
            for row in features
        ],
        "quality": {"feature_sets": feature_sets},
    }


def write_mf_feature_export(
    conn: sqlite3.Connection, *, scope: str, through_month: str,
    output: Path, publisher: str | None = None,
) -> Path:
    payload = build_mf_feature_export(
        conn, scope=scope, through_month=through_month, publisher=publisher
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output
