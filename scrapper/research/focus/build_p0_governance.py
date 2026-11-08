"""Build the bounded FG-00 governance pack from historical sources.

The builder is append-only with respect to source data and read-only with
respect to SQLite.  Missing decision-universe or archetype authority produces
review files and a non-queueable YELLOW manifest; it never assigns label zero.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...config import assert_configured_writable_targets

from ..governance.focus_p0 import (
    ArchetypeApprovalState,
    DecisionUniverseState,
    IdentityResolutionState,
    assign_outcome_label,
    propose_archetype,
    resolve_historical_symbol,
    resolve_verified_exchange_identifier,
    select_p0_companies,
    stable_payload_hash,
    validate_p0_manifest,
)


GOVERNANCE_VERSION = "FG00-v001"
LABEL_SOURCE_VERSION = "FOCUS_LABEL_REGISTRY_v001"
UNIVERSE_SOURCE_VERSION = "DECISION_UNIVERSE_v001"
ARCHETYPE_MAPPING_VERSION = "ANALYSIS_ARCHETYPE_MAPPING_v001"
SELECTION_SEED = 20260816
Q4 = "FY2026-Q4"
Q1 = "FY2027-Q1"
QUARTERS = {
    Q4: {"period_end": "2026-03-31", "cutoff": "2026-08-02"},
    Q1: {"period_end": "2026-06-30", "cutoff": "2026-08-03"},
}
Q4_EXPECTED_POSITIVES = 239
Q1_EXPECTED_POSITIVES = 191
# These aliases were checked against the captured official exchange master and
# the existing Screener-origin company name.  They are deliberately narrow:
# an unlisted OCR token or an instrument outside the equity scope must remain
# quarantined instead of being guessed from a similar name.
FOCUS_VERIFIED_EXCHANGE_IDENTIFIERS = {
    "AIMTRON": ("NSE", "AIMTRON"),
    "ALPEXSOLAR": ("NSE", "ALPEXSOLAR"),
    "AMAL": ("NSE", "AMAL"),
    "CNCRD": ("BSE", "543619"),
    "GSMFOILS": ("NSE", "GSMFOILS"),
    "KPGEL": ("NSE", "KPIGREEN"),
    "MONOLITH": ("NSE", "MONOLITH"),
    "NAMOEWASTE": ("NSE", "NAMOEWASTE"),
    "TTKPRESTIGE": ("NSE", "TTKPRESTIG"),
    "VMARCIND": ("NSE", "VMARCIND"),
    "YASHHV": ("BSE", "544310"),
}
ALLOWED_ARCHETYPES = {
    "OPERATING_COMPANY",
    "PROJECT_EPC",
    "MANUFACTURING",
    "BANK",
    "NBFC",
    "INSURER",
    "IT_SERVICES",
    "PHARMA",
    "CONSUMER",
    "OTHER",
}

Q4_CATEGORY_NAMES = {
    "FMCG OTHER",
    "AEROSPACE",
    "EMS",
    "SHIP BUILDING",
    "RAILWAY",
    "INFRA",
    "REALTY",
    "WIRES",
    "PLYWOOD MFG",
    "AUTO ANCILLIARY",
    "TRANSFORMERS",
    "METALS",
    "INDUSTRIAL PRODUCTS",
    "DATA CENTER",
    "MINING",
    "FINANCIALS",
    "PHARMA",
    "POLYMENR",
    "CHEMICALS",
    "BANKS",
    "TYRE",
    "HEALTHCARE",
    "POWER",
    "DEFENCE",
    "LOGISTICS",
    "TEXTILES",
    "FERTILIZERS",
    "GOLD",
    "COMMODITY",
    "OTHERS",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def load_decision_universe_approvals(
    path: Path,
    *,
    allowed_company_ids: set[int],
) -> dict[int, bool]:
    approvals: dict[int, bool] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            company_id = int(row["company_id"])
            if company_id not in allowed_company_ids:
                raise ValueError(f"Decision approval has unknown company_id {company_id}")
            value = (row.get("approve_as_decision_universe_member") or "").strip().upper()
            if value not in {"", "YES", "NO"}:
                raise ValueError(
                    f"Invalid decision-universe approval {value!r} for company_id {company_id}"
                )
            if value:
                approvals[company_id] = value == "YES"
    return approvals


def load_archetype_approvals(
    path: Path,
    *,
    proposed_by_company: Mapping[int, str | None],
) -> dict[int, str]:
    approvals: dict[int, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            company_id = int(row["company_id"])
            if company_id not in proposed_by_company:
                raise ValueError(f"Archetype approval has unknown company_id {company_id}")
            approve = (row.get("approve") or "").strip().upper()
            override = (row.get("override_archetype") or "").strip().upper()
            if approve not in {"", "YES", "NO"}:
                raise ValueError(
                    f"Invalid archetype approval {approve!r} for company_id {company_id}"
                )
            if override and override not in ALLOWED_ARCHETYPES:
                raise ValueError(
                    f"Invalid archetype override {override!r} for company_id {company_id}"
                )
            if approve == "YES":
                selected = override or proposed_by_company[company_id]
                if not selected:
                    raise ValueError(
                        f"company_id {company_id} needs an allowed override_archetype"
                    )
                approvals[company_id] = selected
    return approvals


def _normalize_symbol_token(token: str) -> str:
    return re.sub(r"[^A-Z0-9&-]", "", token.upper().strip().strip("_-&"))


def _ocr_symbol_candidates(line: str) -> list[str]:
    """Return leading uppercase tokens before the human-readable company name."""

    candidates: list[str] = []
    for token in line.split()[:10]:
        fragments = [value for value in re.split(r"[_|\\]+", token) if value]
        fragment = fragments[0] if fragments else token
        letters = "".join(character for character in fragment if character.isalpha())
        source_marker = "_" in token
        normalized = _normalize_symbol_token(fragment)
        if letters and letters != letters.upper() and not source_marker:
            if candidates:
                break
            continue
        if (
            len(normalized) >= 2
            and normalized not in {"INR", "NR", "BINR"}
            and not re.fullmatch(r"\d+(?:\.\d+)?", normalized)
        ):
            candidates.append(normalized)
    return list(dict.fromkeys(candidates))


def _normalize_company_name(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", value.upper())
    suffixes = {
        "LIMITED",
        "LTD",
        "PRIVATE",
        "PVT",
        "INDIA",
        "CO",
        "COMPANY",
    }
    tokens = [token for token in normalized.split() if token not in suffixes]
    return " ".join(tokens)


def _known_nse_symbols(conn: sqlite3.Connection) -> set[str]:
    values = {
        str(row[0]).strip().upper()
        for row in conn.execute(
            """SELECT identifier_value FROM security_identifiers
                WHERE identifier_type='NSE'
                UNION SELECT source_symbol FROM source_identifiers
                WHERE source_symbol IS NOT NULL"""
        )
        if row[0]
    }
    values.update(
        str(row[0]).strip().upper()
        for row in conn.execute(
            "SELECT nse_symbol FROM exchange_securities WHERE nse_symbol IS NOT NULL"
        )
        if row[0]
    )
    return values


def ingest_q1_focus(source_dir: Path) -> list[dict[str, Any]]:
    source = source_dir / "stocks.csv"
    workbook = source_dir / "screener_focus_import.xlsx"
    rows: list[dict[str, Any]] = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for source_row, raw in enumerate(csv.DictReader(handle), start=2):
            symbol = (raw.get("Stock") or "").strip().upper()
            if not symbol:
                rows.append(
                    {
                        "quarter": Q1,
                        "source_file": str(source),
                        "source_row": source_row,
                        "source_symbol": None,
                        "source_name": None,
                        "source_classification": raw.get("Subsector"),
                        "source_hash": file_sha256(source),
                        "corroborating_source_file": str(workbook),
                        "corroborating_source_hash": file_sha256(workbook),
                    }
                )
                continue
            rows.append(
                {
                    "quarter": Q1,
                    "source_file": str(source),
                    "source_row": source_row,
                    "source_symbol": symbol,
                    "source_name": None,
                    "source_classification": (raw.get("Subsector") or "").strip(),
                    "source_hash": file_sha256(source),
                    "corroborating_source_file": str(workbook),
                    "corroborating_source_hash": file_sha256(workbook),
                }
            )
    if len(rows) != Q1_EXPECTED_POSITIVES:
        raise ValueError(
            f"Q1 Focus source expected {Q1_EXPECTED_POSITIVES} rows; found {len(rows)}"
        )
    return rows


def _detect_q4_category(line: str) -> str | None:
    cleaned = re.sub(r"[^A-Z0-9 -]+", " ", line.upper())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    matches = [category for category in Q4_CATEGORY_NAMES if category in cleaned]
    return max(matches, key=len) if matches else None


def capture_q4_ocr_evidence(screenshot_dir: Path, output_path: Path) -> Path:
    """Capture deterministic OCR text beside hashes without interpreting rows."""

    screenshots: list[dict[str, Any]] = []
    for screenshot in sorted(screenshot_dir.glob("*.png")):
        completed = subprocess.run(
            ["tesseract", str(screenshot), "stdout", "--psm", "6"],
            check=True,
            capture_output=True,
            text=True,
        )
        screenshots.append(
            {
                "source_file": str(screenshot),
                "source_hash": file_sha256(screenshot),
                "tesseract_psm": 6,
                "ocr_text": completed.stdout,
            }
        )
    if not screenshots:
        raise ValueError(f"No Q4 Focus screenshots found under {screenshot_dir}")
    _write_json(
        output_path,
        {
            "evidence_version": "Q4_OCR_EVIDENCE_v001",
            "engine": "tesseract",
            "screenshots": screenshots,
        },
    )
    return output_path


def ingest_q4_focus(
    screenshot_dir: Path,
    *,
    known_symbols: set[str],
    ocr_evidence_path: Path | None = None,
) -> list[dict[str, Any]]:
    occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched_lines: list[dict[str, Any]] = []
    if ocr_evidence_path is not None:
        evidence_payload = json.loads(ocr_evidence_path.read_text(encoding="utf-8"))
        screenshot_evidence = list(evidence_payload.get("screenshots") or [])
    else:
        screenshot_paths = sorted(screenshot_dir.glob("*.png"))
        if not screenshot_paths:
            raise ValueError(f"No Q4 Focus screenshots found under {screenshot_dir}")
        screenshot_evidence = []
        for screenshot in screenshot_paths:
            completed = subprocess.run(
                ["tesseract", str(screenshot), "stdout", "--psm", "6"],
                check=True,
                capture_output=True,
                text=True,
            )
            screenshot_evidence.append(
                {
                    "source_file": str(screenshot),
                    "source_hash": file_sha256(screenshot),
                    "ocr_text": completed.stdout,
                }
            )
    if not screenshot_evidence:
        raise ValueError("Q4 OCR evidence is empty")
    for captured in screenshot_evidence:
        screenshot = Path(str(captured["source_file"]))
        category: str | None = None
        source_hash = str(captured["source_hash"])
        for line_number, raw_line in enumerate(str(captured["ocr_text"]).splitlines(), start=1):
            line = raw_line.strip()
            detected_category = _detect_q4_category(line)
            if detected_category and "INR" not in line.upper():
                category = detected_category
                continue
            if "INR" not in line.upper():
                continue
            candidates = _ocr_symbol_candidates(line)
            known_candidates = [value for value in candidates if value in known_symbols]
            source_symbol = (
                candidates[0]
                if candidates and candidates[0] in known_symbols
                else (
                    known_candidates[0]
                    if candidates and len(candidates[0]) <= 3 and known_candidates
                    else (candidates[0] if candidates else None)
                )
            )
            evidence = {
                "source_file": str(screenshot),
                "source_row": line_number,
                "source_hash": source_hash,
                "ocr_text": line,
                "source_classification": category,
            }
            if source_symbol:
                occurrences[source_symbol].append(evidence)
            else:
                unmatched_lines.append({**evidence, "candidate_symbols": candidates})

    rows: list[dict[str, Any]] = []
    for symbol in sorted(occurrences):
        evidence = sorted(
            occurrences[symbol], key=lambda row: (row["source_file"], row["source_row"])
        )[0]
        rows.append(
            {
                "quarter": Q4,
                "source_file": evidence["source_file"],
                "source_row": evidence["source_row"],
                "source_symbol": symbol,
                "source_name": None,
                "source_classification": evidence["source_classification"],
                "source_hash": evidence["source_hash"],
                "ocr_text": evidence["ocr_text"],
                "source_occurrence_count": len(occurrences[symbol]),
            }
        )
    missing_count = max(0, Q4_EXPECTED_POSITIVES - len(rows))
    for index in range(missing_count):
        rows.append(
            {
                "quarter": Q4,
                "source_file": str(screenshot_dir),
                "source_row": f"expected-count-gap-{index + 1:03d}",
                "source_symbol": None,
                "source_name": None,
                "source_classification": None,
                "source_hash": stable_payload_hash(
                    sorted(
                        (str(item["source_file"]), str(item["source_hash"]))
                        for item in screenshot_evidence
                    )
                ),
                "ocr_text": None,
                "source_occurrence_count": 1,
                "quarantine_reason": "Q4_SCREENSHOT_ROW_NOT_DETERMINISTICALLY_RESOLVED",
            }
        )
    if len(rows) != Q4_EXPECTED_POSITIVES:
        raise ValueError("Q4 registry accounting failed")
    return rows


def _identity_details(conn: sqlite3.Connection, company_id: int) -> dict[str, Any]:
    company = conn.execute(
        "SELECT current_legal_name,status FROM companies WHERE company_id=?", (company_id,)
    ).fetchone()
    identifiers = list(
        conn.execute(
            """SELECT identifier_type,identifier_value,valid_from,valid_to,is_current
                 FROM security_identifiers WHERE company_id=?
                 ORDER BY is_current DESC,identifier_type,identifier_value""",
            (company_id,),
        )
    )
    nse = next(
        (str(row[1]) for row in identifiers if row[0] == "NSE" and row[4]), None
    )
    bse = next(
        (str(row[1]) for row in identifiers if row[0] == "BSE" and row[4]), None
    )
    exchange = conn.execute(
        """SELECT current_isin FROM exchange_securities
            WHERE nse_symbol=? AND current_isin IS NOT NULL
            ORDER BY is_present DESC LIMIT 1""",
        (nse,),
    ).fetchone() if nse else None
    listing = conn.execute(
        """SELECT v.listing_date,v.series_or_group,v.instrument_raw
             FROM exchange_securities es
             JOIN exchange_security_versions v USING(exchange_security_id)
            WHERE es.nse_symbol=?
            ORDER BY v.valid_from_date DESC LIMIT 1""",
        (nse,),
    ).fetchone() if nse else None
    return {
        "company_name": str(company[0]),
        "company_status": str(company[1]),
        "nse_symbol": nse,
        "bse_code": bse,
        "isin": str(exchange[0]) if exchange else None,
        "listing_date": str(listing[0]) if listing and listing[0] else None,
        "series_or_group": str(listing[1]) if listing and listing[1] else None,
        "instrument_raw": str(listing[2]) if listing and listing[2] else None,
    }


def _result_availability(
    conn: sqlite3.Connection,
    *,
    metadata: Mapping[str, Any],
    period_end: str,
    cutoff: str,
) -> dict[str, Any]:
    keys = [value for value in (metadata.get("nse_symbol"), metadata.get("bse_code")) if value]
    clauses = ["isin=?"] if metadata.get("isin") else []
    params: list[Any] = [metadata["isin"]] if metadata.get("isin") else []
    for key in keys:
        clauses.append("exchange_key=?")
        params.append(key)
    if not clauses:
        return {"result_availability": "UNKNOWN", "result_filing_count": 0}
    params.extend([period_end, cutoff + "T23:59:59+00:00"])
    count = conn.execute(
        f"""SELECT COUNT(*) FROM exchange_result_filings
             WHERE ({' OR '.join(clauses)}) AND period_end_date=?
               AND broadcast_at_utc<=?""",
        params,
    ).fetchone()[0]
    return {
        "result_availability": "AVAILABLE" if count else "NOT_FOUND_IN_CAPTURED_FEEDS",
        "result_filing_count": int(count),
    }


def _artifact_shape(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    quarter: str,
) -> dict[str, Any]:
    config = QUARTERS[quarter]
    period_end = config["period_end"]
    cutoff = config["cutoff"]
    counts = {row[0]: int(row[1]) for row in conn.execute(
        """SELECT artifact_type,COUNT(*) FROM document_artifacts
            WHERE company_id=? AND (
              (artifact_type IN ('transcript','presentation','recording') AND period_end=?)
              OR (artifact_type='credit_rating' AND event_date<=?
                  AND event_date>=date(?,'-365 days'))
            ) GROUP BY artifact_type""",
        (company_id, period_end, cutoff, period_end),
    )}
    recovery_count = conn.execute(
        """SELECT COUNT(*) FROM artifact_recovery_targets
            WHERE company_id=? AND event_date<=? AND event_date>=date(?,'-365 days')""",
        (company_id, cutoff, period_end),
    ).fetchone()[0]
    hosts = [str(row[0]) for row in conn.execute(
        """SELECT DISTINCT COALESCE(s.retrieval_host,'')
             FROM document_artifacts a JOIN artifact_sources s USING(artifact_id)
            WHERE a.company_id=? AND a.artifact_type IN ('transcript','presentation','credit_rating')
              AND ((a.artifact_type IN ('transcript','presentation') AND a.period_end=?)
                   OR (a.artifact_type='credit_rating' AND a.event_date<=?
                       AND a.event_date>=date(?,'-365 days')))
            ORDER BY 1""",
        (company_id, period_end, cutoff, period_end),
    ) if row[0]]
    return {
        "transcript_artifact_count": counts.get("transcript", 0),
        "presentation_artifact_count": counts.get("presentation", 0),
        "credit_rating_artifact_count": counts.get("credit_rating", 0),
        "recording_available": bool(counts.get("recording", 0)),
        "alternate_rating_recovery_needed": bool(recovery_count),
        "source_hosts": hosts,
        "ocr_likelihood": False,
    }


def _freeze_artifact_ids(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    quarter: str,
) -> dict[str, list[int]]:
    config = QUARTERS[quarter]
    rows = conn.execute(
        """SELECT artifact_id,artifact_type FROM document_artifacts
            WHERE company_id=? AND (
              (artifact_type IN ('transcript','presentation') AND period_end=?)
              OR (artifact_type='credit_rating' AND event_date<=?
                  AND event_date>=date(?,'-365 days'))
            ) ORDER BY artifact_type,artifact_id""",
        (company_id, config["period_end"], config["cutoff"], config["period_end"]),
    ).fetchall()
    grouped = {"transcripts": [], "presentations": [], "credit_ratings": []}
    keys = {
        "transcript": "transcripts",
        "presentation": "presentations",
        "credit_rating": "credit_ratings",
    }
    for artifact_id, artifact_type in rows:
        grouped[keys[str(artifact_type)]].append(int(artifact_id))
    return grouped


def resolve_focus_registry(
    conn: sqlite3.Connection,
    source_rows: Iterable[Mapping[str, Any]],
    *,
    created_at_utc: str,
) -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in source_rows:
        quarter = str(source["quarter"])
        symbol = source.get("source_symbol")
        key = (
            quarter,
            str(
                symbol
                or f"unresolved:{source['source_file']}:{source['source_row']}"
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        exchange_identifier = (
            FOCUS_VERIFIED_EXCHANGE_IDENTIFIERS.get(str(symbol).strip().upper())
            if symbol
            else None
        )
        resolution = (
            resolve_verified_exchange_identifier(
                conn,
                exchange=exchange_identifier[0],
                identifier=exchange_identifier[1],
            )
            if exchange_identifier
            else (
                resolve_historical_symbol(
                    conn, str(symbol), as_of_date=QUARTERS[quarter]["cutoff"]
                )
                if symbol
                else None
            )
        )
        metadata = (
            _identity_details(conn, int(resolution.company_id))
            if resolution and resolution.company_id is not None
            else {}
        )
        registry.append(
            {
                **dict(source),
                "label": 1,
                "company_id": resolution.company_id if resolution else None,
                "identity_resolution_state": (
                    resolution.state.value
                    if resolution
                    else IdentityResolutionState.UNRESOLVED.value
                ),
                "resolution_method": resolution.method if resolution else "ocr_unresolved",
                "resolved_symbol": resolution.resolved_symbol if resolution else None,
                "historical_symbol": symbol,
                "isin": metadata.get("isin"),
                "bse_code": metadata.get("bse_code"),
                "company_name": metadata.get("company_name"),
                "decision_universe_state": DecisionUniverseState.IN_UNIVERSE.value,
                "outcome_label": assign_outcome_label(
                    is_focus_member=True,
                    universe_state=DecisionUniverseState.IN_UNIVERSE,
                ),
                "label_source_id": f"{quarter}:{source['source_file']}:{source['source_row']}",
                "label_source_version": LABEL_SOURCE_VERSION,
                "ingested_at_utc": created_at_utc,
                "governance_version": GOVERNANCE_VERSION,
            }
        )
    return registry


def _company_name_index(conn: sqlite3.Connection) -> dict[str, set[int]]:
    index: dict[str, set[int]] = defaultdict(set)
    for company_id, name in conn.execute("SELECT company_id,current_legal_name FROM companies"):
        index[_normalize_company_name(str(name))].add(int(company_id))
    return index


def build_negative_approval_pool(
    conn: sqlite3.Connection,
    rejected_csv: Path,
    *,
    focus_company_ids: set[int],
    q4_classification_by_company: Mapping[int, str | None],
    limit: int = 30,
) -> list[dict[str, Any]]:
    name_index = _company_name_index(conn)
    candidates: list[dict[str, Any]] = []
    with rejected_csv.open(encoding="utf-8-sig", newline="") as handle:
        for source_row, raw in enumerate(csv.DictReader(handle), start=2):
            source_name = (raw.get("name") or "").strip()
            matches = name_index.get(_normalize_company_name(source_name), set())
            if len(matches) != 1:
                continue
            company_id = next(iter(matches))
            if company_id in focus_company_ids:
                continue
            metadata = _identity_details(conn, company_id)
            shape = _artifact_shape(conn, company_id=company_id, quarter=Q1)
            availability = _result_availability(
                conn,
                metadata=metadata,
                period_end=QUARTERS[Q1]["period_end"],
                cutoff=QUARTERS[Q1]["cutoff"],
            )
            listing_date = metadata.get("listing_date")
            listed_evidence = (
                "LISTING_DATE_UNKNOWN"
                if not listing_date
                else (
                    "LISTED_BY_CUTOFF"
                    if listing_date <= QUARTERS[Q1]["cutoff"]
                    else "NOT_YET_LISTED"
                )
            )
            row = {
                "company_id": company_id,
                "company_name": metadata.get("company_name"),
                "historical_symbol": metadata.get("nse_symbol"),
                "quarter": Q1,
                "active_listed_evidence": listed_evidence,
                "listing_date": listing_date,
                "instrument_scope": metadata.get("series_or_group") or metadata.get("instrument_raw"),
                **availability,
                "reason_candidate_is_plausible": (
                    f"Q1 contemporaneous derived reject bucket={raw.get('bucket')}; "
                    f"source reason={raw.get('primary_reason')}"
                ),
                "decision_universe_state": DecisionUniverseState.UNKNOWN.value,
                "outcome_label": None,
                "source_file": str(rejected_csv),
                "source_row": source_row,
                "source_hash": file_sha256(rejected_csv),
                "rejection_bucket": raw.get("bucket"),
                "rejection_reason": raw.get("primary_reason"),
                "strength_score": raw.get("strength_score"),
                "source_classification": q4_classification_by_company.get(company_id),
                **metadata,
                **shape,
            }
            candidates.append(row)

    bucket_rank = {
        "strong_but_rejected": 0,
        "near_miss_1fail": 1,
        "near_miss_2fail": 2,
        "partial_fail": 3,
        "obvious_failure": 4,
    }
    candidates.sort(
        key=lambda row: (
            bucket_rank.get(str(row.get("rejection_bucket")), 99),
            -int(row.get("result_filing_count") or 0),
            -sum(
                int(row.get(key) or 0)
                for key in (
                    "transcript_artifact_count",
                    "presentation_artifact_count",
                    "credit_rating_artifact_count",
                )
            ),
            hashlib.sha256(
                f"{SELECTION_SEED}:{row['company_id']}:{Q1}".encode()
            ).hexdigest(),
        )
    )
    return candidates[:limit]


def _positive_review_candidates(
    conn: sqlite3.Connection,
    registry: Iterable[Mapping[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in registry:
        if row.get("company_id") is None or row.get("quarter") != Q1:
            continue
        metadata = _identity_details(conn, int(row["company_id"]))
        proposal = propose_archetype(subsector=row.get("source_classification"))
        if proposal.archetype is None:
            continue
        shape = _artifact_shape(conn, company_id=int(row["company_id"]), quarter=Q1)
        availability = _result_availability(
            conn,
            metadata=metadata,
            period_end=QUARTERS[Q1]["period_end"],
            cutoff=QUARTERS[Q1]["cutoff"],
        )
        candidates.append(
            {
                **dict(row),
                **metadata,
                **shape,
                **availability,
                "proposed_archetype": proposal.archetype.value,
                "mapping_rule_id": proposal.mapping_rule_id,
                "mapping_basis": proposal.mapping_basis,
                "approval_state": proposal.state.value,
                "review_reason": proposal.review_reason,
            }
        )
    by_archetype: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_archetype[str(row["proposed_archetype"])].append(row)
    for rows in by_archetype.values():
        rows.sort(
            key=lambda row: (
                -sum(
                    int(row.get(key) or 0)
                    for key in (
                        "transcript_artifact_count",
                        "presentation_artifact_count",
                        "credit_rating_artifact_count",
                    )
                ),
                hashlib.sha256(
                    f"{SELECTION_SEED}:{row['company_id']}:{Q1}".encode()
                ).hexdigest(),
            )
        )
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(by_archetype.values()):
        for archetype in sorted(by_archetype):
            if by_archetype[archetype] and len(selected) < limit:
                selected.append(by_archetype[archetype].pop(0))
    return selected


def _universe_rows(
    registry: Iterable[Mapping[str, Any]],
    negatives: Iterable[Mapping[str, Any]],
    *,
    quarter: str,
) -> list[dict[str, Any]]:
    rows = [
        {
            "company_id": row.get("company_id"),
            "quarter": quarter,
            "historical_symbol": row.get("historical_symbol"),
            "identity_resolution_state": row.get("identity_resolution_state"),
            "decision_universe_state": row.get("decision_universe_state"),
            "outcome_label": row.get("outcome_label"),
            "eligibility_state": (
                "ELIGIBLE" if row.get("company_id") is not None else "EXCLUDED_IDENTITY_UNRESOLVED"
            ),
            "eligibility_reasons": (
                [] if row.get("company_id") is not None else ["FOCUS_SOURCE_IDENTITY_UNRESOLVED"]
            ),
            "label_source_id": row.get("label_source_id"),
            "universe_source_id": row.get("label_source_id"),
            "universe_source_version": UNIVERSE_SOURCE_VERSION,
            "governance_version": GOVERNANCE_VERSION,
        }
        for row in registry
        if row.get("quarter") == quarter
    ]
    if quarter == Q1:
        rows.extend(
            {
                "company_id": row.get("company_id"),
                "quarter": Q1,
                "historical_symbol": row.get("historical_symbol"),
                "identity_resolution_state": IdentityResolutionState.RESOLVED.value,
                "decision_universe_state": DecisionUniverseState.UNKNOWN.value,
                "outcome_label": None,
                "eligibility_state": "REVIEW_REQUIRED",
                "eligibility_reasons": ["DECISION_UNIVERSE_OWNER_APPROVAL_REQUIRED"],
                "label_source_id": None,
                "universe_source_id": f"Q1_REJECT_CANDIDATE:{row.get('source_row')}",
                "universe_source_version": UNIVERSE_SOURCE_VERSION,
                "governance_version": GOVERNANCE_VERSION,
            }
            for row in negatives
        )
    return rows


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow is required for Parquet export; rerun parquet export with "
            "`uv run --with pyarrow python -m scrapper.pipeline.build_p0_governance "
            "--parquet-only --output-dir ...`"
        ) from exc
    if rows:
        table = pa.Table.from_pylist(rows)
    else:
        table = pa.table(
            {
                "company_id": pa.array([], type=pa.int64()),
                "quarter": pa.array([], type=pa.string()),
                "outcome_label": pa.array([], type=pa.int8()),
            }
        )
    pq.write_table(table, path, compression="zstd")


def write_parquet_exports(output_dir: Path) -> list[Path]:
    specs = (
        ("FOCUS_LABEL_REGISTRY_v001.json", "rows", "FOCUS_LABEL_REGISTRY_v001.parquet"),
        ("Q4_DECISION_UNIVERSE_v001.json", "rows", "Q4_DECISION_UNIVERSE_v001.parquet"),
        ("Q1_DECISION_UNIVERSE_v001.json", "rows", "Q1_DECISION_UNIVERSE_v001.parquet"),
        ("P0_CANDIDATE_POOL_v001.json", "rows", "P0_CANDIDATE_POOL_v001.parquet"),
        ("P0_GOVERNANCE_MANIFEST_v001.json", "companies", "P0_GOVERNANCE_MANIFEST_v001.parquet"),
    )
    outputs: list[Path] = []
    for source_name, key, output_name in specs:
        payload = json.loads((output_dir / source_name).read_text(encoding="utf-8"))
        output = output_dir / output_name
        _write_parquet(output, list(payload.get(key) or []))
        outputs.append(output)
    return outputs


def build_p0_governance(
    conn: sqlite3.Connection,
    *,
    repository_root: Path,
    output_dir: Path,
    created_at_utc: str,
    require_production: bool = True,
    q4_ocr_evidence_path: Path | None = None,
    decision_approval_path: Path | None = None,
    archetype_approval_path: Path | None = None,
) -> dict[str, Any]:
    database_path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    company_count = int(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0])
    artifact_count = int(
        conn.execute("SELECT COUNT(*) FROM document_artifacts").fetchone()[0]
    )
    migration = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    if require_production and (
        database_path != "/data/scraper.db" or company_count < 1000 or artifact_count < 1
    ):
        raise ValueError(
            f"Refusing non-production DB: path={database_path}, companies={company_count}, "
            f"document_artifacts={artifact_count}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    review_dir = output_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    q1_source = (
        repository_root
        / "OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA"
    )
    q4_source = repository_root / "OLD/RESULTS/2026/Q4"
    q1_raw = ingest_q1_focus(q1_source)
    q4_raw = ingest_q4_focus(
        q4_source,
        known_symbols=_known_nse_symbols(conn),
        ocr_evidence_path=q4_ocr_evidence_path,
    )
    registry = resolve_focus_registry(
        conn, [*q4_raw, *q1_raw], created_at_utc=created_at_utc
    )
    registry_payload = {
        "registry_version": LABEL_SOURCE_VERSION,
        "governance_version": GOVERNANCE_VERSION,
        "created_at_utc": created_at_utc,
        "row_count": len(registry),
        "source_hash": stable_payload_hash(
            [{key: row.get(key) for key in ("quarter", "source_file", "source_row", "source_hash")} for row in registry]
        ),
        "rows": registry,
    }
    _write_json(output_dir / "FOCUS_LABEL_REGISTRY_v001.json", registry_payload)

    q4_classification_by_company = {
        int(row["company_id"]): row.get("source_classification")
        for row in registry
        if row.get("quarter") == Q4 and row.get("company_id") is not None
    }
    focus_company_ids = {
        int(row["company_id"])
        for row in registry
        if row.get("quarter") == Q1 and row.get("company_id") is not None
    }
    negative_pool = build_negative_approval_pool(
        conn,
        q1_source / "rejected_candidates.csv",
        focus_company_ids=focus_company_ids,
        q4_classification_by_company=q4_classification_by_company,
    )
    positive_candidates = _positive_review_candidates(conn, registry)

    q4_universe = _universe_rows(registry, negative_pool, quarter=Q4)
    q1_universe = _universe_rows(registry, negative_pool, quarter=Q1)
    _write_json(
        output_dir / "Q4_DECISION_UNIVERSE_v001.json",
        {"status": "PARTIAL", "rows": q4_universe},
    )
    _write_json(
        output_dir / "Q1_DECISION_UNIVERSE_v001.json",
        {"status": "PARTIAL", "rows": q1_universe},
    )

    negative_fields = [
        "company_id",
        "historical_symbol",
        "quarter",
        "active_listed_evidence",
        "listing_date",
        "instrument_scope",
        "result_availability",
        "result_filing_count",
        "reason_candidate_is_plausible",
        "decision_universe_state",
        "outcome_label",
    ]
    _write_csv(
        output_dir / "P0_NEGATIVE_APPROVAL_POOL_v001.csv",
        negative_pool,
        negative_fields,
    )
    decision_review_output = review_dir / "DECISION_UNIVERSE_APPROVAL_REQUIRED.csv"
    if decision_approval_path is None:
        _write_csv(
            decision_review_output,
            [
                {
                    **row,
                    "symbol": row.get("historical_symbol"),
                    "approve_as_decision_universe_member": "",
                    "notes": "",
                }
                for row in negative_pool
            ],
            [
                "company_id",
                "symbol",
                "quarter",
                "approve_as_decision_universe_member",
                "notes",
                "active_listed_evidence",
                "listing_date",
                "instrument_scope",
                "result_availability",
                "reason_candidate_is_plausible",
            ],
        )

    archetype_rows: list[dict[str, Any]] = []
    for row in [*positive_candidates, *negative_pool]:
        if row.get("proposed_archetype"):
            proposal_data = {
                "proposed_archetype": row.get("proposed_archetype"),
                "mapping_rule_id": row.get("mapping_rule_id"),
                "mapping_basis": row.get("mapping_basis"),
                "approval_state": row.get("approval_state"),
                "review_reason": row.get("review_reason"),
            }
        else:
            proposal = propose_archetype(subsector=row.get("source_classification"))
            proposal_data = {
                "proposed_archetype": proposal.archetype.value if proposal.archetype else None,
                "mapping_rule_id": proposal.mapping_rule_id,
                "mapping_basis": proposal.mapping_basis,
                "approval_state": proposal.state.value,
                "review_reason": proposal.review_reason,
            }
        archetype_rows.append(
            {
                "company_id": row.get("company_id"),
                "company_name": row.get("company_name"),
                "sector": row.get("source_classification"),
                "industry": None,
                "subsector": row.get("source_classification"),
                **proposal_data,
            }
        )
    archetype_rows = list(
        {int(row["company_id"]): row for row in archetype_rows if row.get("company_id")}.values()
    )
    _write_csv(
        output_dir / "ANALYSIS_ARCHETYPE_MAPPING_v001.csv",
        archetype_rows,
        [
            "company_id",
            "company_name",
            "sector",
            "industry",
            "subsector",
            "proposed_archetype",
            "mapping_rule_id",
            "mapping_basis",
            "approval_state",
            "review_reason",
        ],
    )
    archetype_review_output = review_dir / "ARCHETYPE_APPROVAL_REQUIRED.csv"
    if archetype_approval_path is None:
        _write_csv(
            archetype_review_output,
            [
                {
                    **row,
                    "approve": "",
                    "override_archetype": "",
                    "notes": "",
                }
                for row in archetype_rows
            ],
            [
                "company_id",
                "company_name",
                "sector",
                "subsector",
                "proposed_archetype",
                "approve",
                "override_archetype",
                "notes",
            ],
        )

    (output_dir / "P0_NEGATIVE_APPROVAL_REQUIRED.md").write_text(
        "# P0 Negative Approval Required\n\n"
        "The Q1 rejected-candidate file is contemporaneous supporting evidence, "
        "not an authoritative mentor decision universe. Every candidate remains "
        "`UNKNOWN` with `outcome_label=NULL`. Review only the targeted rows in "
        "`review/DECISION_UNIVERSE_APPROVAL_REQUIRED.csv`; approve at least 20 "
        "well-evidenced rows to support the intended balanced 40-company pilot.\n",
        encoding="utf-8",
    )
    (review_dir / "APPROVAL_INSTRUCTIONS.md").write_text(
        "# FG-00 Approval Instructions\n\n"
        "## Decision universe\n\n"
        "Edit only `approve_as_decision_universe_member` in "
        "`DECISION_UNIVERSE_APPROVAL_REQUIRED.csv`. Allowed values are `YES` or "
        "`NO`; add optional notes. `YES` means the company genuinely belonged to "
        "the mentor's Q1 decision universe at the cutoff, not merely that it was "
        "absent from Focus. Leave uncertain rows blank.\n\n"
        "## Archetype\n\n"
        "For each required company, set `approve=YES` to approve the proposed "
        "archetype, or put one allowed value in `override_archetype` and explain "
        "it in notes. Allowed archetypes: OPERATING_COMPANY, PROJECT_EPC, "
        "MANUFACTURING, BANK, NBFC, INSURER, IT_SERVICES, PHARMA, CONSUMER, "
        "OTHER. Use `approve=NO` when the row should not enter P0.\n\n"
        "After approval, rerun FG-00 with the same seed (`20260816`) and source "
        "versions. From the repository root run:\n\n"
        "```sh\n"
        "docker compose run --rm -T --entrypoint python -v \"$PWD:/workspace\" "
        "-w /workspace daemon -m scrapper.pipeline.build_p0_governance "
        "--db /data/scraper.db --repository-root /workspace "
        "--output-dir /workspace/artifacts/focus_ml/governance "
        "--q4-ocr-evidence /workspace/artifacts/focus_ml/governance/Q4_OCR_EVIDENCE_v001.json "
        "--decision-approvals /workspace/artifacts/focus_ml/governance/review/DECISION_UNIVERSE_APPROVAL_REQUIRED.csv "
        "--archetype-approvals /workspace/artifacts/focus_ml/governance/review/ARCHETYPE_APPROVAL_REQUIRED.csv "
        "--created-at-utc 2026-08-16T11:00:00Z\n"
        "uv run --with pyarrow python -m scrapper.pipeline.build_p0_governance "
        "--parquet-only --output-dir artifacts/focus_ml/governance\n"
        "```\n\n"
        "The builder hashes both approval files, assigns zero only to approved "
        "universe members, freezes artifact IDs, and re-runs every invariant. It "
        "emits `P0_ACQUISITION_MANIFEST_v001.json` only when GREEN.\n",
        encoding="utf-8",
    )

    proposed_by_company = {
        int(row["company_id"]): row.get("proposed_archetype") for row in archetype_rows
    }
    decision_approvals = (
        load_decision_universe_approvals(
            decision_approval_path,
            allowed_company_ids={int(row["company_id"]) for row in negative_pool},
        )
        if decision_approval_path is not None
        else {}
    )
    archetype_approvals = (
        load_archetype_approvals(
            archetype_approval_path,
            proposed_by_company=proposed_by_company,
        )
        if archetype_approval_path is not None
        else {}
    )
    approval_hashes = {
        "decision_universe": (
            file_sha256(decision_approval_path) if decision_approval_path else None
        ),
        "archetype": file_sha256(archetype_approval_path) if archetype_approval_path else None,
    }
    if decision_approvals:
        for row in q1_universe:
            company_id = row.get("company_id")
            if company_id is None or int(company_id) not in decision_approvals:
                continue
            approved = decision_approvals[int(company_id)]
            row["decision_universe_state"] = (
                DecisionUniverseState.IN_UNIVERSE.value
                if approved
                else DecisionUniverseState.OUT_OF_UNIVERSE.value
            )
            row["outcome_label"] = 0 if approved else None
            row["eligibility_state"] = (
                "ELIGIBLE" if approved else "EXCLUDED_NOT_IN_DECISION_UNIVERSE"
            )
            row["eligibility_reasons"] = (
                [] if approved else ["OWNER_DID_NOT_APPROVE_DECISION_UNIVERSE_MEMBERSHIP"]
            )
        _write_json(
            output_dir / "Q1_DECISION_UNIVERSE_v001.json",
            {
                "status": "APPROVED_SUBSET",
                "approval_source_hash": approval_hashes["decision_universe"],
                "rows": q1_universe,
            },
        )
    if archetype_approvals:
        for row in archetype_rows:
            company_id = int(row["company_id"])
            if company_id in archetype_approvals:
                row["proposed_archetype"] = archetype_approvals[company_id]
                row["approval_state"] = ArchetypeApprovalState.APPROVED.value
                row["review_reason"] = None
        _write_csv(
            output_dir / "ANALYSIS_ARCHETYPE_MAPPING_v001.csv",
            archetype_rows,
            [
                "company_id", "company_name", "sector", "industry", "subsector",
                "proposed_archetype", "mapping_rule_id", "mapping_basis",
                "approval_state", "review_reason",
            ],
        )

    candidate_rows: list[dict[str, Any]] = []
    for row in positive_candidates:
        company_id = int(row["company_id"])
        archetype = archetype_approvals.get(company_id)
        if not archetype:
            continue
        candidate_rows.append(
            {
                **dict(row),
                "decision_universe_state": DecisionUniverseState.IN_UNIVERSE.value,
                "outcome_label": 1,
                "analysis_archetype": archetype,
                "archetype_approval_state": ArchetypeApprovalState.APPROVED.value,
                "identity_resolution_state": row["identity_resolution_state"],
                "label_source": row["label_source_id"],
                "universe_source": row["label_source_id"],
                "archetype_mapping_version": ARCHETYPE_MAPPING_VERSION,
            }
        )
    for row in negative_pool:
        company_id = int(row["company_id"])
        if decision_approvals.get(company_id) is not True:
            continue
        archetype = archetype_approvals.get(company_id)
        if not archetype:
            continue
        candidate_rows.append(
            {
                **dict(row),
                "decision_universe_state": DecisionUniverseState.IN_UNIVERSE.value,
                "outcome_label": 0,
                "analysis_archetype": archetype,
                "archetype_approval_state": ArchetypeApprovalState.APPROVED.value,
                "identity_resolution_state": IdentityResolutionState.RESOLVED.value,
                "label_source": f"{LABEL_SOURCE_VERSION}:approved-universe-nonmember",
                "universe_source": (
                    f"{UNIVERSE_SOURCE_VERSION}:{approval_hashes['decision_universe']}"
                ),
                "archetype_mapping_version": ARCHETYPE_MAPPING_VERSION,
            }
        )
    candidate_payload = {
        "status": "READY" if candidate_rows else "EMPTY_PENDING_APPROVAL",
        "selection_seed": SELECTION_SEED,
        "approval_hashes": approval_hashes,
        "rows": candidate_rows,
        "reason": (
            None
            if candidate_rows
            else "No existing source authorizes both negative membership and approved archetypes."
        ),
    }
    _write_json(output_dir / "P0_CANDIDATE_POOL_v001.json", candidate_payload)

    selected_rows: list[dict[str, Any]] = []
    selection_error: str | None = None
    if candidate_rows:
        try:
            selected_rows = select_p0_companies(
                candidate_rows,
                seed=SELECTION_SEED,
                target_count=40,
                positive_target=20,
            )
        except ValueError as exc:
            selection_error = str(exc)

    manifest_companies: list[dict[str, Any]] = []
    artifact_company: dict[int, int] = {}
    all_artifact_ids: list[int] = []
    for row in selected_rows:
        company_id = int(row["company_id"])
        grouped_artifacts = _freeze_artifact_ids(
            conn, company_id=company_id, quarter=str(row["quarter"])
        )
        for values in grouped_artifacts.values():
            for artifact_id in values:
                artifact_company[artifact_id] = company_id
                all_artifact_ids.append(artifact_id)
        manifest_companies.append(
            {
                "company_id": company_id,
                "quarter": row["quarter"],
                "historical_symbol": row.get("historical_symbol") or row.get("nse_symbol"),
                "isin": row.get("isin"),
                "decision_universe_state": row["decision_universe_state"],
                "outcome_label": row["outcome_label"],
                "analysis_archetype": row["analysis_archetype"],
                "archetype_approval_state": row["archetype_approval_state"],
                "identity_resolution_state": row["identity_resolution_state"],
                "label_source": row["label_source"],
                "universe_source": row["universe_source"],
                "archetype_mapping_version": ARCHETYPE_MAPPING_VERSION,
                "artifact_ids": grouped_artifacts,
                "selection_reason": "deterministic label balance and document-shape ranking",
                "governance_version": GOVERNANCE_VERSION,
            }
        )
    focus_registry_keys = {
        (int(row["company_id"]), str(row["quarter"]))
        for row in registry
        if row.get("company_id") is not None
    }
    invariants = validate_p0_manifest(
        manifest_companies,
        artifact_company=artifact_company,
        focus_registry_keys=focus_registry_keys,
    )
    artifact_type_coverage = {
        kind: sum(len(row["artifact_ids"][kind]) for row in manifest_companies)
        for kind in ("transcripts", "presentations", "credit_ratings")
    }
    invariants["all_automatic_artifact_types_represented"] = all(
        artifact_type_coverage.values()
    )
    gate = "GREEN" if invariants and all(invariants.values()) else "YELLOW"
    blocked_by: list[str] = []
    if len([row for row in candidate_rows if row.get("outcome_label") == 0]) < 20:
        blocked_by.append("DECISION_UNIVERSE_APPROVAL")
    if len(archetype_approvals) < 40:
        blocked_by.append("ARCHETYPE_APPROVAL")
    if selection_error:
        blocked_by.append("P0_SELECTION_COMPOSITION")
    if selected_rows and not invariants["all_automatic_artifact_types_represented"]:
        blocked_by.append("ARTIFACT_TYPE_COVERAGE")
    manifest = {
        "scope_id": "p0-fg00-v001" if gate == "GREEN" else "p0-fg00-v001-NOT-READY",
        "scope_kind": "P0",
        "gate": gate,
        "queueable": gate == "GREEN",
        "governance_version": GOVERNANCE_VERSION,
        "selection_seed": SELECTION_SEED,
        "selection_rule": "20 approved positives plus 20 approved negatives; deterministic artifact-diversity ranking",
        "approval_hashes": approval_hashes,
        "companies": manifest_companies if gate == "GREEN" else [],
        "artifact_types": ["transcript", "presentation", "credit_rating"],
        "artifact_ids": sorted(set(all_artifact_ids)) if gate == "GREEN" else [],
        "blocked_by": sorted(set(blocked_by)),
        "selection_error": selection_error,
    }
    manifest["manifest_hash"] = stable_payload_hash(manifest)
    _write_json(output_dir / "P0_GOVERNANCE_MANIFEST_v001.json", manifest)
    acquisition_manifest = output_dir / "P0_ACQUISITION_MANIFEST_v001.json"
    if gate == "GREEN":
        _write_json(
            acquisition_manifest,
            {
                "scope_id": manifest["scope_id"],
                "scope_kind": "P0",
                "description": f"Governed FG-00 P0 {manifest['manifest_hash']}",
                "artifact_types": manifest["artifact_types"],
                "artifact_ids": manifest["artifact_ids"],
                "companies": [
                    {
                        "symbol": row["historical_symbol"],
                        "outcome_label": "positive" if row["outcome_label"] == 1 else "negative",
                        "archetype": row["analysis_archetype"],
                    }
                    for row in manifest_companies
                ],
            },
        )
    q_counts: dict[str, Counter[str]] = {}
    for quarter in (Q4, Q1):
        quarter_rows = [row for row in registry if row["quarter"] == quarter]
        q_counts[quarter] = Counter(
            str(row["identity_resolution_state"]) for row in quarter_rows
        )
    report_status = (
        "Approved review inputs produced a queueable governed manifest.\n\n"
        if gate == "GREEN"
        else "Unapproved negatives remain UNKNOWN/NULL and the manifest is intentionally non-queueable.\n\n"
    )
    report = (
        "# P0 Governance Report v001\n\n"
        f"Production DB: `{database_path}` ({company_count} companies, {artifact_count} artifacts, migration `{migration}`).\n\n"
        "The source diagnosis is `PARTIAL_UNIVERSE_AVAILABLE`. Positive labels are frozen. "
        + report_status
        +
        f"- Q4 source rows: {sum(1 for row in registry if row['quarter'] == Q4)}\n"
        f"- Q1 source rows: {sum(1 for row in registry if row['quarter'] == Q1)}\n"
        f"- Targeted negative approval rows: {len(negative_pool)}\n"
        f"- Targeted archetype approval rows: {len(archetype_rows)}\n"
        f"- Approved negative rows: {sum(decision_approvals.values())}\n"
        f"- Approved archetype rows: {len(archetype_approvals)}\n"
        f"- Selected P0 companies: {len(manifest_companies) if gate == 'GREEN' else 0}\n"
        f"- Selection seed: {SELECTION_SEED}\n"
    )
    (output_dir / "P0_GOVERNANCE_REPORT_v001.md").write_text(report, encoding="utf-8")

    diagnosis = (
        "# FG-00 Universe Diagnosis\n\n"
        "Classification: **C. PARTIAL_UNIVERSE_AVAILABLE**.\n\n"
        "The 191 Q1 Focus rows and 239-row Q4 screenshot list are positive-label "
        "sources. Q1 also contains a broad Screener export and a derived 400-row "
        "rejected-candidate file, but neither a saved mentor eligibility contract "
        "nor owner approval proves those rejects were all legitimate candidates. "
        "No Q4 negative universe exists. Production exchange snapshots begin on "
        "2026-08-06, so they cannot reconstruct the mentor's earlier selection "
        "boundary. Therefore no negative label is assigned automatically.\n"
    )
    (output_dir / "FG00_UNIVERSE_DIAGNOSIS.md").write_text(diagnosis, encoding="utf-8")

    q4_resolved = sum(
        state in {IdentityResolutionState.RESOLVED.value, IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS.value}
        for state in (row["identity_resolution_state"] for row in registry if row["quarter"] == Q4)
    )
    q1_resolved = sum(
        state in {IdentityResolutionState.RESOLVED.value, IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS.value}
        for state in (row["identity_resolution_state"] for row in registry if row["quarter"] == Q1)
    )
    selected_for_report = manifest_companies if gate == "GREEN" else []
    label_counts = Counter(row["outcome_label"] for row in selected_for_report)
    quarter_counts = Counter(row["quarter"] for row in selected_for_report)
    archetype_counts = Counter(row["analysis_archetype"] for row in selected_for_report)
    remaining_decision_ids = sorted(
        int(row["company_id"])
        for row in negative_pool
        if decision_approvals.get(int(row["company_id"])) is not True
    )
    remaining_archetype_ids = sorted(
        int(row["company_id"])
        for row in archetype_rows
        if int(row["company_id"]) not in archetype_approvals
    )
    gate_report = (
        "# P0 Governance Gate\n\n"
        "## Label sources\n\n"
        f"Q4 source: 16 `Q4 FOCUS LIST` screenshots\ncount: {Q4_EXPECTED_POSITIVES}\nresolved: {q4_resolved}\nunresolved: {Q4_EXPECTED_POSITIVES - q4_resolved}\n\n"
        f"Q1 source: `stocks.csv` corroborated by `screener_focus_import.xlsx`\ncount: {Q1_EXPECTED_POSITIVES}\nresolved: {q1_resolved}\nunresolved: {Q1_EXPECTED_POSITIVES - q1_resolved}\n\n"
        "## Decision universe\n\nstate: PARTIAL\n\n"
        f"Q4: positive={q4_resolved}, negative=0, excluded_identity_unresolved={Q4_EXPECTED_POSITIVES - q4_resolved}, unknown=0, accounting_total={Q4_EXPECTED_POSITIVES}\n"
        f"Q1: positive={q1_resolved}, negative={sum(decision_approvals.values())}, excluded={Q1_EXPECTED_POSITIVES - q1_resolved + sum(value is False for value in decision_approvals.values())}, unknown={len(negative_pool) - len(decision_approvals)}, accounting_total={Q1_EXPECTED_POSITIVES + len(negative_pool)}\n\n"
        f"## Archetypes\n\napproved: {len(archetype_approvals)}\n"
        f"provisional/review/unknown: {len(archetype_rows) - len(archetype_approvals)} targeted rows\n"
        f"counts by approved archetype: {json.dumps(dict(sorted(archetype_counts.items())), sort_keys=True)}\n\n"
        f"## P0\n\ncompany count: {len(selected_for_report)}\npositive count: {label_counts.get(1, 0)}\nnegative count: {label_counts.get(0, 0)}\n"
        f"quarter counts: {json.dumps(dict(sorted(quarter_counts.items())), sort_keys=True)}\n"
        f"transcript artifacts: {artifact_type_coverage['transcripts'] if gate == 'GREEN' else 'not frozen'}\n"
        f"presentation artifacts: {artifact_type_coverage['presentations'] if gate == 'GREEN' else 'not frozen'}\n"
        f"rating artifacts: {artifact_type_coverage['credit_ratings'] if gate == 'GREEN' else 'not frozen'}\n\n"
        "## Governance invariants\n\n"
        + "\n".join(
            f"- {'PASS' if passed else 'FAIL'}: {name}" for name, passed in invariants.items()
        )
        + "\n\n## Remaining approvals\n\n"
        f"- Decision-universe membership: {remaining_decision_ids if gate != 'GREEN' else 'NONE'}.\n"
        f"- Archetypes: {remaining_archetype_ids if gate != 'GREEN' else 'NONE'}.\n\n"
        f"## Gate\n\n**P0_GOVERNANCE: {gate}**\n"
    )
    (output_dir / "P0_GOVERNANCE_GATE_REPORT.md").write_text(
        gate_report, encoding="utf-8"
    )
    return {
        "database": database_path,
        "companies": company_count,
        "document_artifacts": artifact_count,
        "migration": migration,
        "q4": {"source": Q4_EXPECTED_POSITIVES, "resolved": q4_resolved},
        "q1": {"source": Q1_EXPECTED_POSITIVES, "resolved": q1_resolved},
        "negative_approval_count": len(negative_pool),
        "archetype_approval_count": len(archetype_rows),
        "gate": gate,
        "output_dir": str(output_dir),
    }


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("/data/scraper.db"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/focus_ml/governance"),
    )
    parser.add_argument("--created-at-utc", default="2026-08-16T11:00:00Z")
    parser.add_argument("--parquet-only", action="store_true")
    parser.add_argument("--capture-q4-ocr", action="store_true")
    parser.add_argument("--q4-ocr-evidence", type=Path)
    parser.add_argument("--decision-approvals", type=Path)
    parser.add_argument("--archetype-approvals", type=Path)
    args = parser.parse_args()
    output = args.q4_ocr_evidence or args.output_dir
    assert_configured_writable_targets(output, db_path=args.db)
    if args.capture_q4_ocr:
        output = args.q4_ocr_evidence or args.output_dir / "Q4_OCR_EVIDENCE_v001.json"
        capture_q4_ocr_evidence(
            args.repository_root / "OLD/RESULTS/2026/Q4", output
        )
        print(json.dumps({"status": "ok", "output": str(output)}))
        return 0
    if args.parquet_only:
        outputs = write_parquet_exports(args.output_dir)
        print(json.dumps({"status": "ok", "outputs": [str(path) for path in outputs]}))
        return 0
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        payload = build_p0_governance(
            conn,
            repository_root=args.repository_root,
            output_dir=args.output_dir,
            created_at_utc=args.created_at_utc,
            q4_ocr_evidence_path=args.q4_ocr_evidence,
            decision_approval_path=args.decision_approvals,
            archetype_approval_path=args.archetype_approvals,
        )
    finally:
        conn.close()
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
