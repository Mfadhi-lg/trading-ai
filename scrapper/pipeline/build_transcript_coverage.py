"""Build transcript availability and exact recording-fallback ledgers."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..config import DB_PATH, assert_configured_writable_targets
from ..db.database import connect, init_db


TRANSCRIPT_STATES = (
    "OFFICIAL_TRANSCRIPT_AVAILABLE",
    "OFFICIAL_TRANSCRIPT_UNUSABLE",
    "OFFICIAL_TRANSCRIPT_NOT_OFFERED",
    "OFFICIAL_TRANSCRIPT_SOURCE_MISSING",
    "OFFICIAL_TRANSCRIPT_INACCESSIBLE",
    "TECHNICAL_FAILURE",
    "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE",
    "UNKNOWN_REQUIRES_RECONCILIATION",
)

RECORDING_STATES = (
    "RECORDING_AVAILABLE",
    "RECORDING_NOT_AVAILABLE",
    "PERIOD_AMBIGUOUS",
)

# Recording fallback is a positive allowlist of *terminal* transcript outcomes.
# A state is listed here only when the official transcript has been proven
# unusable or unavailable. Anything else - including unresolved technical
# failure, pending reconciliation, and every future state - fails closed.
RECORDING_FALLBACK_ELIGIBLE_STATES = frozenset(
    {
        "OFFICIAL_TRANSCRIPT_UNUSABLE",
        "OFFICIAL_TRANSCRIPT_NOT_OFFERED",
        "OFFICIAL_TRANSCRIPT_SOURCE_MISSING",
        "OFFICIAL_TRANSCRIPT_INACCESSIBLE",
    }
)

# Transcript states that already satisfy a period; a recording adds nothing.
SATISFYING_TRANSCRIPT_STATES = frozenset(
    {
        "OFFICIAL_TRANSCRIPT_AVAILABLE",
        "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE",
    }
)

# Documented, non-authoritative: the allowlist above is the only gate. This
# names the unresolved states that used to leak into fallback through the old
# "not in the good states" rule so reports can call them out explicitly.
RECORDING_FALLBACK_EXCLUDED_UNRESOLVED_STATES = frozenset(
    {
        "TECHNICAL_FAILURE",
        "UNKNOWN_REQUIRES_RECONCILIATION",
    }
)


def recording_fallback_decision(
    *,
    transcript_state: str,
    recording_present: bool,
    identity_resolved: bool,
    in_active_corpus: bool,
    period_already_satisfied: bool,
) -> tuple[bool, str]:
    """Decide recording fallback from an explicit terminal-state allowlist.

    Every condition is a positive requirement, so an unknown or newly added
    transcript state can never acquire a recording by default.
    """

    if transcript_state not in RECORDING_FALLBACK_ELIGIBLE_STATES:
        return False, "transcript_state_not_in_fallback_allowlist"
    if not recording_present:
        return False, "no_recording_for_period"
    if not identity_resolved:
        return False, "company_period_identity_unresolved"
    if not in_active_corpus:
        return False, "period_not_in_active_acquisition_corpus"
    if period_already_satisfied:
        return False, "period_already_satisfied_by_usable_transcript"
    return True, "terminal_transcript_state_with_identified_recording"


def _labels(conn: Any, artifact_types: tuple[str, ...]) -> dict[int, tuple[str, ...]]:
    placeholders = ",".join("?" for _ in artifact_types)
    values: dict[int, set[str]] = defaultdict(set)
    for row in conn.execute(
        f"""SELECT artifact.artifact_id,
                   json_extract(source.classification_evidence_json,
                                '$.period_label_raw') AS label
              FROM document_artifacts artifact
              JOIN artifact_sources source USING(artifact_id)
             WHERE artifact.artifact_type IN ({placeholders})
               AND json_extract(source.classification_evidence_json,
                                '$.period_label_raw') IS NOT NULL""",
        artifact_types,
    ):
        values[int(row["artifact_id"])].add(str(row["label"]))
    return {key: tuple(sorted(value)) for key, value in values.items()}


def _identity_match(left: Any, right: Any) -> str | None:
    overlapping = 0
    for field in ("event_date", "period_end", "fiscal_year", "fiscal_quarter"):
        if left[field] is not None and right[field] is not None:
            overlapping += 1
            if left[field] != right[field]:
                return None
    if left["event_date"] is not None and left["event_date"] == right["event_date"]:
        return "EXACT_EVENT_DATE"
    if left["period_end"] is not None and left["period_end"] == right["period_end"]:
        return "EXACT_PERIOD_END"
    if (
        left["fiscal_year"] is not None
        and left["fiscal_quarter"] is not None
        and left["fiscal_year"] == right["fiscal_year"]
        and left["fiscal_quarter"] == right["fiscal_quarter"]
    ):
        return "EXACT_FISCAL_YEAR_QUARTER"
    return None if overlapping == 0 else None


def _transcript_state(
    artifact: Any,
    *,
    accepted_by_artifact: dict[int, list[Any]],
    contents_by_artifact: dict[int, list[Any]],
    sources_by_artifact: dict[int, list[Any]],
    queues_by_artifact: dict[int, list[Any]],
) -> tuple[str, dict[str, Any]]:
    artifact_id = int(artifact["artifact_id"])
    accepted = accepted_by_artifact.get(artifact_id, [])
    if any(row["origin"] == "official" and row["validation_status"] == "accepted" for row in accepted):
        return "OFFICIAL_TRANSCRIPT_AVAILABLE", {"basis": "accepted_official_transcript"}
    if any(row["origin"] == "machine_generated" and row["validation_status"] == "accepted" for row in accepted):
        return "MACHINE_TRANSCRIPT_ALREADY_AVAILABLE", {"basis": "accepted_machine_transcript"}
    contents = contents_by_artifact.get(artifact_id, [])
    official_statuses = [str(row["extraction_status"]) for row in contents if row["is_official"]]
    if "ok" in official_statuses:
        return "OFFICIAL_TRANSCRIPT_AVAILABLE", {"basis": "official_d03_ok"}
    # A downloaded transcript whose extraction has not finished is unresolved
    # technical work, not evidence that the transcript is unusable.  Treating
    # not_started/pending as UNUSABLE would prematurely queue a heavy recording
    # fallback while D03 can still succeed.
    if any(status in {"not_started", "pending"} for status in official_statuses):
        return "TECHNICAL_FAILURE", {
            "basis": "official_d03_not_terminal",
            "extraction_statuses": sorted(set(official_statuses)),
        }
    if official_statuses:
        return "OFFICIAL_TRANSCRIPT_UNUSABLE", {
            "basis": "official_content_terminal_not_usable",
            "extraction_statuses": sorted(set(official_statuses)),
        }
    # Publisher bytes were acquired but are not yet marked official: retrieval
    # provenance and/or D03 have not finished. The source is demonstrably neither
    # missing nor withheld, so this is unresolved technical work - never UNKNOWN,
    # and never a licence for recording fallback.
    publisher_statuses = [
        str(row["extraction_status"])
        for row in contents
        if str(row["content_origin"]) == "publisher_original"
    ]
    if publisher_statuses:
        pending = [status for status in publisher_statuses if status in {"not_started", "pending"}]
        return "TECHNICAL_FAILURE", {
            "basis": "acquired_content_extraction_pending"
            if pending
            else "acquired_content_official_provenance_unresolved",
            "extraction_statuses": sorted(set(publisher_statuses)),
        }
    sources = sources_by_artifact.get(artifact_id, [])
    if sources and all(row["availability_status"] == "not_offered" for row in sources):
        return "OFFICIAL_TRANSCRIPT_NOT_OFFERED", {"basis": "explicit_source_status"}
    queues = queues_by_artifact.get(artifact_id, [])
    if any(row["status"] in {"PENDING", "LEASED", "RETRYABLE_FAILURE"} for row in queues):
        return "TECHNICAL_FAILURE", {
            "basis": "durable_queue_not_successful",
            "queue_states": sorted({str(row["status"]) for row in queues}),
        }
    terminal_results = {str(row["last_result_status"] or "") for row in queues}
    terminal_errors = " ".join(str(row["last_error"] or "") for row in queues)
    if any(code in terminal_results or code in terminal_errors for code in ("http_401", "http_403")):
        return "OFFICIAL_TRANSCRIPT_INACCESSIBLE", {"basis": "terminal_http_access_denied"}
    if any(code in terminal_results or code in terminal_errors for code in ("http_404", "http_410")):
        return "OFFICIAL_TRANSCRIPT_SOURCE_MISSING", {"basis": "terminal_dead_url"}
    if queues and all(row["status"] in {"TERMINAL_FAILURE", "SKIPPED_POLICY"} for row in queues):
        return "OFFICIAL_TRANSCRIPT_SOURCE_MISSING", {
            "basis": "all_sources_terminal",
            "result_states": sorted(terminal_results),
        }
    if not sources:
        return "OFFICIAL_TRANSCRIPT_SOURCE_MISSING", {"basis": "no_artifact_source"}
    if artifact["acquisition_status"] == "inaccessible":
        return "OFFICIAL_TRANSCRIPT_INACCESSIBLE", {"basis": "artifact_acquisition_status"}
    if not queues:
        # In corpus with a live source but no download job was ever registered:
        # acquisition work is outstanding, not a proven transcript outcome.
        return "UNKNOWN_REQUIRES_RECONCILIATION", {
            "basis": "no_download_job_registered",
            "source_availability_states": sorted(
                {str(row["availability_status"]) for row in sources}
            ),
        }
    return "UNKNOWN_REQUIRES_RECONCILIATION", {
        "basis": "insufficient_terminal_evidence",
        "queue_states": sorted({str(row["status"]) for row in queues}),
        "queue_result_states": sorted(terminal_results),
    }


def build_coverage(conn: Any, *, full_scope_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    transcripts = conn.execute(
        "SELECT * FROM document_artifacts WHERE artifact_type='transcript' ORDER BY artifact_id"
    ).fetchall()
    recordings = conn.execute(
        "SELECT * FROM document_artifacts WHERE artifact_type='recording' ORDER BY artifact_id"
    ).fetchall()
    accepted_by_artifact: dict[int, list[Any]] = defaultdict(list)
    for row in conn.execute(
        "SELECT artifact_id,origin,validation_status FROM transcripts ORDER BY transcript_id"
    ):
        accepted_by_artifact[int(row["artifact_id"])].append(row)
    contents_by_artifact: dict[int, list[Any]] = defaultdict(list)
    content_artifact_ids: set[int] = set()
    for row in conn.execute(
        """SELECT artifact_id,extraction_status,is_official,content_origin
             FROM artifact_contents ORDER BY content_id"""
    ):
        artifact_id = int(row["artifact_id"])
        contents_by_artifact[artifact_id].append(row)
        content_artifact_ids.add(artifact_id)
    sources_by_artifact: dict[int, list[Any]] = defaultdict(list)
    for row in conn.execute(
        "SELECT artifact_id,availability_status,document_url FROM artifact_sources ORDER BY artifact_source_id"
    ):
        sources_by_artifact[int(row["artifact_id"])].append(row)
    queues_by_artifact: dict[int, list[Any]] = defaultdict(list)
    for row in conn.execute(
        """SELECT artifact_id,status,last_result_status,last_error
             FROM artifact_download_queue WHERE scope_id=? ORDER BY download_job_id""",
        (full_scope_id,),
    ):
        queues_by_artifact[int(row["artifact_id"])].append(row)
    labels = _labels(conn, ("transcript", "recording", "presentation"))
    scope_artifact_ids = {
        int(row[0])
        for row in conn.execute(
            "SELECT artifact_id FROM artifact_download_scope_artifacts WHERE scope_id=?",
            (full_scope_id,),
        )
    }
    transcript_groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    recording_groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    presentation_groups: set[tuple[int, str]] = set()
    for artifact in (*transcripts, *recordings):
        artifact_labels = labels.get(int(artifact["artifact_id"]), ())
        if len(artifact_labels) != 1:
            continue
        key = (int(artifact["company_id"]), artifact_labels[0])
        target = transcript_groups if artifact["artifact_type"] == "transcript" else recording_groups
        target[key].append(int(artifact["artifact_id"]))
    for row in conn.execute(
        "SELECT artifact_id,company_id FROM document_artifacts WHERE artifact_type='presentation'"
    ):
        artifact_labels = labels.get(int(row["artifact_id"]), ())
        if len(artifact_labels) == 1:
            presentation_groups.add((int(row["company_id"]), artifact_labels[0]))

    recordings_by_company: dict[int, list[Any]] = defaultdict(list)
    for recording in recordings:
        recordings_by_company[int(recording["company_id"])].append(recording)
    ledger: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    transcript_counts: Counter[str] = Counter({state: 0 for state in TRANSCRIPT_STATES})
    recording_counts: Counter[str] = Counter({state: 0 for state in RECORDING_STATES})
    unknown_reason_counts: Counter[str] = Counter()
    excluded_by_state: Counter[str] = Counter()
    excluded_by_reason: Counter[str] = Counter()
    matched_recordings: set[int] = set()

    # First pass: every transcript state, so a sibling transcript that already
    # satisfies a period can suppress recording fallback for the same period.
    states_by_transcript: dict[int, tuple[str, dict[str, Any]]] = {}
    satisfied_keys: set[tuple[int, str]] = set()
    for transcript in transcripts:
        artifact_id = int(transcript["artifact_id"])
        state, evidence = _transcript_state(
            transcript,
            accepted_by_artifact=accepted_by_artifact,
            contents_by_artifact=contents_by_artifact,
            sources_by_artifact=sources_by_artifact,
            queues_by_artifact=queues_by_artifact,
        )
        states_by_transcript[artifact_id] = (state, evidence)
        transcript_counts[state] += 1
        if state == "UNKNOWN_REQUIRES_RECONCILIATION":
            unknown_reason_counts[str(evidence.get("basis", "unspecified"))] += 1
        artifact_labels = labels.get(artifact_id, ())
        if state in SATISFYING_TRANSCRIPT_STATES and len(artifact_labels) == 1:
            satisfied_keys.add((int(transcript["company_id"]), artifact_labels[0]))

    for transcript in transcripts:
        artifact_id = int(transcript["artifact_id"])
        state, evidence = states_by_transcript[artifact_id]
        matches: dict[int, str] = {}
        for recording in recordings_by_company[int(transcript["company_id"])]:
            basis = _identity_match(transcript, recording)
            if basis:
                matches[int(recording["artifact_id"])] = basis
        artifact_labels = labels.get(artifact_id, ())
        if len(artifact_labels) == 1:
            key = (int(transcript["company_id"]), artifact_labels[0])
            if len(transcript_groups[key]) == 1 and len(recording_groups[key]) == 1:
                matches[recording_groups[key][0]] = "EXACT_SOURCE_PERIOD_LABEL"
        if len(matches) == 1:
            recording_id, match_basis = next(iter(matches.items()))
            matched_recordings.add(recording_id)
            has_content = recording_id in content_artifact_ids
            recording_state = "RECORDING_AVAILABLE"
            recording_counts[recording_state] += 1
            period_key = (
                (int(transcript["company_id"]), artifact_labels[0])
                if len(artifact_labels) == 1
                else None
            )
            needs_fallback, fallback_reason = recording_fallback_decision(
                transcript_state=state,
                recording_present=True,
                # Conflicting source period labels on one transcript are not a
                # resolved identity, even when a recording happens to match.
                identity_resolved=len(artifact_labels) <= 1,
                in_active_corpus=artifact_id in scope_artifact_ids,
                period_already_satisfied=period_key is not None
                and state not in SATISFYING_TRANSCRIPT_STATES
                and period_key in satisfied_keys,
            )
            if needs_fallback:
                fallback.append(
                    {
                        "company_id": int(transcript["company_id"]),
                        "transcript_artifact_id": artifact_id,
                        "recording_artifact_id": recording_id,
                        "source_period_label_raw": artifact_labels[0] if len(artifact_labels) == 1 else None,
                        "match_basis": match_basis,
                        "transcript_state": state,
                        "eligibility_basis": fallback_reason,
                        "recording_already_acquired": has_content,
                    }
                )
                fallback_state = "FALLBACK_REQUIRED"
            else:
                excluded_by_state[state] += 1
                excluded_by_reason[fallback_reason] += 1
                fallback_state = "FALLBACK_NOT_REQUIRED"
        elif len(matches) > 1:
            recording_id = None
            recording_state = "PERIOD_AMBIGUOUS"
            recording_counts[recording_state] += 1
            match_basis = None
            fallback_state = "PERIOD_AMBIGUOUS"
            fallback_reason = "company_period_identity_unresolved"
        else:
            recording_id = None
            recording_state = "RECORDING_NOT_AVAILABLE"
            recording_counts[recording_state] += 1
            match_basis = None
            fallback_state = "RECORDING_UNAVAILABLE"
            fallback_reason = "no_recording_for_period"
        ledger.append(
            {
                "company_id": int(transcript["company_id"]),
                "transcript_artifact_id": artifact_id,
                "source_period_labels": list(artifact_labels),
                "transcript_state": state,
                "transcript_evidence": evidence,
                "recording_artifact_id": recording_id,
                "recording_state": recording_state,
                "recording_match_basis": match_basis,
                "fallback_state": fallback_state,
                "fallback_reason": fallback_reason,
            }
        )

    recording_only_active = []
    recording_only_counts: Counter[str] = Counter()
    for recording in recordings:
        recording_id = int(recording["artifact_id"])
        if recording_id in matched_recordings:
            continue
        artifact_labels = labels.get(recording_id, ())
        active_via_presentation = False
        key: tuple[int, str] | None = None
        if len(artifact_labels) == 1:
            key = (int(recording["company_id"]), artifact_labels[0])
            active_via_presentation = key in presentation_groups
        # Every unmatched recording is resolved to an explicit, evidence-backed
        # residual. UNKNOWN stays reachable only as a fail-closed default.
        if key is None:
            state = "RECORDING_PERIOD_UNIDENTIFIED"
            reason = (
                "no_source_period_label"
                if not artifact_labels
                else "conflicting_source_period_labels"
            )
        elif key in satisfied_keys:
            state = "RECORDING_PERIOD_SATISFIED_BY_TRANSCRIPT"
            reason = "usable_transcript_exists_for_period"
        elif transcript_groups.get(key):
            state = "RECORDING_TRANSCRIPT_MATCH_AMBIGUOUS"
            reason = "transcript_artifact_for_period_not_uniquely_matchable"
        else:
            state = "RECORDING_ONLY_NO_TRANSCRIPT_ARTIFACT"
            reason = "no_transcript_artifact_discovered_for_period"
        recording_only_counts[state] += 1
        recording_only_active.append(
            {
                "company_id": int(recording["company_id"]),
                "recording_artifact_id": recording_id,
                "source_period_labels": list(artifact_labels),
                "active_via_presentation": active_via_presentation,
                "state": state,
                "reason": reason,
            }
        )

    scope = {
        "schema_version": "1.0.0",
        "scope_id": "document-acquisition-recording-fallback-v1",
        "scope_kind": "custom",
        "description": "Recording fallback only for terminal-state in-scope transcripts",
        "artifact_types": ["recording"],
        "recording_fallback_only": True,
        "eligible_transcript_states": sorted(RECORDING_FALLBACK_ELIGIBLE_STATES),
        "company_ids": sorted({int(row["company_id"]) for row in fallback}),
        "companies": [
            {"company_id": company_id}
            for company_id in sorted({int(row["company_id"]) for row in fallback})
        ],
        "artifact_ids": sorted({int(row["recording_artifact_id"]) for row in fallback}),
        "fallback_relationships": fallback,
    }
    queued_by_state = Counter(row["transcript_state"] for row in fallback)
    report = {
        "schema_version": "2.0.0",
        "full_scope_id": full_scope_id,
        "transcript_state_counts": dict(sorted(transcript_counts.items())),
        "recording_state_counts": dict(sorted(recording_counts.items())),
        "recordings_discovered": len(recordings),
        "fallback_required": len(fallback),
        "fallback_not_required": sum(row["fallback_state"] == "FALLBACK_NOT_REQUIRED" for row in ledger),
        "period_ambiguous": sum(row["fallback_state"] == "PERIOD_AMBIGUOUS" for row in ledger),
        "recording_unavailable": sum(row["fallback_state"] == "RECORDING_UNAVAILABLE" for row in ledger),
        "recording_only_requires_reconciliation": recording_only_counts.get(
            "UNKNOWN_REQUIRES_RECONCILIATION", 0
        ),
        "recording_only_total": len(recording_only_active),
        "recording_only_state_counts": dict(sorted(recording_only_counts.items())),
        "transcript_unknown_reason_counts": dict(sorted(unknown_reason_counts.items())),
        "fallback_eligible_states": sorted(RECORDING_FALLBACK_ELIGIBLE_STATES),
        "fallback_queued_by_transcript_state": dict(sorted(queued_by_state.items())),
        "fallback_excluded_by_transcript_state": dict(sorted(excluded_by_state.items())),
        "fallback_excluded_by_reason": dict(sorted(excluded_by_reason.items())),
        "fallback_queued_from_unresolved_states": sum(
            count
            for state, count in queued_by_state.items()
            if state not in RECORDING_FALLBACK_ELIGIBLE_STATES
        ),
        "source_period_rule": "exact source label only; no fiscal quarter or period-end inference",
        "ledger": ledger,
        "recording_only": recording_only_active,
    }
    return report, scope


def write_reports(report: dict[str, Any], scope: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "12_TRANSCRIPT_COVERAGE.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "13_RECORDING_FALLBACK_SCOPE.json").write_text(
        json.dumps(scope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    counts = report["transcript_state_counts"]
    lines = [
        "# Transcript Coverage",
        "",
        *[f"- {key}: {value}" for key, value in counts.items()],
        "",
        f"Recordings discovered: {report['recordings_discovered']}",
        f"Recording fallback required: {report['fallback_required']}",
        f"Fallback not required: {report['fallback_not_required']}",
        f"Period ambiguous: {report['period_ambiguous']}",
        f"Recording unavailable: {report['recording_unavailable']}",
        f"Recording-only periods requiring reconciliation: {report['recording_only_requires_reconciliation']}",
        "",
        "## Recording-only residual states",
        "",
        *[f"- {key}: {value}" for key, value in report["recording_only_state_counts"].items()],
        "",
        "## Transcript UNKNOWN reasons",
        "",
        *(
            [f"- {key}: {value}" for key, value in report["transcript_unknown_reason_counts"].items()]
            or ["- none"]
        ),
        "",
        "## Recording fallback eligibility",
        "",
        f"Eligible transcript states: {', '.join(report['fallback_eligible_states'])}",
        f"Fallback queued from unresolved states: {report['fallback_queued_from_unresolved_states']}",
        "",
        *[
            f"- excluded {key}: {value}"
            for key, value in report["fallback_excluded_by_transcript_state"].items()
        ],
        "",
        "Exact source period labels are retained as evidence only; they are never converted into a fiscal quarter or period end.",
    ]
    (output_dir / "12_TRANSCRIPT_COVERAGE.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    scope_lines = [
        "# Recording Fallback Scope",
        "",
        f"Scope: `{scope['scope_id']}`",
        f"Companies: {len(scope['company_ids'])}",
        f"Recordings: {len(scope['artifact_ids'])}",
        "Recording fallback policy enforced: yes",
        "",
        "Recordings with ambiguous period identity or a usable official transcript are excluded.",
    ]
    (output_dir / "13_RECORDING_FALLBACK_REPORT.md").write_text(
        "\n".join(scope_lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--full-scope-id", default="document-acquisition-full-v1")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert_configured_writable_targets(args.output_dir, db_path=args.db)
    conn = connect(args.db)
    try:
        init_db(conn)
        report, scope = build_coverage(conn, full_scope_id=args.full_scope_id)
    finally:
        conn.close()
    write_reports(report, scope, args.output_dir)
    print(json.dumps({
        "transcript_state_counts": report["transcript_state_counts"],
        "fallback_required": report["fallback_required"],
        "fallback_queued_by_transcript_state": report["fallback_queued_by_transcript_state"],
        "fallback_queued_from_unresolved_states": report["fallback_queued_from_unresolved_states"],
        "recording_only_state_counts": report["recording_only_state_counts"],
        "recording_only_requires_reconciliation": report["recording_only_requires_reconciliation"],
        "transcript_unknown_reason_counts": report["transcript_unknown_reason_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
