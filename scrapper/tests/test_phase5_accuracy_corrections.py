"""Actual failure regressions and conservative semantic boundaries, offline only."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scrapper.tests.test_phase5_document_intelligence import canonical, context, extracted, records
from scrapper.research.document_intelligence.context import period_name, time_context
from scrapper.research.document_intelligence.evidence import text_evidence, cell_evidence, validate_evidence
from scrapper.research.document_intelligence.extract import extract_intelligence
from scrapper.research.document_intelligence.evaluation import context_for
from scrapper.research.document_intelligence.numbers import normalize_value
from scrapper.research.document_intelligence.research_features import qualified_fact

ROOT = Path(__file__).resolve().parents[2] / 'artifacts/phase5/quality-evaluation/v1'


def real(key):
    manifest = json.loads((ROOT/'inputs/manifest.json').read_text())
    row = next(r for r in manifest['documents'] if r['key'] == key)
    return context_for(row), json.loads((ROOT/'results'/f'{key}.canonical.json').read_text())


def test_actual_tata_empty_header_does_not_abort_document():
    ctx, data = real('tata_power_restated')
    failed = json.loads((ROOT/'results/failing-evidence-record.json').read_text())[0]
    assert failed['evidence'][1]['quote'] == ''
    page = data['pages'][9]
    table = next(e for e in page['elements'] if e['element_position'] == 3)
    with pytest.raises(ValueError, match='Empty'):
        cell_evidence(page, table, 0, 1)
    result, diagnostics = extract_intelligence(ctx, data)
    assert result and all(validate_evidence(r, data) for r in result)
    assert any(r.metric == 'table_layout' and r.status == 'UNSUPPORTED' for r in result)
    subsidiaries = [r for r in result if r.metric == 'profit' and r.dimensions.get('entity_scope') == 'CGPL']
    assert {r.reporting_period for r in subsidiaries if any(e['page'] == 35 for e in r.evidence)} == {'Q4FY21', 'Q4FY20'}
    # Native table has no declared currency unit. Correct periods aren't a licence
    # to assume INR crore. Keep numeric acceptance quarantined.
    assert all(r.status == 'AMBIGUOUS' for r in subsidiaries)


def test_invalid_record_is_rejected_while_valid_siblings_survive():
    data = canonical('Revenue was INR20 crore for FY26.')
    good = records(extracted(data['pages'][0]['selected_text']), 'revenue', 'VERIFIED')[0]
    bad = deepcopy(good); bad.evidence[0]['end'] = bad.evidence[0]['start']
    with patch('scrapper.research.document_intelligence.extract._signals', return_value=[bad, good]):
        result, diagnostics = extract_intelligence(context(), data)
    assert records(result, 'revenue', 'VERIFIED')
    quarantine = records(result, 'evidence_rejection')[0]
    assert quarantine.normalized_value is None and not quarantine.evidence
    assert diagnostics['evidence_validation']['failed'] == 1
    assert all(validate_evidence(r, data) for r in result)


@pytest.mark.parametrize('text', ['', '  ', '\n'])
def test_empty_spans_cannot_be_constructed(text):
    with pytest.raises(ValueError):
        text_evidence(canonical('some text')['pages'][0], text)


@pytest.mark.parametrize('amount', ['120', '245'])
def test_projects_are_nouns_and_forward_claims_stay_separate(amount):
    result = extracted(f'Order book for existing projects stood at INR{amount} crore as on 31 March 2026; we expect revenue of INR300 crore for FY27.')
    order = records(result, 'order_book', 'VERIFIED')[0]
    assert order.value_basis == 'ACTUAL' and order.statement_kind == 'HISTORICAL_FACT'
    assert order.reporting_period is None and order.as_of_date == '2026-03-31'
    revenue = records(result, 'revenue', 'VERIFIED')[0]
    assert revenue.value_basis == 'PROJECTED' and revenue.reporting_period is None
    assert revenue.time_horizon == 'FY27'


def test_working_capital_keyword_does_not_override_favourable_polarity():
    result = extracted('Working capital utilisation was negligible for FY26.')
    wc = records(result, 'working_capital')[0]
    assert wc.statement_kind == 'HISTORICAL_FACT'
    assert wc.dimensions['polarity'] == 'FAVOURABLE'
    assert records(extracted('Working capital pressure could weaken liquidity.'), 'working_capital')[0].statement_kind == 'RISK_WARNING'


@pytest.mark.parametrize('prefix,kind,basis', [('We expect', 'GUIDANCE', 'PROJECTED'), ('We target', 'TARGET', 'PROJECTED'), ('We could reach', 'CONDITIONAL_FORECAST', 'CONDITIONAL')])
def test_claim_grammar(prefix,kind,basis):
    r = records(extracted(f'{prefix} revenue of INR300 crore for FY27.'), 'revenue', 'VERIFIED')[0]
    assert (r.statement_kind,r.value_basis,r.time_horizon,r.reporting_period) == (kind,basis,'FY27',None)


@pytest.mark.parametrize('text,expected', [("Q1 '27", 'Q1FY27'),('Q3 FY2024','Q3FY24'),('9M FY24','9MFY24'),('1QFY25','Q1FY25'),('H1 FY24','H1FY24')])
def test_period_grammar(text,expected):
    assert period_name(text) == expected


@pytest.mark.parametrize('duration,period', [('quarter','Q2FY26'),('six months','H1FY26'),('nine months','9MFY26'),('year','FY26')])
def test_explicit_duration_headers(duration,period):
    end = '31 March 2026' if duration=='year' else '30 September 2025'
    assert time_context(f'{duration} ended {end}')[0] == period


def test_unrelated_dates_never_supply_reporting_periods():
    r = records(extracted('Presentation dated 15 May 2026. Revenue was INR70 crore.'), 'revenue', 'VERIFIED')[0]
    assert r.reporting_period is None and r.as_of_date is None
    assert not qualified_fact(r,source_validation={'exact_citation_valid':True,'semantic_match':True})['qualified']
    r = records(extracted('Revenue was INR70 crore during FY20-FY23.'), 'revenue', 'VERIFIED')[0]
    assert r.reporting_period is None


def test_real_comparatives_and_subsidiary_scope():
    ctx, data = real('vimta_call');result,_ = extract_intelligence(ctx,data)
    pat = [r for r in records(result,'profit','VERIFIED') if any(e['page']==5 for e in r.evidence)]
    assert {r.reporting_period for r in pat} >= {'Q1FY27','Q1FY26'}
    ctx, data = real('welspun');result,_ = extract_intelligence(ctx,data)
    subsidiary = next(r for r in records(result,'revenue','VERIFIED') if r.normalized_value=='824000000.0')
    assert subsidiary.reporting_period == 'Q2FY24'
    assert subsidiary.dimensions['entity_scope'] == 'MEPL'


def test_real_timestamped_speaker_heading():
    ctx,data=real('reliance');result,_=extract_intelligence(ctx,data)
    r=next(r for r in records(result,'operating_margin','VERIFIED') if r.normalized_value=='8.6')
    assert r.speaker['name']=='V. Srikanth' and r.speaker['role']=='management'
    assert r.section=='prepared_remarks' and r.reporting_period=='Q3FY25'
    assert r.dimensions['entity_scope']=='retail'


def test_real_multirow_financial_header_and_ratio_units():
    ctx,data=real('vimta_care');result,_=extract_intelligence(ctx,data)
    table=[r for r in result if any(e['kind']=='table_cell' and e['page']==4 and e['element']==6 for e in r.evidence)]
    assert len(table)==15 and all(r.status=='VERIFIED' for r in table)
    assert {r.reporting_period for r in table}=={'FY21','FY22','FY23'}
    assert all(r.dimensions['entity_scope']=='consolidated' for r in table)


def test_currency_range_cites_actual_cross_fragment_text():
    assert normalize_value('INR600 crores to INR800 crores')['normalized_value']=={'low':'6000000000','high':'8000000000'}
    ctx,data=real('welspun');result,_=extract_intelligence(ctx,data)
    r=records(result,'order_inflows','VERIFIED')[0]
    assert r.statement_kind=='TARGET' and r.time_horizon=='FY24'
    assert '\n' in r.original_value and validate_evidence(r,data)


def test_real_facility_rating_actions_and_prior_grades():
    ctx,data=real('sona_indra');result,_=extract_intelligence(ctx,data)
    events=records(result,'rating_action','VERIFIED')
    assert len(events)==5
    assert {r.normalized_value['action'] for r in events}=={'upgraded','reaffirmed','withdrawn'}
    assert all(r.effective_date is None for r in events)
    issuer=next(r for r in events if r.normalized_value['facility']=='Long-Term Issuer Rating')
    assert issuer.normalized_value['previous_grade']=='IND AA'
    ctx,data=real('sarup_brickwork');result,_=extract_intelligence(ctx,data)
    r=next(r for r in records(result,'rating_action','VERIFIED') if r.normalized_value['action']=='downgraded')
    assert r.normalized_value['grade']=='BWR C' and r.normalized_value['previous_grade']=='BWR B'


def test_publication_certification_is_not_added_by_extractor():
    ctx,data=real('tata_power_restated');result,_=extract_intelligence(ctx,data)
    assert all(r.source['publication_at'] is None and 'publication_evidence' not in r.source for r in result)


@pytest.mark.parametrize('agency', ['CARE', 'IND', 'BWR'])
@pytest.mark.parametrize('grade', ['AA-', 'AA+', 'BBB-', 'BB+', 'A1+'])
def test_grade_suffixes_and_inherited_agency_prefix(agency,grade):
    from scrapper.research.document_intelligence.ratings import grades
    assert grades(f'{agency} {grade}/Stable/A1+') == [(f'{agency} {grade}','stable'),(f'{agency} A1+',None)]


def test_agency_html_period_does_not_cross_claim_boundaries():
    from scrapper.research.document_intelligence.extract import _signals
    text='Revenue was INR100 crore during FY26. Liquidity: Adequate.'
    result=_signals(context(),text,[text_evidence(canonical(text)['pages'][0],text)],
                    metrics={'revenue','liquidity'},speaker={'role':'rating_agency'})
    assert records(result,'revenue')[0].reporting_period=='FY26'
    assert records(result,'liquidity')[0].reporting_period is None


def test_annual_ratios_are_levels_and_cumulative_flows_are_separate():
    from scrapper.documents.extraction import DocumentElement
    ratio=DocumentElement(0,'table','native',rows=(('Metric','FY26'),('Overall gearing (times)','0.4')))
    result,_=extract_intelligence(context(kind='credit_rating'),canonical('Financial table',elements=(ratio,)))
    r=records(result,'leverage','VERIFIED')[0]
    assert r.dimensions['measurement_kind']=='level' and r.dimensions['period_aggregation']=='NOT_ESTABLISHED'
    flow=DocumentElement(0,'table','native',rows=(('Metric','9M FY26 INR crore'),('Revenue','400')))
    result,_=extract_intelligence(context(),canonical('Financial table',elements=(flow,)))
    assert records(result,'revenue','VERIFIED')[0].dimensions['period_aggregation']=='CUMULATIVE'
