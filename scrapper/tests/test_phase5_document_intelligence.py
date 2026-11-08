"""Offline contracts: precise evidence and conservative research interpretation."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import socket
import sqlite3
from unittest.mock import patch

import pytest

from scrapper.documents.extraction import DocumentElement, DocumentPage, ExtractedDocument
from scrapper.research.document_intelligence import DocumentContext, extract_intelligence, compare_guidance, eligible_at
from scrapper.research.document_intelligence.evidence import validate_evidence, reconcile
from scrapper.research.document_intelligence.numbers import normalize_value
from scrapper.research.document_intelligence.offline import extract_local, run, deny_network

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
INPUTS = Path(__file__).resolve().parents[2] / "artifacts/phase5/offline-demo/inputs"
AT = "2026-08-17T12:00:00Z"


def canonical(text, *, elements=None, status="ok", origin="native"):
    if elements is None:
        elements = tuple(DocumentElement(i, "text_block", origin, text_raw=line) for i, line in enumerate(text.splitlines()))
    page = DocumentPage(1, "page", text if origin == "native" else "", text if origin == "ocr" else "",
                        text, origin, origin == "native", None, None, elements=elements)
    result = ExtractedDocument("application/pdf", (page,)).to_dict()
    result["extraction_status"] = status
    return result


def context(text="fixture", kind="presentation", **kwargs):
    kwargs.setdefault("company_id", 42)
    kwargs.setdefault("captured_at", AT)
    kwargs.setdefault("publication_at", "2026-08-16T10:00:00Z")
    return DocumentContext("fixture:42", hashlib.sha256(text.encode()).hexdigest(), kind, **kwargs)


def extracted(text, kind="presentation", **kwargs):
    return extract_intelligence(context(text, kind, **kwargs), canonical(text))[0]


def records(rows, metric, status=None):
    return [r for r in rows if r.metric == metric and (status is None or r.status == status)]


@pytest.mark.parametrize("raw,value,currency,unit,scale", [
    ("₹240 billion", "240000000000", "INR", "currency", "1000000000"),
    ("INR808 crores", "8080000000", "INR", "currency", "10000000"),
    ("Rs. 1,23,456.75 crore", "1234567500000.00", "INR", "currency", "10000000"),
    ("USD2.5 million", "2500000.0", "USD", "currency", "1000000"),
    ("10.8%", "10.8", None, "percent", "1"),
    ("36.87x", "36.87", None, "ratio", "1"),
    ("-25 bps", "-25", None, "basis_points", "1"),
    ("200 MW", "200", None, "MW", "1"),
])
def test_decimal_units(raw, value, currency, unit, scale):
    assert normalize_value(raw) == dict(normalized_value=value, currency=currency, unit=unit, scale=scale, approximate=False)


def test_range_approximate_and_missing_units():
    assert normalize_value("18% to 20%")["normalized_value"] == {"low": "18", "high": "20"}
    assert normalize_value("around 17%-18%")["approximate"]
    assert normalize_value("20 to 18%") is None
    assert normalize_value("808") is None
    assert normalize_value("(120)", unit_hint="INR crore")["normalized_value"] == "-1200000000"


def test_guidance_actual_targets_risks_questions():
    text = ("Official Earnings Call Transcript\nPrepared remarks\n"
            "Management: Revenue was INR100 crore for FY26.\n"
            "Management: We expect revenue growth of 18% to 20% for FY27.\n"
            "Management: We target operating margin of 12% for FY27.\n"
            "Management: Project delays may affect execution.\n"
            "Q&A\nAnalyst: Do you expect revenue of INR200 crore for FY27?\nEnd of transcript")
    rows = extracted(text, "transcript")
    assert records(rows, "revenue", "VERIFIED")[0].value_basis == "ACTUAL"
    guidance = records(rows, "revenue_growth", "VERIFIED")[0]
    assert guidance.statement_kind == "GUIDANCE" and guidance.time_horizon == "FY27"
    assert guidance.normalized_value == {"low": "18", "high": "20"}
    assert records(rows, "operating_margin", "VERIFIED")[0].statement_kind == "TARGET"
    assert records(rows, "project_delay")[0].statement_kind == "RISK_WARNING"
    question = next(r for r in rows if r.statement_kind == "ANALYST_QUESTION" and r.metric == "revenue")
    assert question.normalized_value is None
    assert not eligible_at(question, "2026-09-01T00:00:00Z")


def test_real_order_book_is_not_revenue_and_roster_is_evidenced():
    row = next(r for r in json.loads((INPUTS / "manifest.json").read_text())["documents"] if r["document_type"] == "transcript")
    payload = json.loads((INPUTS / row["canonical_path"]).read_text())["extraction"]
    ctx = context("real-test", "transcript")
    rows, _ = extract_intelligence(ctx, payload)
    order = next(r for r in records(rows, "order_book", "VERIFIED") if r.original_value == "INR808 crores")
    assert order.normalized_value == "8080000000"
    assert order.reporting_period is None
    assert order.as_of_date == "2026-03-31"
    assert order.speaker["name"] == "Murari Lal Poddar"
    assert order.speaker["title"] == "CHIEF FINANCIAL OFFICER"
    assert order.section == "prepared_remarks"
    assert any("MURARI LAL" in (e.get("quote") or "") for e in order.evidence)
    assert not any(r.metric == "revenue" and r.original_value == "INR808 crores" for r in rows)
    long_term = records(rows, "growth_guidance", "VERIFIED")[0]
    assert long_term.normalized_value == {"low": "18", "high": "20"}
    assert long_term.time_horizon == "LONG TERM"
    assert all(validate_evidence(r, payload) for r in rows)


def test_footnote_labels_and_multicolumn_ambiguity():
    rows = extracted("Our net order book1 grows to ₹240 billion")
    assert records(rows, "order_book", "VERIFIED")[0].normalized_value == "240000000000"
    rows = extracted("Improvement in EBITDA margin by ~2.8% PAT margin is higher by ~1.8% (~2.2% in")
    assert not any(r.status == "VERIFIED" and r.original_value == "~2.8%" and r.metric == "profit_margin" for r in rows)


def test_pricing_growth_is_not_volume_growth():
    rows = extracted("The composition of volume versus price increase, 8% growth is attributable to material price inflation.")
    assert not records(rows, "volume", "VERIFIED")


def test_delta_and_level_are_separate_and_unknown_units_are_rejected():
    rows = extracted("Operating profit increased by 23% to INR130 crore for FY26.")
    assert records(rows, "operating_profit", "VERIFIED")[0].dimensions["measurement_kind"] == "level"
    rows = extracted("PAT margin is higher by 1.6% for FY26.")
    assert records(rows, "profit_margin", "VERIFIED")[0].dimensions["measurement_kind"] == "change"
    table = DocumentElement(0, "table", "native", rows=(("Metric", "FY26"), ("Order book", "18%")))
    rows, _ = extract_intelligence(context(), canonical("Table", elements=(table,)))
    assert not records(rows, "order_book", "VERIFIED")


def test_guidance_range_bounds_and_incomparable_horizons():
    old = guidance(18, publication="2026-06-01T00:00:00Z", capture="2026-06-02T00:00:00Z", version_text="old")
    new = guidance(20, publication="2026-07-01T00:00:00Z", capture="2026-07-02T00:00:00Z", version_text="new")
    old.normalized_value = {"low": "18", "high": "22"}
    new.normalized_value = {"low": "19", "high": "21"}
    assert compare_guidance(old, new, decision_at="2026-08-01T00:00:00Z")["status"] == "AMBIGUOUS"
    new.time_horizon = "FY28"
    assert compare_guidance(old, new, decision_at="2026-08-01T00:00:00Z")["change"] == "UNKNOWN"


def test_future_publication_overrides_early_capture():
    r = guidance(18, publication="2026-09-01T00:00:00Z", capture="2026-06-02T00:00:00Z", version_text="future")
    assert not eligible_at(r, "2026-08-01T00:00:00Z")


def test_existing_html_rating_parser_is_reused_offline():
    body = b"<html><body><h1>CARE Ratings Limited</h1><p>Liquidity: Strong</p></body></html>"
    data = extract_local(body, ".html")
    rows, _ = extract_intelligence(context(kind="credit_rating"), data)
    assert records(rows, "liquidity", "EVIDENCED_STATEMENT")


def test_native_table_period_units_and_missing_header():
    table = DocumentElement(0, "table", "native", rows=(("Metric", "FY26 INR crore", "FY27 guidance INR crore"), ("Order book", "808", "900")))
    data = canonical("Order book table", elements=(table,))
    rows, _ = extract_intelligence(context(), data)
    found = records(rows, "order_book", "VERIFIED")
    assert [r.normalized_value for r in found] == ["8080000000", "9000000000"]
    assert found[0].reporting_period == "FY26" and found[0].value_basis == "ACTUAL"
    assert found[1].time_horizon == "FY27" and found[1].value_basis == "PROJECTED"
    assert all(validate_evidence(r, data) for r in rows)
    missing = deepcopy(data)
    missing["pages"][0]["elements"][0]["rows"][0][1] = "Results"
    rows, _ = extract_intelligence(context(), missing)
    assert records(rows, "order_book", "AMBIGUOUS")


@pytest.mark.parametrize("action,code,changed", [("Upgraded", "upgraded", True), ("Downgraded", "downgraded", True), ("Reaffirmed", "reaffirmed", False), ("Assigned", "assigned", False)])
def test_rating_events_require_explicit_action(action, code, changed):
    table = DocumentElement(3, "table", "native", rows=(("Facilities/Instruments", "Amount (Rs. crore)", "Rating", "Rating Action"), ("Long Term Bank Facilities", "100", "CARE A; Stable", action)))
    text = "CARE Ratings Limited\nRating Rationale\nDate of rating action: July 15, 2026"
    elements = tuple(DocumentElement(i, "text_block", "native", text_raw=t) for i, t in enumerate(text.splitlines())) + (table,)
    rows, _ = extract_intelligence(context(kind="credit_rating"), canonical(text, elements=elements))
    r = records(rows, "rating_action", "VERIFIED")[0]
    assert r.normalized_value["action"] == code
    assert r.dimensions["is_grade_change"] is changed
    assert r.effective_date == "2026-07-15"
    assert "rating_as_of_observation_not_current_status" in r.reasons


def test_rating_missing_effective_date_and_combined_grades():
    row = next(r for r in json.loads((INPUTS / "manifest.json").read_text())["documents"] if r["document_type"] == "credit_rating")
    data = json.loads((INPUTS / row["canonical_path"]).read_text())["extraction"]
    rows, _ = extract_intelligence(context(kind="credit_rating"), data)
    actions = records(rows, "rating_action")
    assert actions and all(r.status == "VERIFIED" for r in actions)
    assert all(r.effective_date is None for r in actions)
    combined = [r for r in actions if '/' in r.normalized_value['facility']]
    assert {r.normalized_value['grade'] for r in combined} == {'CARE A', 'CARE A1'}
    assert {r.dimensions['rating_tenor'] for r in combined} == {'LONG_TERM', 'SHORT_TERM'}
    assert all('effective_rating_date_unknown' in r.reasons for r in actions)
    assert all(not r.dimensions["is_grade_change"] for r in actions)


@pytest.mark.parametrize("action,event,status", [("Outlook revised to Positive", "OUTLOOK_REVISION", "VERIFIED"), ("Revised", "UNKNOWN", "AMBIGUOUS"), ("Reaffirmed and Upgraded", "UNKNOWN", "AMBIGUOUS")])
def test_outlook_revision_and_mixed_actions(action, event, status):
    table = DocumentElement(3, "table", "native", rows=(("Facilities/Instruments", "Amount (Rs. crore)", "Rating", "Rating Action"), ("Long Term Bank Facilities", "100", "CARE A; Positive", action)))
    text = "CARE Ratings Limited\nRating Rationale\nDate of rating action: July 15, 2026"
    elements = tuple(DocumentElement(i, "text_block", "native", text_raw=t) for i,t in enumerate(text.splitlines())) + (table,)
    rows, _ = extract_intelligence(context(kind="credit_rating"), canonical(text, elements=elements))
    r = records(rows, "rating_action")[0]
    assert r.status == status and r.dimensions["event_type"] == event


def test_exact_evidence_rejects_tampering():
    text = "Order book was INR808 crore for FY26."
    data = canonical(text)
    r = records(extracted(text), "order_book", "VERIFIED")[0]
    assert validate_evidence(r, data)
    tampered = deepcopy(r)
    tampered.evidence[0]["page"] = 99
    assert not validate_evidence(tampered, data)
    tampered = deepcopy(r)
    tampered.evidence[0]["start"] += 1
    assert not validate_evidence(tampered, data)
    tampered = deepcopy(r)
    tampered.original_value = "INR999 crore"
    assert not validate_evidence(tampered, data)
    tampered = deepcopy(r)
    tampered.normalized_value = "9990000000"
    assert not validate_evidence(tampered, data)


def test_unknown_not_found_ambiguous_unsupported_and_ocr():
    rows = extracted("No financial figures disclosed.")
    assert records(rows, "order_book")[0].status == "NOT_FOUND"
    rows = extracted("Order book was INR100 crore or INR120 crore.")
    assert records(rows, "order_book")[0].status == "AMBIGUOUS"
    data = canonical("Order book was INR100 crore.", status="partial")
    rows, _ = extract_intelligence(context(), data)
    assert records(rows, "capex")[0].status == "UNKNOWN"
    chart = DocumentElement(0, "chart", reference={"chart_type": "line"})
    rows, _ = extract_intelligence(context(), canonical("Chart", elements=(chart,)))
    assert records(rows, "visual_content")[0].status == "UNSUPPORTED"
    data = canonical("Order book was INR100 crore.", origin="ocr")
    rows, _ = extract_intelligence(context(), data)
    assert records(rows, "order_book")[0].status == "AMBIGUOUS"


def guidance(value, *, publication, capture, version_text):
    text = f"Official Transcript\nPrepared remarks\nManagement: We expect revenue growth of {value}% for FY27.\nEnd of transcript"
    ctx = context(version_text, "transcript", publication_at=publication, captured_at=capture)
    return records(extract_intelligence(ctx, canonical(text))[0], "revenue_growth", "VERIFIED")[0]


@pytest.mark.parametrize("value,change", [(22,"INCREASED"), (15,"DECREASED"), (18,"UNCHANGED")])
def test_guidance_comparison_is_two_sided(value, change):
    old = guidance(18, publication="2026-06-01T00:00:00Z", capture="2026-06-02T00:00:00Z", version_text="old")
    new = guidance(value, publication="2026-07-01T00:00:00Z", capture="2026-07-02T00:00:00Z", version_text="new")
    result = compare_guidance(old, new, decision_at="2026-08-01T00:00:00Z")
    assert result["status"] == "VERIFIED" and result["change"] == change
    assert result["evidence"] == [old.evidence, new.evidence]
    assert compare_guidance(old, new, decision_at="2026-06-30T00:00:00Z")["change"] == "UNKNOWN"
    new.normalized_value = None
    assert compare_guidance(old, new, decision_at="2026-08-01T00:00:00Z")["change"] == "UNKNOWN"


def test_explicit_withdrawal_not_missing_guidance_or_refusal():
    old = guidance(18, publication="2026-06-01T00:00:00Z", capture="2026-06-02T00:00:00Z", version_text="old")
    text = "Official Transcript\nPrepared remarks\nManagement: We withdraw revenue growth guidance for FY27.\nEnd of transcript"
    ctx = context("new", "transcript", publication_at="2026-07-01T00:00:00Z", captured_at="2026-07-02T00:00:00Z")
    new = records(extract_intelligence(ctx, canonical(text))[0], "revenue_growth")[0]
    assert compare_guidance(old, new, decision_at="2026-08-01T00:00:00Z")["change"] == "WITHDRAWN"
    refusal = extracted("Official Transcript\nPrepared remarks\nManagement: We never give yearly revenue growth guidance.\nEnd of transcript", "transcript")
    assert records(refusal, "revenue_growth")[0].statement_kind == "QUALITATIVE_OPINION"


def test_point_in_time_versions_unknown_publication_and_naive_dates():
    old = guidance(18, publication="2026-06-01T00:00:00Z", capture="2026-06-02T00:00:00Z", version_text="old")
    new = guidance(22, publication="2026-07-01T00:00:00Z", capture="2026-07-02T00:00:00Z", version_text="new")
    new.source["supersedes_version"] = old.source["version"]
    rows = [old, new]
    assert eligible_at(old, "2026-06-30T00:00:00Z", records=rows)
    assert not eligible_at(new, "2026-06-30T00:00:00Z", records=rows)
    assert not eligible_at(old, "2026-07-03T00:00:00Z", records=rows)
    assert eligible_at(new, "2026-07-03T00:00:00Z", records=rows)
    assert old.record_id != new.record_id
    new.source["publication_at"] = None
    assert compare_guidance(old, new, decision_at="2026-08-01T00:00:00Z")["change"] == "UNKNOWN"
    new.source["captured_at"] = None
    assert not eligible_at(new, "2026-08-01T00:00:00Z")
    with pytest.raises(ValueError):
        eligible_at(old, "2026-07-01")


def test_duplicates_and_contradictions_are_not_aggregated():
    rows = extracted("Order book was INR100 crore for FY26.\nOrder book was INR120 crore for FY26.")
    assert all(r.status == "AMBIGUOUS" for r in records(rows, "order_book"))
    r = records(extracted("Order book was INR100 crore."), "order_book")[0]
    assert len(reconcile([r, deepcopy(r)])) == 1


def test_native_pptx_chart_reads_embedded_values_and_cites_cells():
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    data = CategoryChartData()
    data.categories = ["FY26", "FY27"]
    data.add_series("Domestic", (100, 120))
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(6), Inches(4), data).chart
    chart.has_title = True
    chart.chart_title.text_frame.text = "Revenue (INR crore)"
    stream = io.BytesIO()
    presentation.save(stream)
    extracted_document = extract_local(stream.getvalue(), ".pptx")
    # A chart-only slide has no native selected page text in D03, but the explicit
    # embedded chart cells remain independently readable and unit-evidenced.
    rows, _ = extract_intelligence(context(), extracted_document)
    revenue = records(rows, "revenue", "VERIFIED")
    # Preserve exact OOXML cache strings rather than float-coerced display text.
    assert [r.normalized_value for r in revenue] == ["1000000000", "1200000000"]
    assert [r.original_value for r in revenue] == ["100", "120"]
    assert all(validate_evidence(r, extracted_document) for r in revenue)
    tampered = deepcopy(extracted_document)
    tampered["pages"][0]["elements"][0]["reference"]["embedded_series"][0]["values"][0] = "999"
    assert not validate_evidence(revenue[0], tampered)


def test_existing_pptx_and_scan_reuse_parsers():
    for filename, kind in [("d03_presentation.pptx", "presentation"), ("d03_scanned.pdf", "presentation")]:
        source = FIXTURES / filename
        data = extract_local(source.read_bytes(), source.suffix)
        rows, _ = extract_intelligence(context(kind=kind), data)
        assert all(validate_evidence(r, data) for r in rows)
        if filename.endswith("scanned.pdf"):
            assert data["extraction_status"] == "failed"
            assert any(r.status == "UNSUPPORTED" for r in rows)


def test_offline_run_blocks_network_database_and_overwrite(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Database access is forbidden in Phase 5 demo")
    with patch.object(sqlite3, "connect", forbidden):
        report = run(INPUTS / "manifest.json", tmp_path / "results", reuse_canonical=True, decision_at="2024-01-24T00:00:00Z")
    assert report["execution"]["database_connections"] == 0
    assert report["execution"]["network"] == "denied"
    assert len(report["documents"]) == 3
    assert json.loads((tmp_path / "results/point-in-time.json").read_text())["eligible"] == []
    with pytest.raises(ValueError):
        run(INPUTS / "manifest.json", tmp_path / "results")
    with deny_network(), pytest.raises(RuntimeError):
        socket.create_connection(("example.com", 443))


def test_tampered_original_rejected_before_output_created(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "source.pdf"
    source.write_bytes(b"not-an-original")
    manifest = inputs / "manifest.json"
    manifest.write_text(json.dumps({"documents": [{"path": source.name, "content_sha256": "0" * 64}]}))
    with pytest.raises(ValueError, match="hash mismatch"):
        run(manifest, tmp_path / "result")
    assert not (tmp_path / "result").exists()
