"""D08 document-pipeline coverage, anomaly, and pilot report."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import DOCUMENT_QUALITY_EXPORTS


SCHEMA_VERSION = "1.0.0"
ARTIFACT_TYPES = ("presentation", "credit_rating", "transcript", "recording")
_EXPLICIT_ABSENCE = {
    "expected_not_yet_published",
    "not_offered",
    "not_applicable",
    "source_not_covered",
}
_BLOCKING_AVAILABILITY = {"inaccessible", "discovery_failed", "unknown"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _rows(
    conn: sqlite3.Connection, query: str, params: Iterable[object] = ()
) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, tuple(params))]


def _company_ids(conn: sqlite3.Connection, symbols: tuple[str, ...]) -> list[int]:
    if not symbols:
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT DISTINCT company_id FROM document_artifacts ORDER BY company_id"
            )
        ]
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if any(not symbol for symbol in normalized):
        raise ValueError("Document report symbols must not be blank")
    placeholders = ",".join("?" for _ in normalized)
    found = {
        str(row["identifier_value"]).upper(): int(row["company_id"])
        for row in conn.execute(
            f"""SELECT company_id, identifier_value FROM security_identifiers
                WHERE identifier_type='NSE' AND is_current=1
                  AND upper(identifier_value) IN ({placeholders})""",
            normalized,
        )
    }
    missing = [symbol for symbol in normalized if symbol not in found]
    if missing:
        raise ValueError(f"Unknown document-report symbol(s): {', '.join(missing)}")
    no_artifacts = [
        symbol
        for symbol in normalized
        if conn.execute(
            "SELECT 1 FROM document_artifacts WHERE company_id=? LIMIT 1",
            (found[symbol],),
        ).fetchone()
        is None
    ]
    if no_artifacts:
        raise ValueError(
            "No document artifacts for symbol(s): " + ", ".join(no_artifacts)
        )
    return sorted({found[symbol] for symbol in normalized})


def _period_key(artifact: dict[str, Any]) -> str:
    if artifact["period_end"]:
        return f"period_end:{artifact['period_end']}"
    if artifact["fiscal_year"] and artifact["fiscal_quarter"]:
        return f"fiscal:FY{artifact['fiscal_year']}-Q{artifact['fiscal_quarter']}"
    if artifact["event_date"]:
        return f"event:{artifact['event_date']}"
    return "unassigned"


def _source_evidence(conn: sqlite3.Connection, artifact_id: int) -> dict[str, Any]:
    sources = _rows(
        conn,
        """SELECT artifact_source_id, discovered_from_source, discovery_page_url,
                  document_url, publisher_claim, exchange_announcement_id,
                  source_position, source_title_raw, is_primary_source,
                  first_observed_at_utc, last_checked_at_utc, availability_status
           FROM artifact_sources WHERE artifact_id=?
           ORDER BY artifact_source_id""",
        (artifact_id,),
    )
    statuses = Counter(row["availability_status"] for row in sources)
    claims = sorted(
        {
            str(row["publisher_claim"]).strip()
            for row in sources
            if row["is_primary_source"] and row["publisher_claim"]
        }
    )
    return {
        "source_count": len(sources),
        "primary_source_count": sum(bool(row["is_primary_source"]) for row in sources),
        "availability_status_counts": dict(sorted(statuses.items())),
        "primary_publisher_claims": claims,
        "sources": sources,
    }


def _content_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    return _rows(
        conn,
        """SELECT content.content_id, content.blob_id, content.media_type,
                  content.content_sha256, content.byte_size,
                  content.extraction_status, content.is_official,
                  content.content_origin, content.created_at_utc,
                  CASE WHEN artifact.preferred_content_id=content.content_id
                       THEN 1 ELSE 0 END AS is_preferred
           FROM artifact_contents AS content
           JOIN document_artifacts AS artifact USING (artifact_id)
           WHERE content.artifact_id=? ORDER BY content.content_id""",
        (artifact_id,),
    )


def _download_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT download_attempt_id, requested_url, final_url, started_at_utc,
                  fetched_at_utc, http_status, declared_media_type,
                  detected_media_type, byte_size, content_sha256, content_id,
                  result_status, error_json
           FROM artifact_download_attempts WHERE artifact_id=?
           ORDER BY download_attempt_id""",
        (artifact_id,),
    )
    for row in rows:
        row["error"] = _json(row.pop("error_json"))
    return rows


