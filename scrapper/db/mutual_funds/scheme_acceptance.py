"""Scheme registry, classification and auditable workbook acceptance."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from ...sources.amc_workbook.adapters import adapter_for_parsed
from ...sources.amc_workbook.ppfas import PPFASWorkbook
from .workbook_inventory import _json
from .holdings import M03_VALIDATION_RULES, _upsert_m03_validation

@dataclass(frozen=True)
class SchemeRegistrySummary:
    scheme_count: int
    active_equity_scheme_count: int
    classified_holding_count: int
    active_equity_holding_count: int
    classification_rule_version: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()

@dataclass(frozen=True)
class ImportFinalization:
    import_state: str
    validation_status: str
    failed_error_rules: tuple[str, ...]
    failed_warning_rules: tuple[str, ...]
    scheme_registry: SchemeRegistrySummary
    superseded_import_file_ids: tuple[int, ...]

    def to_dict(self) -> dict:
        return {
            **self.__dict__,
            "failed_error_rules": list(self.failed_error_rules),
            "failed_warning_rules": list(self.failed_warning_rules),
            "superseded_import_file_ids": list(self.superseded_import_file_ids),
            "scheme_registry": self.scheme_registry.to_dict(),
        }

M04_VALIDATION_RULES = (
    "m04_prior_contract_complete",
    "m04_prior_blocking_validations_pass",
    "m04_scheme_registry_complete",
    "m04_scheme_classifications_complete",
    "m04_holdings_scheme_links_complete",
)

def _resolved_classification(
    conn: sqlite3.Connection, scheme_id: int, disclosure_month: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM mf_scheme_classifications
           WHERE scheme_id=? AND valid_from_month<=?
             AND (valid_to_month IS NULL OR valid_to_month>=?)
           ORDER BY scheme_classification_id DESC LIMIT 1""",
        (scheme_id, disclosure_month, disclosure_month),
    ).fetchone()

