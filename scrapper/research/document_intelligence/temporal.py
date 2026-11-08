"""Conservative point-in-time eligibility and two-sided guidance comparisons."""
from __future__ import annotations

from decimal import Decimal
from ...documents.availability import parse_utc


def available_at(record):
    source = record.source
    # Publication alone does not prove this version had been captured. The demo
    # uses D03 completion as a conservative upper bound on first local possession.
    capture = source.get("captured_at")
    if not capture:
        return None
    times = [parse_utc(capture)]
    for key in ("publication_at", "ingested_at"):
        if source.get(key):
            times.append(parse_utc(source[key]))
    return max(times)


def eligible_at(record, decision_at, *, records=()):
    cutoff = parse_utc(decision_at)
    known = available_at(record)
    if known is None or known > cutoff or record.status not in {"VERIFIED", "EVIDENCED_STATEMENT"}:
        return False
    if record.statement_kind in {"ANALYST_QUESTION", "UNKNOWN"}:
        return False
    versions = {r.source["version"]: r for r in records if r.source["document_id"] == record.source["document_id"]}
    for newer in versions.values():
        if available_at(newer) is None or available_at(newer) > cutoff:
            continue
        prior, visited = newer.source.get("supersedes_version"), set()
        while prior and prior not in visited:
            if prior == record.source["version"]:
                return False
            visited.add(prior)
            prior = versions[prior].source.get("supersedes_version") if prior in versions else None
    # Rating evidence is an as-of observation only. No API labels it 'current'.
    return True


def compare_guidance(previous, current, *, decision_at):
    result = {"status": "UNKNOWN", "change": "UNKNOWN", "previous_record_id": previous.record_id,
              "current_record_id": current.record_id, "evidence": [previous.evidence, current.evidence],
              "reason": None, "decision_at": decision_at}
    withdrawal = "explicit_guidance_withdrawal" in current.reasons
    keys = ("metric", "time_horizon") if withdrawal else ("metric", "time_horizon", "currency", "unit")
    def scope(record):
        return {k: v for k, v in record.dimensions.items() if k not in {"approximate"}}
    old_company, new_company = previous.source.get("company_id"), current.source.get("company_id")
    if old_company is None or old_company != new_company:
        result["reason"] = "canonical_company_identity_missing_or_mismatch"
    elif (previous.speaker or {}).get("role") != "management" or (current.speaker or {}).get("role") != "management":
        result["reason"] = "management_attribution_required"
    elif any(getattr(previous, k) != getattr(current, k) for k in keys) or scope(previous) != scope(current) or not current.time_horizon:
        result["reason"] = "incomparable_metric_horizon_unit_or_scope"
    elif not previous.evidence or not current.evidence:
        result["reason"] = "both_statements_require_evidence"
    elif previous.statement_kind not in {"GUIDANCE", "TARGET"} or current.statement_kind not in {"GUIDANCE", "TARGET"}:
        result["reason"] = "not_comparable_guidance"
    elif not eligible_at(previous, decision_at) or not eligible_at(current, decision_at):
        result["reason"] = "statement_not_eligible_at_cutoff"
    else:
        # Public chronology must be known; capture order is not guidance order.
        old_time, new_time = previous.source.get("publication_at"), current.source.get("publication_at")
        if not old_time or not new_time or parse_utc(old_time) >= parse_utc(new_time):
            result["reason"] = "public_statement_chronology_unknown_or_unordered"
        elif "explicit_guidance_withdrawal" in current.reasons:
            result.update(status="VERIFIED", change="WITHDRAWN")
        elif previous.status != "VERIFIED" or current.status != "VERIFIED":
            result["reason"] = "numeric_guidance_not_verified"
        elif previous.normalized_value is None or current.normalized_value is None:
            result["reason"] = "missing_numeric_guidance"
        else:
            def bounds(value):
                return (Decimal(value["low"]), Decimal(value["high"])) if isinstance(value, dict) else (Decimal(value), Decimal(value))
            old, new = bounds(previous.normalized_value), bounds(current.normalized_value)
            if old == new:
                change = "UNCHANGED"
            elif all(n >= o for n, o in zip(new, old)):
                change = "INCREASED"
            elif all(n <= o for n, o in zip(new, old)):
                change = "DECREASED"
            else:
                result.update(status="AMBIGUOUS", reason="range_bounds_move_in_different_directions")
                return result
            result.update(status="VERIFIED", change=change)
    return result
