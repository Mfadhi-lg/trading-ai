"""Exact, auditable M05 comparisons across consecutive accepted MF months."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


CHANGE_ENGINE_VERSION = "mf_consecutive_quantity_v2"
MONTH_RE = re.compile(r"^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class ChangeGenerationSummary:
    publisher: str
    previous_month: str
    current_month: str
    generated_change_sets: int
    skipped_change_sets: int
    change_count: int
    change_class_counts: dict[str, int]

    def to_dict(self) -> dict:
        return {
            **self.__dict__,
            "change_class_counts": dict(self.change_class_counts),
        }


@dataclass(frozen=True)
class ChangeRefreshSummary:
    publisher: str
    changed_month: str
    comparisons: tuple[ChangeGenerationSummary, ...]

    def to_dict(self) -> dict:
        return {
            "publisher": self.publisher,
            "changed_month": self.changed_month,
            "comparisons": [item.to_dict() for item in self.comparisons],
        }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _month_parts(month: str) -> tuple[int, int]:
    if not MONTH_RE.fullmatch(month):
        raise ValueError("Disclosure month must use YYYY-MM")
    year, number = (int(part) for part in month.split("-"))
    return year, number


def adjacent_month(month: str, offset: int) -> str:
    year, number = _month_parts(month)
    absolute = year * 12 + number - 1 + offset
    if absolute < 0:
        raise ValueError("Disclosure month offset is outside the supported range")
    return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}"


def _decimal(value: str | None, *, field: str) -> Decimal:
    if value is None:
        raise ValueError(f"{field} is missing")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not an exact decimal: {value}") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite: {value}")
    return result


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _pointer_evidence(
    conn: sqlite3.Connection,
    *,
    publisher: str,
    scheme_id: int,
    month: str,
) -> tuple[dict, int | None, bool]:
    pointer = conn.execute(
        """SELECT av.import_file_id, av.accepted_at_utc AS pointer_accepted_at_utc,
                  f.import_state, f.detected_disclosure_month,
                  event.workbook_acceptance_event_id,
                  event.validation_status
           FROM mf_accepted_versions AS av
           JOIN mf_import_files AS f ON f.import_file_id=av.import_file_id
           LEFT JOIN mf_workbook_acceptance_events AS event
             ON event.import_file_id=av.import_file_id
           WHERE av.publisher=? AND av.scheme_id=? AND av.disclosure_month=?""",
        (publisher, scheme_id, month),
    ).fetchone()
    candidates = [
        {
            "import_file_id": int(row["import_file_id"]),
            "import_state": str(row["import_state"]),
        }
        for row in conn.execute(
            """SELECT DISTINCT f.import_file_id, f.import_state
               FROM mf_import_files AS f
               LEFT JOIN mf_holdings AS h ON h.import_file_id=f.import_file_id
               WHERE f.publisher=?
                 AND COALESCE(f.detected_disclosure_month,
                              f.requested_disclosure_month)=?
                 AND (h.scheme_id=? OR h.scheme_id IS NULL)
               ORDER BY f.import_file_id""",
            (publisher, month, scheme_id),
        )
    ]
    if pointer is None:
        return (
            {
                "month": month,
                "accepted_pointer_present": False,
                "candidate_imports": candidates,
            },
            None,
            False,
        )
    import_id = int(pointer["import_file_id"])
    complete = (
        str(pointer["import_state"]) == "accepted"
        and str(pointer["detected_disclosure_month"]) == month
        and pointer["workbook_acceptance_event_id"] is not None
        and str(pointer["validation_status"]) in {"passed", "passed_with_warnings"}
    )
    return (
        {
            "month": month,
            "accepted_pointer_present": True,
            "import_file_id": import_id,
            "import_state": str(pointer["import_state"]),
            "detected_disclosure_month": pointer["detected_disclosure_month"],
            "pointer_accepted_at_utc": pointer["pointer_accepted_at_utc"],
            "workbook_acceptance_event_id": pointer["workbook_acceptance_event_id"],
            "validation_status": pointer["validation_status"],
            "complete_accepted_disclosure": complete,
            "candidate_imports": candidates,
        },
        import_id,
        complete,
    )


def _eligible_holdings(
    conn: sqlite3.Connection,
    *,
    publisher: str,
    scheme_id: int,
    month: str,
    import_file_id: int,
) -> tuple[dict[int, dict], list[dict], list[dict]]:
    rows = conn.execute(
        """SELECT h.*,
                  COALESCE(
                    h.isin_normalized,
                    (SELECT identifier_value
                     FROM mf_security_identifiers AS identifier
                     WHERE identifier.security_id=h.security_id
                       AND identifier.identifier_type='isin'
                       AND identifier.structurally_valid=1
                     ORDER BY identifier.security_identifier_id LIMIT 1)
                  ) AS canonical_isin
           FROM mf_stock_feature_eligible_holdings AS h
           WHERE h.publisher=? AND h.scheme_id=? AND h.disclosure_month=?
             AND h.import_file_id=?
           ORDER BY h.security_id, h.holding_id""",
        (publisher, scheme_id, month, import_file_id),
    ).fetchall()
    by_security: dict[int, dict] = {}
    issues: list[dict] = []
    fingerprints: list[dict] = []
    for row in rows:
        security_id = int(row["security_id"])
        try:
            quantity = _decimal(row["quantity_decimal"], field="quantity_decimal")
            weight = _decimal(
                row["percentage_of_nav_decimal"], field="percentage_of_nav_decimal"
            )
            if row["canonical_isin"] is None:
                raise ValueError("canonical ISIN is missing")
        except ValueError as exc:
            issues.append(
                {
                    "code": "non_comparable_holding",
                    "holding_id": int(row["holding_id"]),
                    "security_id": security_id,
                    "message": str(exc),
                }
            )
            continue
        if security_id in by_security:
            aggregate = by_security[security_id]
            aggregate["quantity_decimal"] = _decimal_text(
                _decimal(aggregate["quantity_decimal"], field="quantity_decimal")
                + quantity
            )
            aggregate["percentage_of_nav_decimal"] = _decimal_text(
                _decimal(
                    aggregate["percentage_of_nav_decimal"],
                    field="percentage_of_nav_decimal",
                )
                + weight
            )
            aggregate["component_holding_ids"].append(int(row["holding_id"]))
        else:
            aggregate = dict(row)
            aggregate["component_holding_ids"] = [int(row["holding_id"])]
            by_security[security_id] = aggregate
        fingerprints.append(
            {
                "holding_id": int(row["holding_id"]),
                "security_id": security_id,
                "isin": str(row["canonical_isin"]),
                "quantity": str(row["quantity_decimal"]),
                "weight": str(row["percentage_of_nav_decimal"]),
            }
        )
    return by_security, issues, fingerprints


def _candidate_scheme_ids(
    conn: sqlite3.Connection, publisher: str, previous_month: str, current_month: str
) -> tuple[int, ...]:
    return tuple(
        int(row["scheme_id"])
        for row in conn.execute(
            """SELECT scheme_id FROM mf_accepted_versions
               WHERE publisher=? AND disclosure_month IN (?, ?)
               UNION
               SELECT h.scheme_id
               FROM mf_holdings AS h
               JOIN mf_import_files AS f ON f.import_file_id=h.import_file_id
               WHERE f.publisher=?
                 AND COALESCE(f.detected_disclosure_month,
                              f.requested_disclosure_month) IN (?, ?)
                 AND h.scheme_id IS NOT NULL
               ORDER BY scheme_id""",
            (
                publisher, previous_month, current_month,
                publisher, previous_month, current_month,
            ),
        )
    )


def _change_class(previous: Decimal, current: Decimal) -> str:
    if previous == 0 and current > 0:
        return "new_entry"
    if previous > 0 and current == 0:
        return "complete_exit"
    delta = current - previous
    if delta > 0:
        return "quantity_increase"
    if delta < 0:
        return "quantity_decrease"
    return "unchanged"


def _persist_change_validations(
    conn: sqlite3.Connection,
    *,
    change_set_id: int,
    previous_complete: bool,
    current_complete: bool,
    issues: list[dict],
    expected_change_count: int | None,
    actual_change_count: int,
) -> None:
    disclosures_complete = previous_complete and current_complete
    rules = (
        (
            "m05_consecutive_calendar_months", "passed",
            {"months_consecutive": True},
        ),
        (
            "m05_complete_accepted_disclosures",
            "passed" if disclosures_complete else "failed",
            {
                "previous_complete": previous_complete,
                "current_complete": current_complete,
            },
        ),
        (
            "m05_comparable_holding_rows",
            (
                "not_evaluated" if not disclosures_complete
                else "passed" if not issues else "failed"
            ),
            {"issue_count": len(issues), "issues": issues},
        ),
        (
            "m05_exact_change_rows_complete",
            (
                "not_evaluated" if expected_change_count is None
                else "passed" if expected_change_count == actual_change_count
                else "failed"
            ),
            {
                "expected_change_count": expected_change_count,
                "actual_change_count": actual_change_count,
            },
        ),
    )
    conn.executemany(
        """INSERT OR IGNORE INTO mf_scheme_month_change_validations
           (change_set_id, rule_code, severity, validation_status, details_json)
           VALUES (?, ?, 'error', ?, ?)""",
        (
            (change_set_id, rule_code, status, _json(details))
            for rule_code, status, details in rules
        ),
    )


def generate_scheme_month_changes(
    conn: sqlite3.Connection,
    *,
    publisher: str,
    current_month: str,
    generated_at_utc: str,
) -> ChangeGenerationSummary:
    """Generate one prior-month comparison per known scheme, or audit the skip."""
    current_year, current_number = _month_parts(current_month)
    current_month = f"{current_year:04d}-{current_number:02d}"
    previous_month = adjacent_month(current_month, -1)
    generated_sets = 0
    skipped_sets = 0
    change_count = 0
    class_counts = {
        key: 0
        for key in (
            "new_entry", "complete_exit", "quantity_increase",
            "quantity_decrease", "unchanged",
        )
    }
    for scheme_id in _candidate_scheme_ids(
        conn, publisher, previous_month, current_month
    ):
        previous_evidence, previous_import_id, previous_complete = _pointer_evidence(
            conn, publisher=publisher, scheme_id=scheme_id, month=previous_month
        )
        current_evidence, current_import_id, current_complete = _pointer_evidence(
            conn, publisher=publisher, scheme_id=scheme_id, month=current_month
        )
        previous_rows: dict[int, dict] = {}
        current_rows: dict[int, dict] = {}
        issues: list[dict] = []
        holding_inputs: dict[str, list[dict]] = {"previous": [], "current": []}
        if previous_complete and previous_import_id is not None:
            previous_rows, prior_issues, holding_inputs["previous"] = _eligible_holdings(
                conn, publisher=publisher, scheme_id=scheme_id,
                month=previous_month, import_file_id=previous_import_id,
            )
            issues.extend(prior_issues)
        if current_complete and current_import_id is not None:
            current_rows, current_issues, holding_inputs["current"] = _eligible_holdings(
                conn, publisher=publisher, scheme_id=scheme_id,
                month=current_month, import_file_id=current_import_id,
            )
            issues.extend(current_issues)
        complete = previous_complete and current_complete and not issues
        if not previous_complete:
            reason_code = "previous_accepted_disclosure_missing"
        elif not current_complete:
            reason_code = "current_accepted_disclosure_missing"
        elif issues:
            reason_code = "holdings_not_comparable"
        else:
            reason_code = "complete_consecutive_accepted_disclosures"
        evidence = {
            "completeness_rule_version": "complete_accepted_scheme_month_v1",
            "months_consecutive": True,
            "previous": previous_evidence,
            "current": current_evidence,
            "eligible_holding_counts": {
                "previous": len(previous_rows),
                "current": len(current_rows),
            },
            "holding_issues": issues,
            "absent_row_as_zero_authorized": complete,
        }
        fingerprint_payload = {
            "engine_version": CHANGE_ENGINE_VERSION,
            "publisher": publisher,
            "scheme_id": scheme_id,
            "previous_month": previous_month,
            "current_month": current_month,
            "previous_import_file_id": previous_import_id,
            "current_import_file_id": current_import_id,
            "reason_code": reason_code,
            "evidence": evidence,
            "holding_inputs": holding_inputs,
        }
        fingerprint = hashlib.sha256(_json(fingerprint_payload).encode("utf-8")).hexdigest()
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO mf_scheme_month_change_sets
                   (publisher, scheme_id, previous_month, current_month,
                    previous_import_file_id, current_import_file_id,
                    comparison_status, reason_code, completeness_evidence_json,
                    engine_version, input_fingerprint, generated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    publisher, scheme_id, previous_month, current_month,
                    previous_import_id, current_import_id,
                    "generated" if complete else "skipped", reason_code,
                    _json(evidence), CHANGE_ENGINE_VERSION, fingerprint,
                    generated_at_utc,
                ),
            )
            change_set = conn.execute(
                """SELECT change_set_id, comparison_status
                   FROM mf_scheme_month_change_sets WHERE input_fingerprint=?""",
                (fingerprint,),
            ).fetchone()
            if change_set is None:
                raise RuntimeError("Failed to persist mutual-fund comparison audit")
            change_set_id = int(change_set["change_set_id"])
            if not complete:
                _persist_change_validations(
                    conn,
                    change_set_id=change_set_id,
                    previous_complete=previous_complete,
                    current_complete=current_complete,
                    issues=issues,
                    expected_change_count=None,
                    actual_change_count=0,
                )
                skipped_sets += 1
                continue
            generated_sets += 1
            evidence_json = _json(evidence)
            assert previous_import_id is not None and current_import_id is not None
            for security_id in sorted(set(previous_rows) | set(current_rows)):
                previous = previous_rows.get(security_id)
                current = current_rows.get(security_id)
                previous_quantity = (
                    _decimal(previous["quantity_decimal"], field="previous_quantity")
                    if previous is not None else Decimal(0)
                )
                current_quantity = (
                    _decimal(current["quantity_decimal"], field="current_quantity")
                    if current is not None else Decimal(0)
                )
                previous_weight = (
                    _decimal(previous["percentage_of_nav_decimal"], field="previous_weight")
                    if previous is not None else Decimal(0)
                )
                current_weight = (
                    _decimal(current["percentage_of_nav_decimal"], field="current_weight")
                    if current is not None else Decimal(0)
                )
                change_class = _change_class(previous_quantity, current_quantity)
                identity_row = current if current is not None else previous
                if identity_row is None:
                    raise RuntimeError("Comparison security has no source holding")
                isin = str(identity_row["canonical_isin"])
                conn.execute(
                    """INSERT OR IGNORE INTO mf_scheme_security_month_changes
                       (change_set_id, publisher, scheme_id, security_id,
                        isin_normalized, previous_month, current_month,
                        previous_holding_id, current_holding_id,
                        previous_row_presence, current_row_presence,
                        previous_quantity_decimal, current_quantity_decimal,
                        quantity_change_decimal, previous_weight_decimal,
                        current_weight_decimal, portfolio_weight_change_decimal,
                        change_class, previous_import_file_id,
                        current_import_file_id, completeness_evidence_json,
                        generated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?)""",
                    (
                        change_set_id, publisher, scheme_id, security_id, isin,
                        previous_month, current_month,
                        int(previous["holding_id"]) if previous is not None else None,
                        int(current["holding_id"]) if current is not None else None,
                        "present" if previous is not None else "absent",
                        "present" if current is not None else "absent",
                        str(previous["quantity_decimal"]) if previous is not None else None,
                        str(current["quantity_decimal"]) if current is not None else None,
                        _decimal_text(current_quantity - previous_quantity),
                        str(previous["percentage_of_nav_decimal"])
                        if previous is not None else None,
                        str(current["percentage_of_nav_decimal"])
                        if current is not None else None,
                        _decimal_text(current_weight - previous_weight),
                        change_class, previous_import_id, current_import_id,
                        evidence_json, generated_at_utc,
                    ),
                )
            persisted_counts = conn.execute(
                """SELECT change_class, COUNT(*) AS count
                   FROM mf_scheme_security_month_changes
                   WHERE change_set_id=? GROUP BY change_class""",
                (change_set_id,),
            ).fetchall()
            for row in persisted_counts:
                count = int(row["count"])
                class_counts[str(row["change_class"])] += count
                change_count += count
            expected_count = len(set(previous_rows) | set(current_rows))
            actual_count = sum(int(row["count"]) for row in persisted_counts)
            _persist_change_validations(
                conn,
                change_set_id=change_set_id,
                previous_complete=previous_complete,
                current_complete=current_complete,
                issues=issues,
                expected_change_count=expected_count,
                actual_change_count=actual_count,
            )
            if actual_count != expected_count:
                raise RuntimeError(
                    "Mutual-fund change rows did not reconcile to the security union"
                )
    return ChangeGenerationSummary(
        publisher, previous_month, current_month, generated_sets,
        skipped_sets, change_count, class_counts,
    )


def refresh_changes_for_accepted_month(
    conn: sqlite3.Connection,
    *,
    publisher: str,
    changed_month: str,
    generated_at_utc: str,
) -> ChangeRefreshSummary:
    """Refresh the pair ending at this month and any accepted following month."""
    comparisons = [
        generate_scheme_month_changes(
            conn, publisher=publisher, current_month=changed_month,
            generated_at_utc=generated_at_utc,
        )
    ]
    following = adjacent_month(changed_month, 1)
    has_following_pointer = conn.execute(
        """SELECT 1 FROM mf_accepted_versions
           WHERE publisher=? AND disclosure_month=? LIMIT 1""",
        (publisher, following),
    ).fetchone()
    if has_following_pointer is not None:
        comparisons.append(
            generate_scheme_month_changes(
                conn, publisher=publisher, current_month=following,
                generated_at_utc=generated_at_utc,
            )
        )
    return ChangeRefreshSummary(publisher, changed_month, tuple(comparisons))
