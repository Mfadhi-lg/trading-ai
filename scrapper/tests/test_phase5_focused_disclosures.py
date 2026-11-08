"""Semantic boundaries for the bounded five-category mode."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch
import pytest
from scrapper.tests.test_phase5_document_intelligence import canonical, context
from scrapper.research.document_intelligence.extract import extract_intelligence
from scrapper.research.document_intelligence.evidence import validate_evidence
from scrapper.research.document_intelligence.numbers import normalize_value
from scrapper.research.document_intelligence.research_features import public_pit_readiness


def focused(text, kind='presentation', **kwargs):
    extraction = canonical(text)
    rows, diag = extract_intelligence(context(text, kind, **kwargs), extraction, focus_disclosures=True)
    assert all(validate_evidence(r, extraction) for r in rows)
    return [r for r in rows if r.dimensions.get('disclosure_categories') and not r.dimensions.get('evidence_only')]


def test_expected_orders_are_not_confirmed_book():
    rows = focused('We expect order inflows of INR600 crore for FY28.\nExisting order book stands at INR808 crore as on 31 March 2026.')
    flow = next(r for r in rows if r.metric == 'order_inflows')
    book = next(r for r in rows if r.metric == 'order_book')
    assert flow.value_basis == 'PROJECTED' and flow.time_horizon == 'FY28'
    assert flow.dimensions['order_status'] == 'EXPECTED'
    assert book.value_basis == 'ACTUAL' and book.as_of_date == '2026-03-31'
    assert book.dimensions['order_status'] == 'CONFIRMED'


def test_scope_and_stages_and_mtpa():
    rows = focused('Subsidiary: Alpha Ltd will commission production capacity of 2.5 MTPA in FY28.\nCapital expenditure incurred was INR50 crore in FY25.')
    capacity = next(r for r in rows if r.metric == 'manufacturing_capacity')
    assert capacity.unit == 'MTPA' and capacity.normalized_value == '2.5'
    assert capacity.dimensions['entity_scope'] == 'Alpha Ltd'
    assert capacity.dimensions['project_stage'] == 'PLANNED'
    assert capacity.time_horizon == 'FY28' and capacity.reporting_period is None
    capex = next(r for r in rows if r.metric == 'capex')
    assert capex.value_basis == 'ACTUAL' and capex.dimensions['project_stage'] == 'COMPLETED'
    assert capex.reporting_period == 'FY25'


@pytest.mark.parametrize('text', ['Working capital liquidity is healthy with low utilization.',
    'No material delays in project execution.', 'Regulatory approvals were received.',
    'We are confident about the future.'])
def test_positive_neutral_and_optimism_are_not_risks_or_guidance(text):
    assert not any('operational_risk' in r.dimensions['disclosure_categories'] or
                   'management_guidance' in r.dimensions['disclosure_categories'] for r in focused(text))


def test_analyst_question_and_historical_financials_excluded():
    rows = focused('Official Earnings Call Transcript\nPrepared remarks\nManagement: Revenue was INR100 crore for FY26.\nQ&A\nAnalyst: Do you expect revenue of INR200 crore for FY27?\nEnd of transcript', 'transcript')
    assert not any(r.metric == 'revenue' for r in rows)


def test_no_qualitative_cover_period_and_no_historical_admission():
    rows = focused('Official Q2FY26 Earnings Call Transcript\nPrepared remarks\nManagement: Demand slowdown may delay project execution.\nEnd of transcript', 'transcript', reporting_period='Q2FY26')
    risk = next(r for r in rows if 'operational_risk' in r.dimensions['disclosure_categories'])
    assert risk.reporting_period is None
    assert not public_pit_readiness(risk.source, '2026-09-01T00:00:00Z')['eligible']


def test_ambiguous_currency_not_silently_inferred():
    row = next(r for r in focused('Order book stands at 808 crore.') if r.metric == 'order_book')
    assert row.status != 'VERIFIED'


def test_status_tracks_reconciliation():
    rows = focused('Order book stands at INR100 crore.\nOrder book stands at INR200 crore.')
    assert all(r.disclosure_status == 'AMBIGUOUS' for r in rows)


def test_native_project_sections_do_not_contradict_other_projects():
    text = ('Expansion at Alpha Plant\nEstimated capex INR100 crore\n'
        'Construction work is ongoing\nExpected completion by Q4 FY28\n'
        'Expansion at Beta Plant\nEstimated capex INR200 crore\nExpected completion by Q2 FY29')
    rows = [r for r in focused(text) if r.metric == 'capex']
    assert len(rows)==2 and all(r.status=='VERIFIED' for r in rows)
    assert {(r.dimensions['entity_scope'],r.time_horizon) for r in rows} == {
        ('Expansion at Alpha Plant','Q4FY28'),('Expansion at Beta Plant','Q2FY29')}
    assert rows[0].dimensions['project_stage']=='UNDER_CONSTRUCTION'
    assert any('Construction work is ongoing' in e.get('quote','') for e in rows[0].evidence)


def test_unknown_capacity_basis_and_from_to_are_not_exact_claims():
    rows=focused('Capacity expansion from 4.2 to 6 MTPA.')
    assert all(r.status=='AMBIGUOUS' for r in rows)


def test_rating_sensitivity_sections_keep_direction_and_no_new_rating_event():
    text=('CRISIL Ratings\nRating Update\nSonam Limited\nUpward factors:\n'
        'Significant growth leading to PAT of Rs 5 crore on sustained basis\n'
        'Improvement in working capital cycle\nDownward factors:\n'
        'Increase in dependence on external debt weakening liquidity\nAbout the Company\n'
        'Manufactures clocks at its facility.')
    rows=focused(text,'credit_rating')
    conditions=[r for r in rows if r.metric=='rating_sensitivities' and r.status=='EVIDENCED_STATEMENT']
    assert {r.dimensions['sensitivity_direction'] for r in conditions} == {'POSITIVE','NEGATIVE'}
    assert all(r.value_basis=='CONDITIONAL' for r in conditions)
    assert not any(r.metric=='rating_action' and r.status=='VERIFIED' for r in rows)


def test_focused_offline_projection_requires_public_certification(tmp_path):
    from scrapper.research.document_intelligence.offline import run
    inputs=tmp_path/'inputs'
    inputs.mkdir()
    body=b'controlled original bytes'
    (inputs/'original.pdf').write_bytes(body)
    manifest=inputs/'manifest.json'
    manifest.write_text(json.dumps({'documents':[dict(path='original.pdf',document_id='controlled:1',
        company_id=42,content_sha256=hashlib.sha256(body).hexdigest(),document_type='presentation',
        captured_at='2026-08-17T12:00:00Z',publication_at='2026-08-16T10:00:00Z')]}))
    with patch('scrapper.research.document_intelligence.offline.extract_local',return_value=canonical(
            'Order book stands at INR808 crore as on 31 March 2026.')):
        run(manifest,tmp_path/'output',focus_disclosures=True,decision_at='2026-10-14T00:00:00Z')
    projection=json.loads((tmp_path/'output/point-in-time.json').read_text())
    assert projection['eligible']==[]
    assert projection['availability_basis']=='certified_public_version_and_capture'


def test_unsupported_powerpoint_plot_keeps_other_slide_evidence():
    import io
    from zipfile import ZipFile
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    from scrapper.research.document_intelligence.offline import extract_local
    p=Presentation()
    slide=p.slides.add_slide(p.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0),Inches(0),Inches(6),Inches(1)).text='Order book stands at INR808 crore as on 31 March 2026.'
    data=CategoryChartData()
    data.categories=['FY26','FY27']
    data.add_series('Orders',(100,120))
    slide.shapes.add_chart(XL_CHART_TYPE.PIE,Inches(1),Inches(2),Inches(5),Inches(4),data)
    initial=io.BytesIO()
    p.save(initial)
    altered=io.BytesIO()
    with ZipFile(io.BytesIO(initial.getvalue())) as source,ZipFile(altered,'w') as target:
        for item in source.infolist():
            raw=source.read(item.filename)
            if item.filename=='ppt/charts/chart1.xml':
                raw=raw.replace(b'c:pieChart',b'c:pie3DChart')
            target.writestr(item,raw)
    extracted=extract_local(altered.getvalue(),'.pptx')
    rows,_=extract_intelligence(context('pptx'),extracted,focus_disclosures=True)
    assert any(r.metric=='order_book' and r.status=='VERIFIED' for r in rows)
    chart=next(e for e in extracted['pages'][0]['elements'] if e['element_kind']=='chart')
    assert chart['reference']['chart_type']=='unsupported'
    assert chart['reference']['embedded_chart_structure']['series'][0]['points'][1]['value']=='120'