def _extraction_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT extraction.extraction_id, extraction.content_id,
                  extraction.parser_name, extraction.parser_version,
                  extraction.ocr_engine, extraction.ocr_engine_version,
                  extraction.extraction_status,
                  extraction.page_or_slide_count, extraction.native_page_count,
                  extraction.ocr_page_count, extraction.unusable_page_count,
                  extraction.started_at_utc, extraction.completed_at_utc,
                  extraction.error_json, content.is_official,
                  content.content_origin
           FROM document_extractions AS extraction
           JOIN artifact_contents AS content USING (content_id)
           WHERE content.artifact_id=? ORDER BY extraction.extraction_id""",
        (artifact_id,),
    )
    for row in rows:
        row["error"] = _json(row.pop("error_json"))
        row["failed_validation_codes"] = [
            str(item[0])
            for item in conn.execute(
                """SELECT rule_code FROM document_extraction_validations
                   WHERE extraction_id=? AND status='failed'
                   ORDER BY rule_code""",
                (row["extraction_id"],),
            )
        ]
    return rows


def _rating_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT normalization.normalization_id, normalization.content_id,
                  normalization.extraction_id, normalization.parser_name,
                  normalization.parser_version, normalization.document_class,
                  normalization.agency_raw, normalization.agency_code,
                  normalization.action_date, normalization.normalization_status,
                  normalization.accepted_action_count,
                  normalization.review_action_count,
                  normalization.conflict_action_count,
                  normalization.error_json, content.is_official,
                  content.content_origin,
                  CASE WHEN preferred.normalization_id=normalization.normalization_id
                       THEN 1 ELSE 0 END AS is_preferred
           FROM credit_rating_normalizations AS normalization
           JOIN artifact_contents AS content USING (content_id)
           LEFT JOIN preferred_credit_rating_normalizations AS preferred
             ON preferred.artifact_id=normalization.artifact_id
           WHERE normalization.artifact_id=?
           ORDER BY normalization.normalization_id""",
        (artifact_id,),
    )
    for row in rows:
        row["error"] = _json(row.pop("error_json"))
        row["failed_validation_codes"] = [
            str(item[0])
            for item in conn.execute(
                """SELECT rule_code FROM credit_rating_normalization_validations
                   WHERE normalization_id=? AND status='failed'
                   ORDER BY rule_code""",
                (row["normalization_id"],),
            )
        ]
    return rows


def _transcript_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT transcript.transcript_id, transcript.content_id,
                  transcript.extraction_id, transcript.origin,
                  transcript.source_media_content_id, transcript.language,
                  transcript.engine, transcript.model_version,
                  transcript.validation_status, transcript.segment_count,
                  transcript.page_start, transcript.page_end,
                  transcript.created_at_utc, transcript.error_json,
                  CASE WHEN preferred.transcript_id=transcript.transcript_id
                       THEN 1 ELSE 0 END AS is_preferred
           FROM transcripts AS transcript
           LEFT JOIN preferred_transcripts AS preferred
             ON preferred.artifact_id=transcript.artifact_id
           WHERE transcript.artifact_id=? ORDER BY transcript.transcript_id""",
        (artifact_id,),
    )
    for row in rows:
        row["error"] = _json(row.pop("error_json"))
        row["failed_validation_codes"] = [
            str(item[0])
            for item in conn.execute(
                """SELECT rule_code FROM transcript_validations
                   WHERE transcript_id=? AND status='failed'
                   ORDER BY rule_code""",
                (row["transcript_id"],),
            )
        ]
    return rows


def _machine_run_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT transcription_run_id, recording_artifact_id, normalization_id,
                  source_media_content_id, source_media_sha256,
                  source_media_duration_ms, source_recording_content_id,
                  source_recording_sha256, engine, engine_version, model_version,
                  comparison_requested, gate_reason, detected_language,
                  language_confidence, duration_coverage, run_status,
                  generated_content_id, transcript_id, error_json
           FROM machine_transcription_runs WHERE transcript_artifact_id=?
           ORDER BY transcription_run_id""",
        (artifact_id,),
    )
    for row in rows:
        row["error"] = _json(row.pop("error_json"))
    return rows


