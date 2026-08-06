"""Deterministic, page-evidenced normalization of credit-rating reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


CREDIT_RATING_PARSER_NAME = "credit-rating-normalizer"
CREDIT_RATING_PARSER_VERSION = "1.0.0"
CREDIT_RATING_OPTIONS = {
    "amount_rule": "header_declared_inr_crore_v1",
    "classification_rule": "document_phrase_priority_v1",
    "conflict_key": "facility_action_date_v1",
    "table_header_rule": "four_required_columns_v1",
}

_AGENCIES = (
    ("CARE Ratings Limited", "care"),
    ("CARE Ratings", "care"),
    ("CRISIL Ratings Limited", "crisil"),
    ("CRISIL Ratings", "crisil"),
    ("ICRA Limited", "icra"),
    ("India Ratings and Research Private Limited", "india_ratings"),
    ("India Ratings", "india_ratings"),
    ("Acuité Ratings & Research Limited", "acuite"),
    ("Acuité Ratings", "acuite"),
)

_MONTH_DATE = re.compile(
    r"\b(?:date|date of (?:press release|rating action))\s*:\s*"
    r"((?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},\s+\d{4})\b",
    re.IGNORECASE,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


@dataclass(frozen=True)
class RatingEvidence:
    field_code: str
    page_number: int
    source_value_raw: str
    text_origin: str
    evidence_kind: str
    element_position: int | None = None
    source_row_position: int | None = None
    source_column_position: int | None = None
    bbox: list[float] | None = None


@dataclass(frozen=True)
class RatingSourceTable:
    page_number: int
    element_position: int
    rows: tuple[tuple[str, ...], ...]
    header_mapping: dict[str, int]
    status: str
    review_reasons: tuple[str, ...] = ()
    bbox: list[float] | None = None


@dataclass(frozen=True)
class RatingPassage:
    page_number: int
    passage_position: int
    element_position: int
    passage_kind: str
    text_raw: str
    text_origin: str
    bbox: list[float] | None = None


@dataclass(frozen=True)
class NormalizedRatingAction:
    source_table_index: int
    source_row_position: int
    facility_or_instrument_raw: str
    facility_or_instrument_code: str | None
    amount_display: str | None
    amount_decimal: str | None
    currency: str | None
    unit_code: str | None
    scale_multiplier: str | None
    rating_raw: str
    rating_code: str | None
    outlook_raw: str | None
    outlook_code: str | None
    rating_action_raw: str
    rating_action_code: str | None
    action_date_raw: str | None
    action_date: str | None
    canonical_status: str
    source_row: tuple[str, ...]
    ambiguity_reasons: tuple[str, ...] = ()
    evidence: tuple[RatingEvidence, ...] = ()


@dataclass(frozen=True)
class CreditRatingNormalization:
    document_class: str
    agency_raw: str | None
    agency_code: str | None
    action_date_raw: str | None
    action_date: str | None
    source_tables: tuple[RatingSourceTable, ...]
    passages: tuple[RatingPassage, ...]
    actions: tuple[NormalizedRatingAction, ...]
    document_evidence: tuple[RatingEvidence, ...]
    validations: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        if any(
            row["severity"] == "error" and not row["passed"]
            for row in self.validations
        ):
            return "failed"
        if any(
            row["severity"] == "manual_review" and not row["passed"]
            for row in self.validations
        ):
            return "manual_review"
        return "accepted"


def _text_block_evidence(
    page: dict[str, Any], field_code: str, needle: str, source_value: str
) -> RatingEvidence:
    needle_lower = needle.lower()
    for element in page["elements"]:
        text = _compact(element.get("text_raw"))
        if element.get("element_kind") == "text_block" and needle_lower in text.lower():
            return RatingEvidence(
                field_code=field_code,
                page_number=int(page["page_or_slide_number"]),
                source_value_raw=source_value,
                text_origin=element["text_origin"],
                evidence_kind="page_text",
                element_position=int(element["element_position"]),
                bbox=element.get("bbox"),
            )
    return RatingEvidence(
        field_code=field_code,
        page_number=int(page["page_or_slide_number"]),
        source_value_raw=source_value,
        text_origin=page["selected_text_origin"],
        evidence_kind="page_text",
    )


def _classify_document(pages: list[dict[str, Any]]) -> tuple[str, RatingEvidence | None]:
    priorities = (
        ("rating rationale", "rating_rationale"),
        ("rating letter", "rating_letter"),
        ("surveillance", "surveillance"),
        ("reaffirmation", "reaffirmation"),
    )
    for phrase, code in priorities:
        for page in pages:
            match = re.search(re.escape(phrase), page["selected_text"], re.IGNORECASE)
            if match:
                displayed = match.group(0)
                return code, _text_block_evidence(
                    page, "document_class", displayed, displayed
                )
    return "unknown", None


def _identify_agency(
    pages: list[dict[str, Any]],
) -> tuple[str | None, str | None, RatingEvidence | None]:
    for agency_raw, agency_code in _AGENCIES:
        for page in pages:
            match = re.search(re.escape(agency_raw), page["selected_text"], re.IGNORECASE)
            if match:
                displayed = match.group(0)
                return (
                    displayed,
                    agency_code,
                    _text_block_evidence(page, "agency", displayed, displayed),
                )
    return None, None, None


def _identify_action_date(
    pages: list[dict[str, Any]],
) -> tuple[str | None, str | None, RatingEvidence | None]:
    for page in pages:
        match = _MONTH_DATE.search(page["selected_text"])
        if not match:
            continue
        raw = match.group(1)
        parsed = datetime.strptime(raw.title(), "%B %d, %Y").date().isoformat()
        return raw, parsed, _text_block_evidence(page, "action_date", raw, raw)
    return None, None, None


def _header_field(value: str) -> str | None:
    normalized = _token(value)
    if "action" in normalized and "rating" in normalized:
        return "rating_action"
    if "facilit" in normalized or "instrument" in normalized:
        return "facility_or_instrument"
    if "amount" in normalized:
        return "amount"
    if "rating" in normalized:
        return "rating"
    return None


def _amount_unit(header: str) -> tuple[str, str, str] | None:
    normalized = _token(header)
    if not any(token in normalized.split("_") for token in ("rs", "inr")) and "₹" not in header:
        return None
    for token, unit_code, scale in (
        ("crore", "INR_CRORE", "10000000"),
        ("lakh", "INR_LAKH", "100000"),
        ("lac", "INR_LAKH", "100000"),
        ("million", "INR_MILLION", "1000000"),
        ("billion", "INR_BILLION", "1000000000"),
    ):
        if token in normalized.split("_"):
            return "INR", unit_code, scale
    return None


def _map_table(
    page_number: int, element: dict[str, Any]
) -> RatingSourceTable:
    rows = tuple(tuple(_compact(cell) for cell in row) for row in (element["rows"] or []))
    if not rows:
        return RatingSourceTable(
            page_number, int(element["element_position"]), rows, {}, "unrelated",
            bbox=element.get("bbox"),
        )
    mapped: dict[str, list[int]] = {}
    for position, header in enumerate(rows[0]):
        field = _header_field(header)
        if field:
            mapped.setdefault(field, []).append(position)
    required = {"facility_or_instrument", "amount", "rating", "rating_action"}
    if len(required.intersection(mapped)) < 2:
        return RatingSourceTable(
            page_number, int(element["element_position"]), rows, {}, "unrelated",
            bbox=element.get("bbox"),
        )
    reasons: list[str] = []
    missing = sorted(required.difference(mapped))
    duplicates = sorted(field for field, columns in mapped.items() if len(columns) != 1)
    if missing:
        reasons.append("missing_headers:" + ",".join(missing))
    if duplicates:
        reasons.append("ambiguous_headers:" + ",".join(duplicates))
    mapping = {field: columns[0] for field, columns in mapped.items() if len(columns) == 1}
    if "amount" in mapping and _amount_unit(rows[0][mapping["amount"]]) is None:
        reasons.append("amount_unit_unresolved")
    return RatingSourceTable(
        page_number=page_number,
        element_position=int(element["element_position"]),
        rows=rows,
        header_mapping=mapping,
        status="ambiguous" if reasons else "normalized",
        review_reasons=tuple(reasons),
        bbox=element.get("bbox"),
    )


def _passage_kind(text: str, document_class: str, agency_raw: str | None) -> str:
    lowered = text.lower()
    if "previous rating" in lowered:
        return "rating_history"
    if (
        "rating assigned" in lowered
        or "rating reaffirmed" in lowered
        or ("rating" in lowered and "outlook" in lowered)
        or "rating strengths" in lowered
        or "rating constraints" in lowered
        or "key rating drivers" in lowered
    ):
        return "rationale"
    if agency_raw and text.casefold() == agency_raw.casefold():
        return "agency_header"
    if document_class != "unknown" and _token(text) == document_class:
        return "document_heading"
    if _MONTH_DATE.search(text):
        return "action_date"
    return "context"


def _extract_passages(
    pages: list[dict[str, Any]],
    tables: list[RatingSourceTable],
    document_class: str,
    agency_raw: str | None,
) -> tuple[RatingPassage, ...]:
    table_lines = {
        _compact(" ".join(row))
        for table in tables
        for row in table.rows
        if any(row)
    }
    passages: list[RatingPassage] = []
    for page in pages:
        for element in page["elements"]:
            if element["element_kind"] != "text_block":
                continue
            text = _compact(element.get("text_raw"))
            if not text or text in table_lines:
                continue
            passages.append(
                RatingPassage(
                    page_number=int(page["page_or_slide_number"]),
                    passage_position=len(passages),
                    element_position=int(element["element_position"]),
                    passage_kind=_passage_kind(text, document_class, agency_raw),
                    text_raw=text,
                    text_origin=element["text_origin"],
                    bbox=element.get("bbox"),
                )
            )
    return tuple(passages)


def _decimal_string(value: str) -> str | None:
    compact = value.replace(",", "").strip()
    if not compact:
        return None
    try:
        parsed = Decimal(compact)
    except InvalidOperation:
        return None
    return format(parsed, "f")


def _split_rating(value: str) -> tuple[str | None, str | None, str | None]:
    parts = [_compact(part) for part in value.split(";", 1)]
    rating_code = parts[0] or None
    outlook_raw = parts[1] if len(parts) > 1 and parts[1] else None
    outlook_code = _token(outlook_raw) if outlook_raw else None
    return rating_code, outlook_raw, outlook_code


def _action_code(value: str) -> str | None:
    normalized = value.lower()
    for needle, code in (
        ("reaffirm", "reaffirmed"),
        ("upgrade", "upgraded"),
        ("downgrade", "downgraded"),
        ("assign", "assigned"),
        ("withdraw", "withdrawn"),
        ("revise", "revised"),
    ):
        if needle in normalized:
            return code
    return None


def _table_cell_evidence(
    *,
    table: RatingSourceTable,
    field_code: str,
    row_position: int,
    column_position: int,
    source_value: str,
    text_origin: str,
    bbox: list[float] | None,
) -> RatingEvidence:
    return RatingEvidence(
        field_code=field_code,
        page_number=table.page_number,
        source_value_raw=source_value,
        text_origin=text_origin,
        evidence_kind="table_cell",
        element_position=table.element_position,
        source_row_position=row_position,
        source_column_position=column_position,
        bbox=bbox,
    )


def _normalize_table_actions(
    *,
    table_index: int,
    table: RatingSourceTable,
    element: dict[str, Any],
    action_date_raw: str | None,
    action_date: str | None,
    action_date_evidence: RatingEvidence | None,
) -> list[NormalizedRatingAction]:
    if table.status != "normalized":
        return []
    output: list[NormalizedRatingAction] = []
    mapping = table.header_mapping
    amount_unit = _amount_unit(table.rows[0][mapping["amount"]])
    if amount_unit is None:
        return []
    currency, unit_code, scale_multiplier = amount_unit
    for row_position, row in enumerate(table.rows[1:], 1):
        if not any(row):
            continue
        facility = row[mapping["facility_or_instrument"]]
        amount = row[mapping["amount"]]
        rating = row[mapping["rating"]]
        action = row[mapping["rating_action"]]
        reasons: list[str] = []
        amount_decimal = _decimal_string(amount)
        rating_code, outlook_raw, outlook_code = _split_rating(rating)
        action_code = _action_code(action)
        if not facility:
            reasons.append("missing_facility_or_instrument")
        if amount_decimal is None:
            reasons.append("missing_or_invalid_amount")
        if not rating_code:
            reasons.append("missing_rating")
        if action_code is None:
            reasons.append("unknown_rating_action")
        if action_date is None:
            reasons.append("missing_action_date")
        bbox = element.get("bbox")
        evidence = [
            _table_cell_evidence(
                table=table,
                field_code=field_code,
                row_position=row_position,
                column_position=mapping[mapping_key],
                source_value=value,
                text_origin=element["text_origin"],
                bbox=bbox,
            )
            for field_code, mapping_key, value in (
                ("facility_or_instrument", "facility_or_instrument", facility),
                ("amount", "amount", amount),
                ("rating", "rating", rating),
                ("rating_action", "rating_action", action),
            )
        ]
        if outlook_raw:
            evidence.append(
                _table_cell_evidence(
                    table=table,
                    field_code="outlook",
                    row_position=row_position,
                    column_position=mapping["rating"],
                    source_value=outlook_raw,
                    text_origin=element["text_origin"],
                    bbox=bbox,
                )
            )
        if action_date_evidence:
            evidence.append(action_date_evidence)
        output.append(
            NormalizedRatingAction(
                source_table_index=table_index,
                source_row_position=row_position,
                facility_or_instrument_raw=facility,
                facility_or_instrument_code=_token(facility) if facility else None,
                amount_display=amount or None,
                amount_decimal=amount_decimal,
                currency=currency if amount else None,
                unit_code=unit_code if amount else None,
                scale_multiplier=scale_multiplier if amount else None,
                rating_raw=rating,
                rating_code=rating_code,
                outlook_raw=outlook_raw,
                outlook_code=outlook_code,
                rating_action_raw=action,
                rating_action_code=action_code,
                action_date_raw=action_date_raw,
                action_date=action_date,
                canonical_status="review" if reasons else "accepted",
                source_row=row,
                ambiguity_reasons=tuple(reasons),
                evidence=tuple(evidence),
            )
        )
    return output


def _mark_conflicts(
    actions: list[NormalizedRatingAction],
) -> list[NormalizedRatingAction]:
    groups: dict[tuple[str | None, str | None], list[int]] = {}
    for index, action in enumerate(actions):
        groups.setdefault(
            (action.facility_or_instrument_code, action.action_date), []
        ).append(index)
    output = list(actions)
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        values = {
            (
                actions[index].amount_decimal,
                actions[index].rating_code,
                actions[index].outlook_code,
                actions[index].rating_action_code,
            )
            for index in indexes
        }
        reason = "conflicting_action_rows" if len(values) > 1 else "duplicate_action_rows"
        status = "conflict" if len(values) > 1 else "review"
        for index in indexes:
            action = output[index]
            output[index] = replace(
                action,
                canonical_status=status,
                ambiguity_reasons=tuple((*action.ambiguity_reasons, reason)),
            )
    return output


def _validations(
    *,
    document_class: str,
    agency_code: str | None,
    action_date: str | None,
    tables: list[RatingSourceTable],
    actions: list[NormalizedRatingAction],
    document_evidence: list[RatingEvidence],
    passages: tuple[RatingPassage, ...],
) -> tuple[dict[str, Any], ...]:
    rating_tables = [table for table in tables if table.status != "unrelated"]
    ambiguous_tables = [
        {"page": table.page_number, "element": table.element_position, "reasons": table.review_reasons}
        for table in rating_tables
        if table.status == "ambiguous"
    ]
    conflict_rows = [
        {"table_index": action.source_table_index, "row": action.source_row_position}
        for action in actions
        if action.canonical_status == "conflict"
    ]
    review_rows = [
        {"table_index": action.source_table_index, "row": action.source_row_position,
         "reasons": action.ambiguity_reasons}
        for action in actions
        if action.canonical_status != "accepted"
    ]
    expected_document_fields = set()
    if document_class != "unknown":
        expected_document_fields.add("document_class")
    if agency_code is not None:
        expected_document_fields.add("agency")
    if action_date is not None:
        expected_document_fields.add("action_date")
    document_fields = {evidence.field_code for evidence in document_evidence}
    evidence_complete = expected_document_fields.issubset(document_fields) and all(
        ({"facility_or_instrument", "amount", "rating", "rating_action"}
         | ({"action_date"} if action.action_date is not None else set()))
        .issubset({evidence.field_code for evidence in action.evidence})
        and (action.outlook_raw is None or "outlook" in {e.field_code for e in action.evidence})
        for action in actions
    )
    return (
        {"rule_code": "credit_rating_document_class_known", "severity": "manual_review",
         "passed": document_class != "unknown", "expected": "known", "actual": document_class,
         "details": {}},
        {"rule_code": "credit_rating_agency_identified", "severity": "manual_review",
         "passed": agency_code is not None, "expected": "known agency", "actual": agency_code,
         "details": {}},
        {"rule_code": "credit_rating_action_date_present", "severity": "manual_review",
         "passed": action_date is not None, "expected": "YYYY-MM-DD", "actual": action_date,
         "details": {}},
        {"rule_code": "credit_rating_tables_present", "severity": "error",
         "passed": bool(rating_tables), "expected": ">=1", "actual": len(rating_tables),
         "details": {}},
        {"rule_code": "credit_rating_tables_unambiguous", "severity": "manual_review",
         "passed": not ambiguous_tables, "expected": [], "actual": ambiguous_tables,
         "details": {}},
        {"rule_code": "credit_rating_actions_present", "severity": "manual_review",
         "passed": bool(actions), "expected": ">=1", "actual": len(actions), "details": {}},
        {"rule_code": "credit_rating_actions_review_free", "severity": "manual_review",
         "passed": not review_rows, "expected": [], "actual": review_rows, "details": {}},
        {"rule_code": "credit_rating_conflicts_absent", "severity": "manual_review",
         "passed": not conflict_rows, "expected": [], "actual": conflict_rows, "details": {}},
        {"rule_code": "credit_rating_field_evidence_complete", "severity": "error",
         "passed": evidence_complete, "expected": True, "actual": evidence_complete,
         "details": {}},
        {"rule_code": "credit_rating_rationale_passages_present", "severity": "manual_review",
         "passed": document_class not in {"rating_rationale", "surveillance", "reaffirmation"}
         or any(passage.passage_kind == "rationale" for passage in passages),
         "expected": ">=1 for rationale/surveillance/reaffirmation", 
         "actual": sum(passage.passage_kind == "rationale" for passage in passages),
         "details": {}},
    )


def normalize_credit_rating(extraction: dict[str, Any]) -> CreditRatingNormalization:
    """Normalize one wholly usable D03 extraction; never consult live bytes."""

    if extraction.get("extraction_status") != "ok":
        raise ValueError("Credit-rating normalization requires an ok extraction")
    pages = list(extraction.get("pages") or ())
    if not pages or any(page.get("page_kind") != "page" for page in pages):
        raise ValueError("Credit-rating normalization requires ordered PDF pages")
    document_class, class_evidence = _classify_document(pages)
    agency_raw, agency_code, agency_evidence = _identify_agency(pages)
    action_date_raw, action_date, date_evidence = _identify_action_date(pages)
    document_evidence = [
        evidence
        for evidence in (class_evidence, agency_evidence, date_evidence)
        if evidence is not None
    ]
    tables: list[RatingSourceTable] = []
    actions: list[NormalizedRatingAction] = []
    for page in pages:
        for element in page["elements"]:
            if element["element_kind"] != "table":
                continue
            table = _map_table(int(page["page_or_slide_number"]), element)
            tables.append(table)
            actions.extend(
                _normalize_table_actions(
                    table_index=len(tables) - 1,
                    table=table,
                    element=element,
                    action_date_raw=action_date_raw,
                    action_date=action_date,
                    action_date_evidence=date_evidence,
                )
            )
    actions = _mark_conflicts(actions)
    passages = _extract_passages(
        pages, tables, document_class=document_class, agency_raw=agency_raw
    )
    validations = _validations(
        document_class=document_class,
        agency_code=agency_code,
        action_date=action_date,
        tables=tables,
        actions=actions,
        document_evidence=document_evidence,
        passages=passages,
    )
    return CreditRatingNormalization(
        document_class=document_class,
        agency_raw=agency_raw,
        agency_code=agency_code,
        action_date_raw=action_date_raw,
        action_date=action_date,
        source_tables=tuple(tables),
        passages=passages,
        actions=tuple(actions),
        document_evidence=tuple(document_evidence),
        validations=validations,
    )
