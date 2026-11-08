"""Safety boundaries: no semantic auto-admission and no unverifiable numeric claims."""
import json
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

import pytest

from scrapper.research.general_ai_experiment.protocol import FIELDS, validate_output, classification_questions
from scrapper.research.general_ai_experiment.verification import verify
from scrapper.research.general_ai_experiment.runner import budget_charge_bound, api_key, budget_used, can_reserve, reasoning_settings
from scrapper.tests.test_phase5_document_intelligence import canonical, context


def proposal(quote='Revenue was INR 155 crore.', **changes):
    c = context()
    event = dict.fromkeys(FIELDS)
    event.update(document_id=c.document_id, event_type='guidance', event_direction='unclear',
        historical_or_forward='historical', source_page=1, exact_source_quote=quote,
        reported_value='155', unit='crore')
    event.update(changes)
    return event


def test_exact_quote_does_not_establish_semantics_or_ml_eligibility():
    e = proposal()
    result = verify(e, context(), canonical(e['exact_source_quote']), [])
    assert result['source_verification'] == 'source_matched'
    assert result['semantic_review_status'] == 'pending'
    assert result['historical_ml_eligible'] is False


@pytest.mark.parametrize('changes,reason', [
    ({'reported_value': '55'}, 'reported_value_not_verbatim_in_quote'),
    ({'reported_value': '1.55'}, 'reported_value_not_verbatim_in_quote'),
    ({'unit': 'million'}, 'unit_not_verbatim_in_quote'),
    ({'document_id': 'other'}, 'wrong_document_identity'),
    ({'source_page': 2}, 'quote_not_in_identified_native_page_or_element_visual_review_required'),
    ({'exact_source_quote': 'Revenue was INR 200 crore.'}, 'quote_not_in_identified_native_page_or_element_visual_review_required'),
])
def test_reject_wrong_number_scale_identity_or_quote(changes, reason):
    e = proposal(**changes)
    result = verify(e, context(), canonical('Revenue was INR 155 crore.'), [])
    assert reason in result['rejection_reasons']
    assert result['historical_ml_eligible'] is False


def test_strict_schema_no_repair_and_no_boolean_page():
    assert validate_output({'events': [proposal()]})
    assert not validate_output({'events': [proposal(source_page=True)]})
    assert not validate_output({'events': [proposal(reported_value=155)]})
    assert not validate_output({'events': [], 'summary': 'extra'})
    assert validate_output({'events': []})


def test_high_context_tier_and_cache_cost_reserved():
    model = {'context_length': 1000000, 'pricing': {'prompt': '0.0000001', 'completion': '0.0000005',
             'overrides': [{'prompt': '0.0000005', 'completion': '0.0000025', 'input_cache_write': '0.000000625'}]}}
    endpoint = {'pricing': {'prompt': '0.0000001', 'completion': '0.0000005'}, 'context_length': 1000000}
    bound, inp, out = budget_charge_bound(model, endpoint)
    assert inp == Decimal('0.000000625')
    assert out == Decimal('0.0000025')
    assert bound > Decimal('0.625')


def test_unknown_provider_fee_blocks_execution():
    with pytest.raises(ValueError, match='unbounded_additional'):
        budget_charge_bound({'context_length': 1000}, {'pricing': {'prompt': '0', 'completion': '0', 'per_request': '1'}})


def test_secret_reader_does_not_execute_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    (tmp_path / '.env').write_text('UNRELATED_SECRET=hidden\nOPENROUTER_API_KEY="$(touch should-not-exist)"\n')
    with patch('scrapper.research.general_ai_experiment.runner.ROOT', tmp_path):
        assert api_key() == '$(touch should-not-exist)'
    assert not (tmp_path / 'should-not-exist').exists()


def test_jev_uses_typed_questions_including_abstention():
    questions = classification_questions()
    assert all(q['type'] == 'choice' for q in questions.values())
    assert all('unclear' in q['criteria'] for q in questions.values())


def test_uncapped_key_experiment_budget_accounts_for_failed_requests():
    ledger = {'requests': [{'reserved_usd': '3', 'actual_usd': None},
                           {'reserved_usd': '5', 'actual_usd': '1.25'}]}
    assert budget_used(ledger) == Decimal('4.25')
    assert can_reserve(ledger, Decimal('5.75'))
    assert not can_reserve(ledger, Decimal('5.7500001'))
    assert not can_reserve(ledger, Decimal('NaN'))
    assert not can_reserve(ledger, Decimal('-1'))


def test_whitespace_reanchoring_preserves_original_quote():
    e = proposal()
    result = verify(e, context(), canonical('Revenue was INR 155\ncrore.'), [])
    assert result['source_verification'] == 'source_matched'
    assert result['canonical_evidence'][0]['quote'] == 'Revenue was INR 155\ncrore.'
    assert result['event']['exact_source_quote'] == 'Revenue was INR 155 crore.'
    assert result['quote_match_mode'].startswith('whitespace_only')


def test_currency_unit_separated_by_value_is_not_silently_rescaled():
    e = proposal(unit='INR crore')
    result = verify(e, context(), canonical(e['exact_source_quote']), [])
    assert result['source_verification'] == 'source_matched'
    assert result['semantic_review_status'] == 'pending'


def test_sentence_punctuation_is_not_a_numeric_digit_boundary():
    e = proposal(reported_value='INR 155 crore', unit='INR crore')
    assert verify(e, context(), canonical(e['exact_source_quote']), [])['source_verification'] == 'source_matched'
    e = proposal(reported_value='INR 155 crore', unit='INR million')
    assert 'unit_not_verbatim_in_quote' in verify(e, context(), canonical(e['exact_source_quote']), [])['rejection_reasons']


def test_reasoning_transport_respects_mandatory_and_supported_efforts():
    assert reasoning_settings({'reasoning': {'mandatory': True, 'supported_efforts': ['low', 'medium']}}) == {'effort': 'low'}
    assert reasoning_settings({'reasoning': {'supported_efforts': ['minimal', 'low']}}) == {'effort': 'minimal'}
    assert reasoning_settings({'reasoning': {'supported_efforts': ['none', 'high']}}) == {'effort': 'none'}
    assert reasoning_settings({}) == {'enabled': False}


def test_json_unicode_quote_can_only_reanchor_to_existing_original_characters():
    e = proposal(quote=r'The firm\u2019s revenue was INR 155 crore.')
    source = canonical('The firm’s revenue was INR 155 crore.')
    result = verify(e, context(), source, [])
    assert result['source_verification'] == 'source_matched'
    assert result['event']['exact_source_quote'] == e['exact_source_quote']
    assert result['canonical_evidence'][0]['quote'] == 'The firm’s revenue was INR 155 crore.'
    e['exact_source_quote'] = r'The firm\u2019s revenue was INR 200 crore.'
    assert verify(e, context(), source, [])['source_verification'] == 'rejected'