def persist_scheme_registry(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    parsed: PPFASWorkbook,
) -> SchemeRegistrySummary:
    """Persist append-only names/classifications and link every normalized row."""
    adapter = adapter_for_parsed(parsed)
    manifest = adapter.load_manifest()
    registry = manifest["scheme_registry"]
    declared = registry["schemes"]
    observed_at = conn.execute(
        "SELECT first_observed_at_utc FROM mf_import_files WHERE import_file_id=?",
        (import_file_id,),
    ).fetchone()
    if observed_at is None:
        raise RuntimeError("Cannot classify schemes for a missing import")
    created_at = str(observed_at["first_observed_at_utc"])
    resolved: dict[str, tuple[int, sqlite3.Row]] = {}
    with conn:
        for sheet in parsed.sheets:
            policy = declared.get(sheet.scheme_code)
            if policy is None:
                continue
            scheme = conn.execute(
                "SELECT scheme_id, current_name FROM mf_schemes WHERE publisher=? AND scheme_code=?",
                (parsed.detected_publisher, sheet.scheme_code),
            ).fetchone()
            if scheme is None:
                cursor = conn.execute(
                    """INSERT INTO mf_schemes(publisher, scheme_code, current_name)
                       VALUES (?, ?, ?)""",
                    (
                        parsed.detected_publisher, sheet.scheme_code,
                        sheet.scheme_name_raw,
                    ),
                )
                scheme_id = int(cursor.lastrowid)
            else:
                scheme_id = int(scheme["scheme_id"])
                if str(scheme["current_name"]) != sheet.scheme_name_raw:
                    conn.execute(
                        "UPDATE mf_schemes SET current_name=? WHERE scheme_id=?",
                        (sheet.scheme_name_raw, scheme_id),
                    )
            conn.execute(
                """INSERT OR IGNORE INTO mf_scheme_name_versions
                   (scheme_id, scheme_name_raw, valid_from_month, valid_to_month,
                    naming_source, naming_rule_version, first_seen_import_file_id,
                    created_at_utc)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    scheme_id, sheet.scheme_name_raw,
                    parsed.detected_disclosure_month,
                    registry["classification_source"],
                    registry["naming_rule_version"], import_file_id, created_at,
                ),
            )
            conn.execute(
                """INSERT OR IGNORE INTO mf_scheme_classifications
                   (scheme_id, scheme_management_type, is_equity_scheme,
                    scheme_category, valid_from_month, valid_to_month,
                    classification_source, classification_rule_version,
                    first_seen_import_file_id, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    scheme_id, policy["scheme_management_type"],
                    int(policy["is_equity_scheme"]), policy["scheme_category"],
                    policy.get("valid_from_month", registry["valid_from_month"]),
                    registry["classification_source"],
                    registry["classification_rule_version"], import_file_id,
                    created_at,
                ),
            )
            classification = _resolved_classification(
                conn, scheme_id, parsed.detected_disclosure_month
            )
            if classification is not None:
                resolved[sheet.scheme_code] = (scheme_id, classification)
                conn.execute(
                    """UPDATE mf_holdings
                       SET scheme_id=?, scheme_category=?
                       WHERE import_file_id=? AND scheme_code=?""",
                    (
                        scheme_id, str(classification["scheme_category"]),
                        import_file_id, sheet.scheme_code,
                    ),
                )

    active_codes = {
        code
        for code, (_, classification) in resolved.items()
        if classification["scheme_management_type"] == "active"
        and int(classification["is_equity_scheme"]) == 1
    }
    holding_counts = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(scheme_id IS NOT NULL AND scheme_category IS NOT NULL) AS classified
           FROM mf_holdings WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    active_holding_count = 0
    if active_codes:
        placeholders = ",".join("?" for _ in active_codes)
        active_holding_count = int(
            conn.execute(
                f"""SELECT COUNT(*) FROM mf_holdings
                     WHERE import_file_id=? AND scheme_code IN ({placeholders})""",
                (import_file_id, *sorted(active_codes)),
            ).fetchone()[0]
        )
    return SchemeRegistrySummary(
        scheme_count=len(resolved),
        active_equity_scheme_count=len(active_codes),
        classified_holding_count=int(holding_counts["classified"] or 0),
        active_equity_holding_count=active_holding_count,
        classification_rule_version=str(registry["classification_rule_version"]),
    )

