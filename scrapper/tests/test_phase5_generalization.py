"""Known-source regressions and evidence safety; never train on new holdout."""
import json
from pathlib import Path

import pytest

from scrapper.tests.test_phase5_document_intelligence import canonical, context, extracted, records
from scrapper.research.document_intelligence.context import period_name
from scrapper.research.document_intelligence.evidence import validate_evidence
from scrapper.research.document_intelligence.evaluation import context_for, score
from scrapper.research.document_intelligence.extract import extract_intelligence
from scrapper.research.document_intelligence.transcripts import corroborated_body, roster

ROOT = Path(__file__).resolve().parents[2]


def test_himadri_known_regression_all_eight_frozen_numeric_claims():
    source = ROOT / 'artifacts/phase5/corrections/v2/final-holdout/inputs'
    row = json.loads((source / 'manifest.json').read_text())['documents'][0]
    data = json.loads((source / 'himadri.archive.json').read_text())['extraction']
    result, _ = extract_intelligence(context_for(row), data)
    refs = json.loads((source / 'references.json').read_text())
    scored = score({'himadri': result}, refs)
    assert all(r['outcome'] == 'TP' for r in scored['reference_results'])
    assert scored['attributes']['reporting_period']['correct'] == 7
    assert all(validate_evidence(r, data) for r in result)


@pytest.mark.parametrize('dash', ['--', '—', '-'])
def test_double_dash_management_introductions(dash):
    data = canonical(f'Mr. Ravi Kumar {dash} CEO; Mr. Neha Shah {dash} Chief Financial Officer')
    assert roster(data)['ravikumar']['role'] == 'management'
    assert roster(data)['nehashah']['role'] == 'management'


@pytest.mark.parametrize('label,unit,value', [('Net Income', 'INR30 crore', '300000000'), ('Net Income margin', '12%', '12')])
def test_net_income_aliases_do_not_confuse_margin_with_amount(label, unit, value):
    metric = 'profit_margin' if 'margin' in label else 'profit'
    result = extracted(f'{label} was {unit} for FY2026 on a standalone basis.')
    r = records(result, metric, 'VERIFIED')[0]
    assert r.normalized_value == value and r.reporting_period == 'FY26'
    assert r.dimensions['entity_scope'] == 'standalone'
    if metric == 'profit_margin':
        assert not records(result, 'profit', 'VERIFIED')


@pytest.mark.parametrize('year', ['FY 20 18', 'FY20 18', 'Q4 FY 20 18'])
def test_explicit_split_four_digit_fiscal_tokens(year):
    assert period_name(year) == ('Q4FY18' if year.startswith('Q') else 'FY18')


def test_split_digit_recovery_requires_intact_native_source_span():
    text, offset = corroborated_body('EBITDA was Rs. 247 crores in FY2018.', 'EBITDA was Rs. 24 7 crores in FY 20 18.')
    assert text == 'EBITDA was Rs. 247 crores in FY2018.' and offset == 0
    assert corroborated_body('EBITDA was Rs. 24 crores.', 'EBITDA was Rs. 24 7 crores.')[0] is None
    # Unsupported split digits cannot become a guessed value from the tail.
    assert not records(extracted('Revenue was Rs. 24 7 crores in FY18.'), 'revenue', 'VERIFIED')


@pytest.mark.parametrize('text,metric,values', [
    ('EBITDA increased from Rs. 247 crores in FY2017 to Rs. 450 crores in FY2018.', 'operating_profit', {'2470000000': 'FY17', '4500000000': 'FY18'}),
    ('Net Income was INR30 crore in FY26 from INR20 crore in FY25.', 'profit', {'300000000': 'FY26', '200000000': 'FY25'}),
    ('Net Income margin was 12% in FY26 compared with 10% in FY25.', 'profit_margin', {'12': 'FY26', '10': 'FY25'}),
])
def test_two_values_bind_to_explicit_comparative_periods(text, metric, values):
    result = extracted(text)
    assert {r.normalized_value: r.reporting_period for r in records(result, metric, 'VERIFIED')} == values


def test_comparison_without_period_mapping_stays_ambiguous():
    assert not records(extracted('Revenue was INR30 crore from INR20 crore.'), 'revenue', 'VERIFIED')
    assert not records(extracted('Revenue was INR30 crore and INR20 crore in FY26.'), 'revenue', 'VERIFIED')


def test_explicit_scope_and_annual_financial_handoff_are_cited():
    data = canonical('Mr. Ravi Kumar -- CEO\nModerator: Ravi will discuss financials of FY2026.\nRavi Kumar: On a consolidated basis. Net debt has come down to INR30 crore from INR40 crore in FY25. Our revenue was INR70 crore.')
    result, _ = extract_intelligence(context(kind='transcript'), data)
    debt = records(result, 'debt', 'VERIFIED')
    assert {r.normalized_value: r.reporting_period for r in debt} == {'300000000': 'FY26', '400000000': 'FY25'}
    revenue = records(result, 'revenue', 'VERIFIED')[0]
    assert revenue.reporting_period == 'FY26'
    assert revenue.dimensions['entity_scope'] == 'consolidated'
    assert any('consolidated' in e['quote'] for e in revenue.evidence)
    assert any('financials of FY2026' in e['quote'] for e in revenue.evidence)
    assert all(validate_evidence(r, data) for r in result)


def test_contradictory_same_scope_values_are_preserved():
    result = extracted('Net Income was INR30 crore in FY26. Net Income was INR20 crore in FY26.')
    claims = records(result, 'profit', 'AMBIGUOUS')
    assert {r.normalized_value for r in claims} == {'300000000', '200000000'}
    assert all('contradictory_same_scope_values' in r.reasons for r in claims)
