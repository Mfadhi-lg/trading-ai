"""Exact locators into D03 pages/elements/cells; fail closed on mismatches."""
from __future__ import annotations

import hashlib
import json
import re
from .numbers import normalize_value
from .context import UNIT


def canonical_hash(extraction):
    return hashlib.sha256(json.dumps(extraction, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def text_evidence(page, text, *, element=None, start=None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty evidence span")
    container = element["text_raw"] if element is not None else page["selected_text"]
    offset = container.find(text) if start is None else start
    if offset < 0 or container[offset:offset + len(text)] != text:
        raise ValueError("Evidence does not match canonical source text")
    return {"page": page["page_or_slide_number"], "kind": "element_text" if element else "page_text",
            "element": element["element_position"] if element else None,
            "start": offset, "end": offset + len(text), "quote": text,
            "text_origin": element["text_origin"] if element else page["selected_text_origin"],
            "bbox": element.get("bbox") if element else None}


def cell_evidence(page, element, row, column):
    value = element["rows"][row][column]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Empty table cell evidence")
    return {"page": page["page_or_slide_number"], "kind": "table_cell",
            "element": element["element_position"], "row": row, "column": column,
            "start": 0, "end": len(value), "quote": value,
            "text_origin": element["text_origin"], "bbox": _cell_bbox(element, row, column)}


def _cell_bbox(element, row, column):
    boxes = (element.get('reference') or {}).get('cell_bboxes') or []
    return (boxes[row][column] if row < len(boxes) and column < len(boxes[row]) else None) or element.get('bbox')


def element_evidence(page, element):
    return {"page": page["page_or_slide_number"], "kind": "element_reference",
            "element": element["element_position"], "reference": element.get("reference"),
            "bbox": element.get("bbox"), "quote": None, "text_origin": element["text_origin"]}


def validate_evidence(record, extraction):
    """Check exact offsets and source origin, not merely quote occurrence anywhere."""
    pages = {p["page_or_slide_number"]: p for p in extraction["pages"]}
    if not record.evidence:
        return record.status in {"UNKNOWN", "NOT_FOUND", "UNSUPPORTED"}
    try:
        for evidence in record.evidence:
            page = pages[evidence["page"]]
            element = next((e for e in page["elements"] if e["element_position"] == evidence.get("element")), None)
            kind = evidence["kind"]
            if kind == "page_text":
                text, origin = page["selected_text"], page["selected_text_origin"]
            elif kind == "element_text":
                text, origin = element["text_raw"], element["text_origin"]
            elif kind == "table_cell":
                text, origin = element["rows"][evidence["row"]][evidence["column"]], element["text_origin"]
                if evidence.get("bbox") != _cell_bbox(element, evidence["row"], evidence["column"]):
                    return False
            elif kind == "element_reference":
                if element.get("reference") != evidence.get("reference") or element.get("bbox") != evidence.get("bbox") or element['text_origin'] != evidence.get('text_origin'):
                    return False
                continue
            elif kind == "chart_cell":
                series = element["reference"]["embedded_series"][evidence["series"]]
                if (series["values"][evidence["category"]] != evidence["quote"]
                        or series["categories"][evidence["category"]] != evidence["period"]
                        or series["name"] != evidence["series_name"]
                        or element["reference"]["title_text"] != evidence["title"]
                        or evidence["text_origin"] != "native"):
                    return False
                continue
            else:
                return False
            if (not isinstance(evidence.get('quote'), str) or not evidence['quote'].strip()
                    or evidence["start"] < 0 or evidence["end"] <= evidence["start"]
                    or evidence["end"] > len(text)
                    or text[evidence["start"]:evidence["end"]] != evidence["quote"]
                    or origin != evidence["text_origin"]):
                return False
        if record.original_value and record.status == "VERIFIED":
            if not any(record.original_value in (e.get("quote") or "") for e in record.evidence):
                return False
            if isinstance(record.normalized_value, str) or (isinstance(record.normalized_value, dict) and set(record.normalized_value) == {"low", "high"}):
                if record.unit is not None:
                    parsed = normalize_value(record.original_value)
                    if parsed is None:
                        quotes = " ".join((e.get("quote") or "") + " " + (e.get("title") or "") for e in record.evidence)
                        hint = UNIT.search(quotes)
                        parsed = normalize_value(record.original_value, unit_hint=hint.group() if hint else None)
                    if parsed is None or any(parsed[k] != getattr(record, k) for k in ("normalized_value", "currency", "unit", "scale")):
                        return False
        return True
    except (KeyError, TypeError, StopIteration, IndexError):
        return False


def reconcile(records):
    """Keep contradictions and duplicates visible; never sum repeated disclosures."""
    seen = {}
    groups = {}
    for record in records:
        record.seal()
        duplicate_key = (record.source["content_sha256"], record.metric, record.statement_kind,
                         record.reporting_period, record.time_horizon,
                         json.dumps(record.dimensions, sort_keys=True), record.original_value,
                         tuple(e.get("quote") for e in record.evidence))
        if duplicate_key in seen:
            prior = seen[duplicate_key]
            if prior.record_id == record.record_id:
                continue
            record.reasons.append("duplicate_disclosure")
            record.related_records.append(prior.record_id)
        else:
            seen[duplicate_key] = record
        key = (record.source["document_id"], record.source["version"], record.metric,
               record.statement_kind, record.reporting_period, record.time_horizon,
               json.dumps({k:v for k,v in record.dimensions.items() if k != "financial_relationship"}, sort_keys=True), record.currency, record.unit)
        if record.status == "VERIFIED" and record.normalized_value is not None:
            groups.setdefault(key, []).append(record)
    for group in groups.values():
        if len({json.dumps(r.normalized_value, sort_keys=True) for r in group}) > 1:
            for r in group:
                r.status = "AMBIGUOUS"
                r.reasons.append("contradictory_same_scope_values")
                r.related_records.extend(x.record_id for x in group if x is not r)
    unique = {}
    for r in records:
        unique.setdefault(r.record_id, r)
    return list(unique.values())
