"""Deterministic M09 gap/conflict and source-precedence report."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def build_mfdata_crosscheck_export(
    conn: sqlite3.Connection, mfdata_crosscheck_run_id: int
) -> dict:
    run = conn.execute(
        """SELECT run.*, snapshot.provider, snapshot.source,
                  snapshot.source_quality, snapshot.scheme_code,
                  snapshot.provider_family_id, snapshot.response_url,
                  snapshot.content_sha256, snapshot.byte_size,
                  snapshot.submitted_path, snapshot.immutable_path,
                  snapshot.parser_version, snapshot.first_observed_at_utc,
                  snapshot.accepted_at_utc
           FROM mfdata_crosscheck_runs AS run
           JOIN mfdata_snapshots AS snapshot
             ON snapshot.mfdata_snapshot_id=run.mfdata_snapshot_id
           WHERE run.mfdata_crosscheck_run_id=?""",
        (mfdata_crosscheck_run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"Unknown mfdata cross-check run: {mfdata_crosscheck_run_id}")
    holdings = conn.execute(
        """SELECT * FROM mfdata_holdings WHERE mfdata_snapshot_id=?
           ORDER BY source_position""",
        (run["mfdata_snapshot_id"],),
    ).fetchall()
    results = conn.execute(
        """SELECT * FROM mfdata_crosscheck_results
           WHERE mfdata_crosscheck_run_id=? ORDER BY result_position""",
        (mfdata_crosscheck_run_id,),
    ).fetchall()
    historical_evidence: list[dict] = []
    holdings_by_id = {int(row["mfdata_holding_id"]): row for row in holdings}
    for result in results:
        for holding_id in json.loads(result["official_holding_ids_json"]):
            official = conn.execute(
                "SELECT * FROM mf_holdings WHERE holding_id=?", (holding_id,)
            ).fetchone()
            if official is None:
                raise RuntimeError(
                    f"Cross-check result refers to missing official holding {holding_id}"
                )
            historical_evidence.append({
                "observation_key": f"official:{holding_id}",
                "source_observation_id": holding_id,
                "publisher": official["publisher"],
                "scheme_id": official["scheme_id"],
                "scheme_code": official["scheme_code"],
                "disclosure_month": official["disclosure_month"],
                "security_id": official["security_id"],
                "security_name_raw": official["security_name_raw"],
                "isin_normalized": official["isin_normalized"],
                "quantity_decimal": official["quantity_decimal"],
                "market_value_lakh_decimal": official["market_value_decimal"],
                "percentage_of_nav_decimal": official["percentage_of_nav_decimal"],
                "source": official["source"],
                "source_quality": official["source_quality"],
                "source_precedence_rank": 1,
                "is_preferred": result["preferred_source"] == "amc_workbook",
            })
        third_id = result["mfdata_holding_id"]
        if third_id is not None:
            third = holdings_by_id[int(third_id)]
            historical_evidence.append({
                "observation_key": f"mfdata:{third_id}",
                "source_observation_id": third_id,
                "publisher": third["publisher"],
                "scheme_id": third["scheme_id"],
                "scheme_code": third["scheme_code"],
                "disclosure_month": third["disclosure_month"],
                "security_id": third["security_id"],
                "security_name_raw": third["security_name_raw"],
                "isin_normalized": third["isin_normalized"],
                "quantity_decimal": third["quantity_decimal"],
                "market_value_lakh_decimal": third["market_value_lakh_decimal"],
                "percentage_of_nav_decimal": third["percentage_of_nav_decimal"],
                "source": third["source"],
                "source_quality": third["source_quality"],
                "source_precedence_rank": 2,
                "is_preferred": result["preferred_source"] == "mfdata",
            })
    historical_evidence.sort(
        key=lambda item: (
            item["isin_normalized"] or "", item["source_precedence_rank"],
            item["observation_key"],
        )
    )
    summary_fields = (
        "comparison_status", "official_observation_count",
        "third_party_observation_count", "matched_count", "conflict_count",
        "official_only_count", "third_party_only_count",
        "unmatched_third_party_count",
    )
    return {
        "schema_version": "1.0.0",
        "provenance": {
            key: run[key]
            for key in (
                "mfdata_crosscheck_run_id", "mfdata_snapshot_id", "provider",
                "source", "source_quality", "publisher", "scheme_id",
                "scheme_code", "provider_family_id", "disclosure_month",
                "response_url", "content_sha256", "byte_size", "submitted_path",
                "immutable_path", "parser_version", "first_observed_at_utc",
                "accepted_at_utc", "input_fingerprint", "generated_at_utc",
            )
        },
        "summary": {
            **{key: run[key] for key in summary_fields},
            "official_precedence_enforced": bool(
                run["official_precedence_enforced"]
            ),
            "gap_count": (
                int(run["official_only_count"])
                + int(run["third_party_only_count"])
            ),
        },
        "source_faithful": {
            "mfdata_rows": [
                {
                    "source_position": row["source_position"],
                    "holding_type": row["holding_type"],
                    "holding_type_position": row["holding_type_position"],
                    "source_row": json.loads(row["source_row_json"]),
                }
                for row in holdings
            ]
        },
        "normalized": {
            "mfdata_holdings": [dict(row) for row in holdings],
            "preferred_holding_evidence": historical_evidence,
        },
        "reconciliation": [
            {
                **{
                    key: row[key]
                    for key in row.keys()
                    if key not in {
                        "official_holding_ids_json", "conflict_fields_json"
                    }
                },
                "official_holding_ids": json.loads(
                    row["official_holding_ids_json"]
                ),
                "conflict_fields": json.loads(row["conflict_fields_json"]),
            }
            for row in results
        ],
    }


def write_mfdata_crosscheck_export(
    conn: sqlite3.Connection,
    mfdata_crosscheck_run_id: int,
    output: Path,
) -> Path:
    payload = build_mfdata_crosscheck_export(conn, mfdata_crosscheck_run_id)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output