def _recording_evidence(conn: sqlite3.Connection, artifact_id: int) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT normalization_id, source_content_id, normalized_content_id,
                  probe_engine, probe_engine_version, normalizer_engine,
                  normalizer_engine_version, normalization_status,
                  source_duration_ms, normalized_duration_ms,
                  started_at_utc, completed_at_utc, error_json
           FROM recording_normalizations WHERE artifact_id=?
           ORDER BY normalization_id""",
        (artifact_id,),
    )
    for row in rows:
        row["error"] = _json(row.pop("error_json"))
        row["failed_validation_codes"] = [
            str(item[0])
            for item in conn.execute(
                """SELECT rule_code FROM recording_normalization_validations
                   WHERE normalization_id=? AND status='failed'
                   ORDER BY rule_code""",
                (row["normalization_id"],),
            )
        ]
    return rows


def _absence_or_block(
    artifact: dict[str, Any], source: dict[str, Any], downloads: list[dict[str, Any]]
) -> tuple[str, str]:
    statuses = set(source["availability_status_counts"])
    if statuses and statuses <= _EXPLICIT_ABSENCE:
        return "explicitly_missing", "+".join(sorted(statuses))
    if statuses & _BLOCKING_AVAILABILITY:
        return "blocked", "+".join(sorted(statuses & _BLOCKING_AVAILABILITY))
    rejected = [
        row["result_status"]
        for row in downloads
        if row["result_status"] != "validated"
    ]
    if rejected:
        return "blocked", "download_" + "+".join(sorted(set(rejected)))
    if artifact["acquisition_status"] in {"failed", "inaccessible"}:
        return "blocked", f"acquisition_{artifact['acquisition_status']}"
    if not source["source_count"]:
        return "blocked", "no_discovery_provenance"
    if not downloads:
        return "blocked", "download_not_attempted"
    return "blocked", "processing_incomplete"


def _artifact_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    artifact = dict(row)
    artifact_id = int(artifact["artifact_id"])
    source = _source_evidence(conn, artifact_id)
    contents = _content_evidence(conn, artifact_id)
    downloads = _download_evidence(conn, artifact_id)
    extractions = _extraction_evidence(conn, artifact_id)
    rating = (
        _rating_evidence(conn, artifact_id)
        if artifact["artifact_type"] == "credit_rating"
        else []
    )
    transcripts = (
        _transcript_evidence(conn, artifact_id)
        if artifact["artifact_type"] == "transcript"
        else []
    )
    machine_runs = (
        _machine_run_evidence(conn, artifact_id)
        if artifact["artifact_type"] == "transcript"
        else []
    )
    recordings = (
        _recording_evidence(conn, artifact_id)
        if artifact["artifact_type"] == "recording"
        else []
    )

    outcome: str
    reason: str
    if artifact["artifact_type"] == "presentation" and any(
        item["extraction_status"] == "ok"
        and item["is_official"]
        and item["content_origin"] == "publisher_original"
        for item in extractions
    ):
        outcome, reason = "accepted", "usable_extraction"
    elif artifact["artifact_type"] == "credit_rating" and any(
        item["is_preferred"] and item["normalization_status"] == "accepted"
        and item["is_official"]
        and item["content_origin"] == "publisher_original"
        for item in rating
    ):
        outcome, reason = "accepted", "accepted_rating_normalization"
    elif artifact["artifact_type"] == "transcript" and any(
        item["is_preferred"] and item["validation_status"] == "accepted"
        for item in transcripts
    ):
        preferred = next(item for item in transcripts if item["is_preferred"])
        machine_run_accepted = any(
            item["transcript_id"] == preferred["transcript_id"]
            and item["run_status"] == "accepted"
            for item in machine_runs
        )
        if preferred["origin"] == "official":
            outcome, reason = "accepted", "official_transcript"
        elif machine_run_accepted:
            outcome, reason = "accepted", "recording_fallback"
        else:
            outcome, reason = "blocked", "machine_transcription_provenance_missing"
    elif artifact["artifact_type"] == "recording" and any(
        item["normalization_status"] == "accepted" for item in recordings
    ):
        outcome, reason = "accepted", "accepted_recording_normalization"
    elif extractions and all(item["extraction_status"] != "ok" for item in extractions):
        outcome, reason = "blocked", "extraction_failed"
    elif rating:
        outcome, reason = "blocked", "rating_normalization_not_accepted"
    elif transcripts:
        outcome, reason = "blocked", "transcript_not_accepted"
    elif recordings:
        outcome, reason = "blocked", "recording_normalization_not_accepted"
    else:
        outcome, reason = _absence_or_block(artifact, source, downloads)

    official_transcript_accepted = any(
        item["origin"] == "official" and item["validation_status"] == "accepted"
        for item in transcripts
    )
    preferred_machine = any(
        item["origin"] == "machine_generated"
        and item["validation_status"] == "accepted"
        and item["is_preferred"]
        for item in transcripts
    ) and any(
        item["run_status"] == "accepted"
        and any(
            transcript["is_preferred"]
            and transcript["transcript_id"] == item["transcript_id"]
            for transcript in transcripts
        )
        for item in machine_runs
    )
    artifact["period_key"] = _period_key(artifact)
    artifact["outcome"] = outcome
    artifact["outcome_reason"] = reason
    artifact["official_transcript_status"] = (
        "accepted"
        if official_transcript_accepted
        else "missing" if artifact["artifact_type"] == "transcript" else "not_applicable"
    )
    artifact["recording_fallback_used"] = preferred_machine
    artifact["source_evidence"] = source
    artifact["contents"] = contents
    artifact["download_attempts"] = downloads
    artifact["extractions"] = extractions
    artifact["credit_rating_normalizations"] = rating
    artifact["transcripts"] = transcripts
    artifact["machine_transcription_runs"] = machine_runs
    artifact["recording_normalizations"] = recordings
    return artifact


def _period_coverage(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        grouped[(int(artifact["company_id"]), artifact["period_key"])].append(artifact)
    result: list[dict[str, Any]] = []
    for (company_id, period_key), rows in sorted(grouped.items()):
        outcome_counts = Counter(row["outcome"] for row in rows)
        type_counts = Counter(row["artifact_type"] for row in rows)
        result.append(
            {
                "company_id": company_id,
                "period_key": period_key,
                "period_end_values": sorted(
                    {row["period_end"] for row in rows if row["period_end"]}
                ),
                "event_date_values": sorted(
                    {row["event_date"] for row in rows if row["event_date"]}
                ),
                "artifact_count": len(rows),
                "artifact_type_counts": dict(sorted(type_counts.items())),
                "outcome_counts": dict(sorted(outcome_counts.items())),
                "artifact_ids": [int(row["artifact_id"]) for row in rows],
                "coverage_status": "accounted",
            }
        )
    return result


def build_document_quality_report(
    conn: sqlite3.Connection,
    *,
    symbols: tuple[str, ...] = (),
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build D08 coverage for discovered artifacts without fetching or mutating."""

    company_ids = _company_ids(conn, symbols)
    placeholders = ",".join("?" for _ in company_ids)
    artifact_rows = (
        conn.execute(
            f"""SELECT artifact.*, company.current_legal_name AS company_name
                FROM document_artifacts AS artifact
                JOIN companies AS company USING (company_id)
                WHERE artifact.company_id IN ({placeholders})
                ORDER BY artifact.company_id,
                         COALESCE(artifact.period_end, artifact.event_date, ''),
                         CASE artifact.artifact_type
                           WHEN 'presentation' THEN 1 WHEN 'credit_rating' THEN 2
                           WHEN 'transcript' THEN 3 ELSE 4 END,
                         artifact.artifact_id""",
            company_ids,
        ).fetchall()
        if company_ids
        else []
    )
    artifacts = [_artifact_payload(conn, row) for row in artifact_rows]
    companies = []
    for company_id in company_ids:
        company_artifacts = [
            row for row in artifacts if int(row["company_id"]) == company_id
        ]
        company = conn.execute(
            "SELECT current_legal_name FROM companies WHERE company_id=?", (company_id,)
        ).fetchone()
        identifiers = _rows(
            conn,
            """SELECT identifier_type, identifier_value, valid_from, valid_to,
                      is_current FROM security_identifiers WHERE company_id=?
               ORDER BY identifier_type, identifier_value, valid_from""",
            (company_id,),
        )
        companies.append(
            {
                "company_id": company_id,
                "company_name": company["current_legal_name"],
                "identifiers": identifiers,
                "artifact_count": len(company_artifacts),
                "artifact_type_counts": dict(
                    sorted(
                        Counter(
                            row["artifact_type"] for row in company_artifacts
                        ).items()
                    )
                ),
                "outcome_counts": dict(
                    sorted(
                        Counter(row["outcome"] for row in company_artifacts).items()
                    )
                ),
                "period_keys": sorted({row["period_key"] for row in company_artifacts}),
            }
        )

    missing_official = [
        {
            "artifact_id": int(row["artifact_id"]),
            "company_id": int(row["company_id"]),
            "period_key": row["period_key"],
            "outcome": row["outcome"],
            "outcome_reason": row["outcome_reason"],
            "recording_fallback_used": row["recording_fallback_used"],
        }
        for row in artifacts
        if row["artifact_type"] == "transcript"
        and row["official_transcript_status"] == "missing"
    ]
    artifact_ids = [int(row["artifact_id"]) for row in artifacts]
    artifact_placeholders = ",".join("?" for _ in artifact_ids)
    ocr_pages = (
        _rows(
            conn,
            f"""SELECT content.artifact_id, page.extraction_id,
                       page.page_or_slide_number, page.page_kind,
                       page.extraction_confidence, page.native_unusable_reason
                FROM document_pages AS page
                JOIN artifact_contents AS content USING (content_id)
                WHERE page.selected_text_origin='ocr'
                  AND content.artifact_id IN ({artifact_placeholders})
                ORDER BY content.artifact_id, page.extraction_id,
                         page.page_or_slide_number""",
            artifact_ids,
        )
        if artifacts
        else []
    )
    extraction_failures = [
        {
            "artifact_id": int(row["artifact_id"]),
            "extraction_id": int(extraction["extraction_id"]),
            "status": extraction["extraction_status"],
            "failed_validation_codes": extraction["failed_validation_codes"],
            "error": extraction["error"],
        }
        for row in artifacts
        for extraction in row["extractions"]
        if extraction["extraction_status"] != "ok"
    ]
    provenance_conflicts: list[dict[str, Any]] = []
    for row in artifacts:
        claims = row["source_evidence"]["primary_publisher_claims"]
        if len(claims) > 1:
            provenance_conflicts.append(
                {
                    "kind": "primary_publisher_claim_conflict",
                    "artifact_id": int(row["artifact_id"]),
                    "values": claims,
                }
            )
        for normalization in row["credit_rating_normalizations"]:
            if normalization["conflict_action_count"]:
                provenance_conflicts.append(
                    {
                        "kind": "credit_rating_action_conflict",
                        "artifact_id": int(row["artifact_id"]),
                        "normalization_id": int(normalization["normalization_id"]),
                        "conflict_action_count": int(
                            normalization["conflict_action_count"]
                        ),
                    }
                )

    outcomes = Counter(row["outcome"] for row in artifacts)
    type_counts = Counter(row["artifact_type"] for row in artifacts)
    types_present = [kind for kind in ARTIFACT_TYPES if type_counts[kind]]
    missing_types = [kind for kind in ARTIFACT_TYPES if not type_counts[kind]]
    terminal_outcomes = all(
        row["outcome"] in {"accepted", "explicitly_missing", "blocked"}
        for row in artifacts
    )
    pilot_complete = bool(artifacts) and not missing_types and terminal_outcomes
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or _utc_now(),
        "selection": {"symbols": list(symbols), "company_ids": company_ids},
        "coverage_status": (
            "complete" if terminal_outcomes and artifacts else "incomplete"
        ),
        "summary": {
            "company_count": len(companies),
            "period_count": len(_period_coverage(artifacts)),
            "expected_artifact_count": len(artifacts),
            "outcome_counts": dict(sorted(outcomes.items())),
            "artifact_type_counts": dict(sorted(type_counts.items())),
            "missing_official_transcript_count": len(missing_official),
            "recording_fallback_count": sum(
                row["recording_fallback_used"] for row in artifacts
            ),
            "ocr_page_count": len(ocr_pages),
            "extraction_failure_count": len(extraction_failures),
            "provenance_conflict_count": len(provenance_conflicts),
        },
        "pilot": {
            "status": "complete" if pilot_complete else "incomplete",
            "required_artifact_types": list(ARTIFACT_TYPES),
            "covered_artifact_types": types_present,
            "missing_artifact_types": missing_types,
            "all_expected_artifacts_accounted": terminal_outcomes and bool(artifacts),
        },
        "companies": companies,
        "period_coverage": _period_coverage(artifacts),
        "anomalies": {
            "missing_official_transcripts": missing_official,
            "recording_fallbacks": [
                {
                    "artifact_id": int(row["artifact_id"]),
                    "company_id": int(row["company_id"]),
                    "period_key": row["period_key"],
                }
                for row in artifacts
                if row["recording_fallback_used"]
            ],
            "ocr_pages": ocr_pages,
            "extraction_failures": extraction_failures,
            "provenance_conflicts": provenance_conflicts,
        },
        "artifacts": artifacts,
    }
    canonical = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def write_document_quality_report(
    conn: sqlite3.Connection,
    destination: Path | None = None,
    *,
    symbols: tuple[str, ...] = (),
    generated_at_utc: str | None = None,
) -> Path:
    path = destination or (DOCUMENT_QUALITY_EXPORTS / "document-quality-report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            build_document_quality_report(
                conn, symbols=symbols, generated_at_utc=generated_at_utc
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