def _transition_import_state(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    to_state: str,
    changed_at_utc: str,
    changed_by: str,
    reason: dict,
) -> bool:
    current = conn.execute(
        "SELECT import_state FROM mf_import_files WHERE import_file_id=?",
        (import_file_id,),
    ).fetchone()
    if current is None:
        raise RuntimeError("Cannot transition a missing workbook import")
    from_state = str(current["import_state"])
    if from_state == to_state:
        return False
    allowed = {
        "inbox": {"accepted", "rejected", "quarantine"},
        "rejected": {"accepted", "quarantine"},
        "accepted": set(),
        "quarantine": set(),
    }
    if to_state not in allowed[from_state]:
        raise RuntimeError(f"Invalid mutual-fund import transition: {from_state}->{to_state}")
    reason_json = _json(reason)
    conn.execute(
        """UPDATE mf_import_files
           SET import_state=?, accepted_at_utc=CASE WHEN ?='accepted' THEN ? ELSE NULL END,
               state_reason_json=?
           WHERE import_file_id=?""",
        (to_state, to_state, changed_at_utc, reason_json, import_file_id),
    )
    conn.execute(
        """INSERT INTO mf_import_state_events
           (import_file_id, from_state, to_state, changed_at_utc, changed_by,
            reason_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            import_file_id, from_state, to_state, changed_at_utc, changed_by,
            reason_json,
        ),
    )
    return True

def finalize_ppfas_import(
    conn: sqlite3.Connection,
    *,
    import_file_id: int,
    parsed: PPFASWorkbook,
    decided_at_utc: str,
) -> ImportFinalization:
    """Validate, classify, and atomically accept or reject one complete workbook."""
    adapter = adapter_for_parsed(parsed)
    registry_summary = persist_scheme_registry(
        conn, import_file_id=import_file_id, parsed=parsed
    )
    expected_prior = set(adapter.structure_validation_rules) | set(M03_VALIDATION_RULES)
    prior_rows = conn.execute(
        """SELECT rule_code, severity, passed
           FROM mf_workbook_validation_results
           WHERE import_file_id=? AND rule_code NOT LIKE 'm04_%'""",
        (import_file_id,),
    ).fetchall()
    prior_codes = {str(row["rule_code"]) for row in prior_rows}
    prior_errors_pass = all(
        int(row["passed"]) == 1
        for row in prior_rows
        if str(row["severity"]) == "error"
    )
    sheet_codes = {
        str(row["detected_scheme_code"])
        for row in conn.execute(
            """SELECT detected_scheme_code FROM mf_workbook_sheets
               WHERE import_file_id=? AND sheet_classification='portfolio'""",
            (import_file_id,),
        )
        if row["detected_scheme_code"] is not None
    }
    manifest = adapter.load_manifest()
    manifest_codes = set(
        adapter.expected_scheme_codes(manifest, parsed.detected_disclosure_month)
    )
    holding_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM mf_holdings WHERE import_file_id=?",
            (import_file_id,),
        ).fetchone()[0]
    )
    classifications_complete = (
        registry_summary.scheme_count == len(sheet_codes) == len(manifest_codes)
    )
    links_complete = (
        holding_count > 0
        and registry_summary.classified_holding_count == holding_count
        and conn.execute(
            """SELECT COUNT(*) FROM mf_holdings
               WHERE import_file_id=? AND scheme_id IS NULL""",
            (import_file_id,),
        ).fetchone()[0] == 0
    )
    validation_details = {
        "expected_prior_rule_count": len(expected_prior),
        "present_prior_rule_count": len(prior_codes & expected_prior),
        "sheet_scheme_codes": sorted(sheet_codes),
        "manifest_scheme_codes": sorted(manifest_codes),
        "holding_count": holding_count,
        **registry_summary.to_dict(),
    }
    rules = (
        ("m04_prior_contract_complete", expected_prior.issubset(prior_codes)),
        ("m04_prior_blocking_validations_pass", prior_errors_pass),
        ("m04_scheme_registry_complete", sheet_codes == manifest_codes),
        ("m04_scheme_classifications_complete", classifications_complete),
        ("m04_holdings_scheme_links_complete", links_complete),
    )
    with conn:
        for rule_code, passed in rules:
            _upsert_m03_validation(
                conn, import_file_id, rule_code, severity="error",
                passed=passed, details=validation_details,
            )

    failed_errors = tuple(
        str(row["rule_code"])
        for row in conn.execute(
            """SELECT rule_code FROM mf_workbook_validation_results
               WHERE import_file_id=? AND severity='error' AND passed=0
               ORDER BY rule_code""",
            (import_file_id,),
        )
    )
    failed_warnings = tuple(
        str(row["rule_code"])
        for row in conn.execute(
            """SELECT rule_code FROM mf_workbook_validation_results
               WHERE import_file_id=? AND severity='warning' AND passed=0
               ORDER BY rule_code""",
            (import_file_id,),
        )
    )
    if failed_errors:
        reason = {
            "decision_code": "deterministic_validation_failed",
            "failed_error_rules": list(failed_errors),
            "failed_warning_rules": list(failed_warnings),
        }
        with conn:
            _transition_import_state(
                conn, import_file_id=import_file_id, to_state="rejected",
                changed_at_utc=decided_at_utc, changed_by="m04_acceptance_workflow",
                reason=reason,
            )
        return ImportFinalization(
            "rejected", "failed", failed_errors, failed_warnings,
            registry_summary, (),
        )

    validation_status = "passed_with_warnings" if failed_warnings else "passed"
    imported = conn.execute(
        """SELECT publisher, detected_disclosure_month, import_state
           FROM mf_import_files WHERE import_file_id=?""",
        (import_file_id,),
    ).fetchone()
    if imported is None or imported["detected_disclosure_month"] is None:
        raise RuntimeError("Accepted workbook is missing detected source identity")
    publisher = str(imported["publisher"])
    disclosure_month = str(imported["detected_disclosure_month"])
    schemes = conn.execute(
        """SELECT DISTINCT scheme_id FROM mf_holdings
           WHERE import_file_id=? ORDER BY scheme_id""",
        (import_file_id,),
    ).fetchall()
    superseded: set[int] = set()
    with conn:
        if str(imported["import_state"]) != "accepted":
            for scheme_row in schemes:
                scheme_id = int(scheme_row["scheme_id"])
                classification = _resolved_classification(
                    conn, scheme_id, disclosure_month
                )
                if classification is None:
                    raise RuntimeError("Scheme classification disappeared during acceptance")
                previous = conn.execute(
                    """SELECT import_file_id FROM mf_accepted_versions
                       WHERE publisher=? AND scheme_id=? AND disclosure_month=?""",
                    (publisher, scheme_id, disclosure_month),
                ).fetchone()
                previous_id = int(previous["import_file_id"]) if previous else None
                if previous_id is not None and previous_id != import_file_id:
                    superseded.add(previous_id)
                conn.execute(
                    """INSERT OR IGNORE INTO mf_accepted_version_events
                       (publisher, scheme_id, disclosure_month, import_file_id,
                        previous_import_file_id, scheme_classification_id,
                        accepted_at_utc, accepted_by, acceptance_mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'm04_acceptance_workflow',
                               'automatic_complete_contract')""",
                    (
                        publisher, scheme_id, disclosure_month, import_file_id,
                        previous_id, int(classification["scheme_classification_id"]),
                        decided_at_utc,
                    ),
                )
                conn.execute(
                    """INSERT INTO mf_accepted_versions
                       (publisher, scheme_id, disclosure_month, import_file_id,
                        accepted_at_utc, accepted_by, acceptance_mode)
                       VALUES (?, ?, ?, ?, ?, 'm04_acceptance_workflow',
                               'automatic_complete_contract')
                       ON CONFLICT(publisher, scheme_id, disclosure_month) DO UPDATE SET
                         import_file_id=excluded.import_file_id,
                         accepted_at_utc=excluded.accepted_at_utc,
                         accepted_by=excluded.accepted_by,
                         acceptance_mode=excluded.acceptance_mode""",
                    (
                        publisher, scheme_id, disclosure_month, import_file_id,
                        decided_at_utc,
                    ),
                )
            reason = {
                "decision_code": "complete_contract_accepted",
                "validation_status": validation_status,
                "failed_warning_rules": list(failed_warnings),
                "scheme_count": len(schemes),
                "superseded_import_file_ids": sorted(superseded),
            }
            _transition_import_state(
                conn, import_file_id=import_file_id, to_state="accepted",
                changed_at_utc=decided_at_utc, changed_by="m04_acceptance_workflow",
                reason=reason,
            )
            conn.execute(
                """UPDATE mf_holdings SET accepted_at_utc=?
                   WHERE import_file_id=?""",
                (decided_at_utc, import_file_id),
            )
            conn.execute(
                """INSERT INTO mf_workbook_acceptance_events
                   (import_file_id, decided_at_utc, decided_by, acceptance_mode,
                    validation_status, scheme_count,
                    previous_import_file_ids_json, decision_json)
                   VALUES (?, ?, 'm04_acceptance_workflow',
                           'automatic_complete_contract', ?, ?, ?, ?)""",
                (
                    import_file_id, decided_at_utc, validation_status, len(schemes),
                    _json(sorted(superseded)), _json(reason),
                ),
            )
    return ImportFinalization(
        "accepted", validation_status, (), failed_warnings, registry_summary,
        tuple(sorted(superseded)),
    )
