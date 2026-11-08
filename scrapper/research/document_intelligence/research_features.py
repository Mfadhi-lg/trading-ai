"""Evidence-preserving research features with a strict public-PIT admission gate.

Source-reviewed examples and automatic extractor proposals are different inputs.
No feature assigns a numerical severity to qualitative text. Unknown conditions
produce None plus reasons, never zero. No DB or scheduler integration exists.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import re

from ...documents.availability import parse_utc

FEATURE_VERSION='phase5-research-features-1.0.0'


def public_pit_readiness(source, decision_at, *, effective_date=None, current_rating=False):
    """A capture boundary alone is insufficient for public historical backtests."""
    cutoff=parse_utc(decision_at)
    reasons=[]
    if source.get('company_id') is None or not source.get('content_sha256') or not source.get('version'):
        reasons.append('canonical_identity_or_version_missing')
    publication=source.get('publication_at')
    provenance=source.get('publication_evidence')
    if not publication:
        reasons.append('public_version_timestamp_unknown')
    elif parse_utc(publication)>cutoff:
        reasons.append('public_version_is_after_cutoff')
    if (not provenance or not provenance.get('source_reference') or
            provenance.get('basis') not in {'exchange_publication_timestamp','issuer_publication_timestamp','agency_publication_timestamp'} or
            provenance.get('validated') is not True or provenance.get('timestamp_matches_source') is not True or
            provenance.get('content_sha256')!=source.get('content_sha256')):
        reasons.append('public_timestamp_provenance_not_verified')
    capture=source.get('captured_at')
    if not capture:
        reasons.append('local_capture_timestamp_unknown')
    elif parse_utc(capture)>cutoff:
        reasons.append('version_not_captured_by_cutoff')
    if source.get('ingested_at') and parse_utc(source['ingested_at'])>cutoff:
        reasons.append('ingestion_after_cutoff')
    if current_rating:
        # Event announcement availability is different from grade effectiveness.
        # Never infer current rating from a single historical observation.
        reasons.append('current_rating_requires_complete_later_event_check')
        if not effective_date:
            reasons.append('effective_rating_date_unknown')
        elif effective_date>=cutoff.date().isoformat():
            reasons.append('rating_effect_not_established_before_cutoff_day')
    return {'eligible':not reasons,'reasons':reasons,'decision_at':decision_at,
            'availability_basis':'public_version_provenance_and_capture_required',
            'effective_date':effective_date,'current_rating_claim':current_rating}


def qualified_fact(record, *, source_validation, required_scope=False):
    """Automatic proposals need semantic review, period and exact evidence checks."""
    reasons=[]
    if record.status!='VERIFIED':
        reasons.append('not_verified_numeric_or_event_extraction')
    if not source_validation.get('exact_citation_valid'):
        reasons.append('citation_not_validated')
    if not source_validation.get('semantic_match'):
        reasons.append('semantic_entailment_not_validated')
    if not record.reporting_period and record.statement_kind=='HISTORICAL_FACT':
        reasons.append('fact_reporting_period_unknown')
    if record.value_basis not in {'ACTUAL','PROJECTED','CONDITIONAL'}:
        reasons.append('value_basis_unknown')
    if required_scope and not record.dimensions.get('entity_scope'):
        reasons.append('entity_scope_not_established')
    if record.metric=='rating_action' and record.effective_date is None:
        reasons.append('effective_rating_date_unknown')
    return {'qualified':not reasons,'reasons':reasons,'record_id':record.record_id}


def _feature(name, inputs, value, method, *, reasons=(), decision_at, parameters=None):
    references=[f['reference_id'] for f in inputs]
    evidence=[{'reference_id':f['reference_id'],'document_id':f['source']['document_id'],
               'content_sha256':f['source']['content_sha256'],'version':f['source']['version'],
               'page':f['page'],'source_quote':f['source_quote'],'review_status':f['review_status']} for f in inputs]
    pit=[public_pit_readiness(f['source'],decision_at) for f in inputs]
    ready=bool(inputs) and all(p['eligible'] for p in pit)
    result={'feature':name,'feature_version':FEATURE_VERSION,'value':value,'missing':value is None,
            'status':'UNKNOWN' if value is None else 'SOURCE_CHECKED_RESEARCH_ONLY',
            'method':method,'parameters':parameters or {},'input_reference_ids':references,
            'evidence':evidence,'reasons':list(reasons),'public_pit_eligible':ready,
            'pit_checks':pit,'decision_at':decision_at,
            'automatic_extractor_feature':False,'human_adjudication':'PENDING',
            'ml_admission':'BLOCKED_PENDING_PUBLIC_PIT_AND_INDEPENDENT_LABEL_REVIEW'}
    result['feature_id']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def _comparable(previous,current):
    checks=['company_id']
    if any(previous['source'].get(k)!=current['source'].get(k) for k in checks):
        return False
    return (previous['source'].get('company_id') is not None and
            previous['metric']==current['metric'] and previous['unit']==current['unit'] and
            previous['currency']==current['currency'] and previous.get('entity_scope')==current.get('entity_scope') and
            previous['measurement_kind']==current['measurement_kind'])


def order_book_growth(previous,current,*,decision_at):
    inputs=[previous,current]
    if (not _comparable(previous,current) or previous['value_basis']!='ACTUAL' or current['value_basis']!='ACTUAL'
            or not previous['reporting_period'] or not current['reporting_period']
            or previous.get('book_definition')!=current.get('book_definition') or not current.get('book_definition')):
        return _feature('order_book_growth',inputs,None,'(current/previous-1)*100',reasons=['incomparable_period_scope_or_book_definition'],decision_at=decision_at)
    # The real example is Q3FY23 -> Q3FY24. No FY label is converted into a
    # publication date; period ordering is separate from public availability.
    old=re.fullmatch(r'Q([1-4])FY(\d{2,4})',previous['reporting_period'])
    new=re.fullmatch(r'Q([1-4])FY(\d{2,4})',current['reporting_period'])
    if not old or not new or old[1]!=new[1] or int(old[2])>=int(new[2]):
        return _feature('order_book_growth',inputs,None,'(current/previous-1)*100',reasons=['periods_not_chronological_same_quarter'],decision_at=decision_at)
    denominator=Decimal(previous['normalized_value'])
    if denominator<=0:
        return _feature('order_book_growth',inputs,None,'(current/previous-1)*100',reasons=['nonpositive_denominator'],decision_at=decision_at)
    with localcontext() as ctx:
        ctx.prec=28
        value=format(((Decimal(current['normalized_value'])/denominator-1)*100).quantize(Decimal('0.000000000001')),'f')
    return _feature('order_book_growth',inputs,value,'(current/previous-1)*100',decision_at=decision_at,
                    parameters={'unit':'percent','rounding':'12 decimal places, Decimal half-even','periods':[previous['reporting_period'],current['reporting_period']],
                                'book_definition':current['book_definition']})


def declared_event(name,fact,*,value,method,decision_at,expected_kind=None,parameters=None):
    if expected_kind and fact['statement_kind']!=expected_kind:
        return _feature(name,[fact],None,method,reasons=['statement_kind_mismatch'],decision_at=decision_at)
    return _feature(name,[fact],value,method,decision_at=decision_at,parameters=parameters)


def build_examples(manifest,reference_data,*,decision_at):
    rows={r['key']:r for r in manifest['documents']}
    windows={w['window_id']:w for w in reference_data['windows']}
    facts=[]
    for ref in reference_data['references']:
        row=rows[ref['document_key']]
        source={k:row.get(k) for k in ['document_id','content_sha256','version','company_id','company_name','symbol','publication_at','captured_at','ingested_at','source_url','provenance']}
        facts.append({**ref,'source':source,'source_quote':windows[ref['window_id']]['source_quote'],
                      'book_definition':'discounted_awarded_program_revenue_next_10_years' if ref['document_key'].startswith('sona_fy') and ref['metric']=='order_book' else None,
                      'review_status':ref['review_status']})
    def one(doc,metric,value=None,kind=None):
        return next(f for f in facts if f['document_key']==doc and f['metric']==metric and
                    (value is None or f['normalized_value']==value) and (kind is None or f['statement_kind']==kind))
    result=[]
    old=one('sona_fy23','order_book','238000000000')
    new=one('sona_fy24','order_book','240000000000')
    result.append(order_book_growth(old,new,decision_at=decision_at))
    ratio=one('sona_fy24','order_book_ratio')
    result.append(_feature('order_book_to_revenue_ratio',[ratio],ratio['normalized_value'],'direct_reported_ratio',decision_at=decision_at,
                           parameters={'unit':'ratio','numerator_as_of':'Q3FY24','denominator_period':'FY23','not_recomputed_from_mismatched_quarter_revenue':True}))
    capex=one('vimta_call','capex')
    result.append(declared_event('capex_expansion_announcement',capex,value={'event':'BUDGET_ANNOUNCED','amount_base':capex['normalized_value'],'currency':'INR','horizon':'FY27','approximate':True},method='explicit_budget_statement_no_spent_amount_inference',decision_at=decision_at,expected_kind='GUIDANCE',
                                parameters={'phasing':'business visibility, customer demand and project-level requirements','value_basis':'PROJECTED'}))
    margin=one('welspun','margin_outlook')
    result.append(declared_event('margin_outlook_direction',margin,value='IMPROVEMENT_EXPECTED',method='explicit_improve_further_phrase_no_numeric_score',decision_at=decision_at,expected_kind='GUIDANCE',parameters={'entity_scope':'MEPL','horizon':None,'numeric_margin_target':None}))
    delay=one('dynamic_cables','project_delay')
    result.append(declared_event('execution_risk_indicator',delay,value='DISCLOSED_PROJECT_DELAY',method='explicit_delay_disclosure_no_severity_score',decision_at=decision_at,expected_kind='RISK_WARNING'))
    for doc,action in [('sona_indra','upgraded'),('sarup_brickwork','downgraded')]:
        f=next(f for f in facts if f['document_key']==doc and f['metric']=='rating_action' and f['normalized_value']['action']==action)
        result.append(declared_event('credit_rating_change',f,value=f['normalized_value'],method='explicit_facility_specific_rating_action_not_new_report_inference',decision_at=decision_at,expected_kind='RATING_EVENT',
                                    parameters={'current_rating':None,'effective_rating_date':None,'as_of_status':'historical_report_event_with_unknown_effective_date'}))
    guide=one('dynamic_cables','growth_guidance')
    result.append(_feature('management_guidance_change',[guide],None,'two_sided_same_metric_horizon_scope_public_chronology',reasons=['no_independent_earlier_matching_statement','public_statement_times_unknown'],decision_at=decision_at))
    liquidity=one('vimta_care','liquidity')
    result.append(_feature('liquidity_direction',[liquidity],None,'same_company_same_agency_explicit_comparable_assessments',reasons=['no_comparable_prior_liquidity_assessment','no_ordinal_score_for_strong_or_adequate'],decision_at=decision_at))
    wc=one('vimta_care','working_capital')
    result.append(_feature('working_capital_pressure',[wc],None,'explicit_pressure_disclosure_required',reasons=['negligible_utilisation_is_not_pressure','partial_reference_set_cannot_prove_company_wide_absence'],decision_at=decision_at))
    result.append(_feature('demand_risk_indicator',[],None,'explicit_source_warning_required_no_sentiment_score',reasons=['no_adjudicated_demand_warning_in_this_reference_subset'],decision_at=decision_at))
    return result
