"""Quality evaluator and safe research features; controlled cases != accuracy data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scrapper.research.document_intelligence.evaluation import compact, score, in_window, original_text, period_key
from scrapper.research.document_intelligence.research_features import public_pit_readiness, qualified_fact, build_examples, order_book_growth
from scrapper.research.document_intelligence.schema import ResearchEvidence
from scrapper.research.document_intelligence.temporal import eligible_at, compare_guidance

ROOT=Path(__file__).resolve().parents[2]/'artifacts/phase5/quality-evaluation/v1'


def dataset():
    return json.loads((ROOT/'inputs/manifest.json').read_text()),json.loads((ROOT/'inputs/references.json').read_text())


def source():
    # Controlled provenance and timestamps, not historical corporate truth.
    return {'document_id':'controlled-revision','company_id':1,'content_sha256':'a'*64,'version':'a'*64,
            'publication_at':'2026-01-10T12:00:00Z','captured_at':'2026-01-11T12:00:00Z',
            'publication_evidence':{'basis':'exchange_publication_timestamp','source_reference':'CONTROLLED_TEST_RECEIPT',
                                    'validated':True,'timestamp_matches_source':True,'content_sha256':'a'*64}}


def record(value='100',**kwargs):
    return ResearchEvidence(source=source(),metric='revenue_growth',statement_kind='GUIDANCE',status='VERIFIED',
                            normalized_value=value,unit='percent',value_basis='PROJECTED',time_horizon='FY27',
                            speaker={'name':None,'role':'management'},evidence=[{'page':1,'quote':'CONTROLLED_TEST_QUOTE'}],**kwargs).seal()


def test_frozen_gold_is_independent_and_source_pinned():
    manifest,refs=dataset()
    assert len(manifest['documents'])==11 and len(refs['references'])==93
    assert hashlib.sha256((ROOT/'inputs/references.json').read_bytes()).hexdigest()==(ROOT/'inputs/references.sha256').read_text().strip()
    assert refs['methodology']['second_reviewer']=='PENDING'
    assert all(r['review_status']=='AGENT_SOURCE_CHECKED_HUMAN_ADJUDICATION_PENDING' for r in refs['references'])
    assert all('record_id' not in r for r in refs['references'])


def test_gold_terms_exist_in_independent_original_readers():
    manifest,refs=dataset()
    for row in manifest['documents']:
        path=ROOT/'inputs'/row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row['content_sha256']
        for page in {r['page'] for r in refs['references'] if r['document_key']==row['key']}:
            raw=original_text(path.read_bytes(),path.suffix,page)
            for ref in refs['references']:
                if ref['document_key']==row['key'] and ref['page']==page:
                    assert all(compact(term) in compact(raw) for term in ref['required_source_terms'])


def test_publication_period_and_capture_are_distinct():
    s=source()
    assert public_pit_readiness(s,'2026-01-12T00:00:00Z')['eligible']
    assert not public_pit_readiness(s,'2026-01-10T18:00:00Z')['eligible']
    s['publication_at']=None
    s['reporting_period']='FY20'
    assert not public_pit_readiness(s,'2026-10-01T00:00:00Z')['eligible']
    # Existing capture-safe API accepts after capture; public ML gate rejects.
    r=record();r.source=s
    assert eligible_at(r,'2026-10-01T00:00:00Z')
    assert not public_pit_readiness(r.source,'2026-10-01T00:00:00Z')['eligible']


@pytest.mark.parametrize('change',[{'publication_at':'2027-01-01T00:00:00Z'}, {'captured_at':None}, {'ingested_at':'2027-01-01T00:00:00Z'}, {'company_id':None}])
def test_missing_or_future_metadata_is_blocked(change):
    s=source();s.update(change)
    assert not public_pit_readiness(s,'2026-12-31T00:00:00Z')['eligible']


def test_public_timestamp_receipt_must_match_exact_version():
    s=source();s['publication_evidence']['content_sha256']='b'*64
    assert not public_pit_readiness(s,'2026-01-12T00:00:00Z')['eligible']


def test_revised_versions_do_not_change_earlier_cutoff():
    old=record('18');new=record('22')
    new.source=deepcopy(old.source)
    new.source.update(content_sha256='b'*64,version='b'*64,supersedes_version=old.source['version'],
                      publication_at='2026-02-01T12:00:00Z',captured_at='2026-02-02T12:00:00Z')
    new.source['publication_evidence']['content_sha256']='b'*64
    assert eligible_at(old,'2026-01-31T00:00:00Z',records=[old,new])
    assert not eligible_at(new,'2026-01-31T00:00:00Z',records=[old,new])
    assert not eligible_at(old,'2026-02-03T00:00:00Z',records=[old,new])
    assert eligible_at(new,'2026-02-03T00:00:00Z',records=[old,new])


def test_repeated_guidance_uses_two_statements_and_unknown_dates_block():
    old=record('18');new=record('18')
    new.source=deepcopy(new.source);new.source.update(publication_at='2026-02-01T12:00:00Z',captured_at='2026-02-02T12:00:00Z')
    assert compare_guidance(old,new,decision_at='2026-03-01T00:00:00Z')['change']=='UNCHANGED'
    new.source['publication_at']=None
    assert compare_guidance(old,new,decision_at='2026-03-01T00:00:00Z')['change']=='UNKNOWN'


def test_contradictory_range_does_not_force_direction():
    old=record();new=record()
    old.normalized_value={'low':'18','high':'22'};new.normalized_value={'low':'19','high':'21'}
    new.source=deepcopy(new.source);new.source.update(publication_at='2026-02-01T12:00:00Z',captured_at='2026-02-02T12:00:00Z')
    assert compare_guidance(old,new,decision_at='2026-03-01T00:00:00Z')['status']=='AMBIGUOUS'


@pytest.mark.parametrize('effective',[None,'2026-02-01','2025-12-01'])
def test_rating_announcement_is_never_unchecked_current_grade(effective):
    result=public_pit_readiness(source(),'2026-01-12T00:00:00Z',effective_date=effective,current_rating=True)
    assert not result['eligible']
    assert 'current_rating_requires_complete_later_event_check' in result['reasons']


def test_semantic_review_required_even_when_citation_matches():
    r=record();r.statement_kind='HISTORICAL_FACT';r.reporting_period='FY26'
    assert not qualified_fact(r,source_validation={'exact_citation_valid':True,'semantic_match':False})['qualified']
    r.value_basis='ACTUAL'
    assert qualified_fact(r,source_validation={'exact_citation_valid':True,'semantic_match':True})['qualified']
    assert not qualified_fact(r,source_validation={'exact_citation_valid':True,'semantic_match':True},required_scope=True)['qualified']


def test_real_features_are_traceable_and_not_historical_ml_certified():
    manifest,refs=dataset()
    features=build_examples(manifest,refs,decision_at='2026-10-13T00:00:00Z')
    growth=next(f for f in features if f['feature']=='order_book_growth')
    assert growth['value']=='0.840336134454'
    assert len(growth['input_reference_ids'])==2
    assert all(not f['public_pit_eligible'] for f in features)
    assert next(f for f in features if f['feature']=='management_guidance_change')['value'] is None
    assert next(f for f in features if f['feature']=='working_capital_pressure')['missing']
    assert all('model_score' not in f for f in features)


def test_growth_rejects_changed_definition_or_period():
    manifest,refs=dataset()
    feature=build_examples(manifest,refs,decision_at='2026-10-13T00:00:00Z')[0]
    # Build minimal, controlled feature facts to exercise nonmatching periods.
    a={'reference_id':'A','source':source(),'metric':'order_book','unit':'currency','currency':'INR','entity_scope':None,
       'measurement_kind':'level','value_basis':'ACTUAL','reporting_period':'Q3FY23','normalized_value':'238',
       'book_definition':'awarded','page':1,'source_quote':'CONTROLLED','review_status':'CONTROLLED'}
    b=deepcopy(a);b.update(reference_id='B',reporting_period='Q3FY24',normalized_value='240',book_definition='pipeline')
    assert order_book_growth(a,b,decision_at='2026-10-13T00:00:00Z')['value'] is None
    b['book_definition']='awarded';b['reporting_period']='Q4FY24'
    assert order_book_growth(a,b,decision_at='2026-10-13T00:00:00Z')['value'] is None


def test_scorer_counts_missing_numeric_not_as_fabrication():
    ref={'reference_id':'TEST','window_id':'W','document_key':'D','category':'financial_metrics','metric':'revenue_growth',
         'normalized_value':'18','unit':'percent','currency':None,'statement_kind':'GUIDANCE','value_basis':'PROJECTED',
         'reporting_period':None,'time_horizon':'FY27','speaker_role':None,'entity_scope':None,'required_source_terms':['18%']}
    win={'window_id':'W','document_key':'D','page':1,'source_quote':'We expect revenue growth of 18% for FY27.', 'score_metrics':['revenue_growth']}
    r=record('18');r.evidence=[{'page':1,'quote':win['source_quote']}];r.seal()
    result=score({'D':[r]},{'windows':[win],'references':[ref]})
    assert result['categories']['financial_metrics']=={'tp':1,'fp':0,'fn':0,'predictions':1,'references':1,'precision':1.0,'recall':1.0}
    r.status='AMBIGUOUS'
    result=score({'D':[r]},{'windows':[win],'references':[ref]})
    assert result['categories']['financial_metrics']['fp']==0 and result['categories']['financial_metrics']['fn']==1


def test_reporting_period_normalization_is_not_publication_inference():
    assert period_key('31st March 2026')=='2026-03-31'
    assert period_key("Q1 FY '27")=='q1fy27'
    assert period_key(None) is None
    with pytest.raises(ValueError):public_pit_readiness(source(),'2026-01-12')
