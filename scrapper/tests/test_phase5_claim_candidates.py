"""Pointer contract tests; synthetic and known sources only, never holdout gold."""
from copy import deepcopy
import json
import pytest
from scrapper.tests.test_phase5_document_intelligence import canonical, context
from scrapper.research.document_intelligence.claim_candidates import build_packet, candidate_schema, canonical_period, deterministic_proposals, qualify
from scrapper.research.document_intelligence.evidence import validate_evidence


def sample(text='Revenue was INR30 crore in FY26 on a consolidated basis.'):
    data=canonical(text);ctx=context(text)
    packet=build_packet(ctx,data,1)
    return ctx,data,packet,deterministic_proposals(packet)


def test_exact_original_evidence_and_decimal_normalization():
    ctx,data,p,g=sample();out,rej=qualify(ctx,data,p,g,method='test')
    assert len(out)==1 and not rej
    r=out[0];assert r.normalized_value=='300000000' and r.reporting_period=='FY26'
    assert r.dimensions['entity_scope']=='consolidated' and validate_evidence(r,data)
    assert r.evidence[0]['kind']=='element_text' and r.evidence[0]['start']==0

@pytest.mark.parametrize('mutate',[
    lambda c:c.update(value='invented'),lambda c:c.update(metric='invented'),
    lambda c:c.update(normalized_value='1'),lambda c:c.update(period='invented'),
    lambda c:c.update(scope='invented'),lambda c:c.update(measurement='invented'),
])
def test_no_generated_amounts_enums_or_forged_ids(mutate):
    ctx,data,p,g=sample();mutate(g['claims'][0]);out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej


def test_cross_frame_value_rejected():
    ctx,data,p,g=sample('Revenue was INR30 crore in FY26.\nNet Income was INR10 crore in FY26.')
    g['claims'][0]['frame']=g['claims'][1]['frame']
    out,rej=qualify(ctx,data,p,{'claims':[g['claims'][0]]},method='test')
    assert not out and rej[0]['reason']=='cross_frame_value'


def test_multiyear_comparison_requires_unique_value_period_anchor():
    ctx,data,p,g=sample('Revenue was INR30 crore in FY26 compared to INR20 crore in FY25.')
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and all(r['reason']=='multiple_periods_without_unique_value_anchor' for r in rej)


def test_unknown_transcript_speaker_fails_closed():
    ctx,data,p,g=sample();ctx=context(kind='transcript')
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej[0]['reason']=='management_identity_not_grounded'


def test_ocr_boxes_are_not_attestations():
    ctx,data,p,g=sample();ocr={'text':data['pages'][0]['selected_text'],'bbox':[0,0,100,100],'origin':'local-model'}
    p=build_packet(ctx,data,1,ocr_atoms=[ocr]);assert p['ocr_rejected']==1 and not p['corroborated_ocr']
    data['pages'][0]['elements'][0]['bbox']=[0,0,100,100]
    p=build_packet(ctx,data,1,ocr_atoms=[ocr]);assert p['ocr_rejected']==0 and len(p['corroborated_ocr'])==1
    ocr['text']='Revenue was INR99 crore in FY26.'
    assert build_packet(ctx,data,1,ocr_atoms=[ocr])['ocr_rejected']==1


def test_mutated_native_evidence_fails_existing_strict_verifier():
    ctx,data,p,g=sample();p['atoms'][next(iter(p['atoms']))]['evidence']['quote']='fabricated'
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej[0]['reason']=='strict_evidence_or_decimal_verifier_failed'


def test_decline_does_not_manufacture_a_sign():
    ctx,data,p,g=sample('Revenue growth declined by 13.1% in FY26.')
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej


def test_fiscal_range_uses_end_year_and_schema_is_ids_only():
    assert canonical_period("FY'21-22")=='FY22'
    assert canonical_period("FY'21-25") is None
    _,_,p,_=sample();s=candidate_schema(p)
    assert s['properties']['claims']['items']['additionalProperties'] is False
    assert 'normalized_value' not in s['properties']['claims']['items']['properties']


def test_no_period_inherited_from_other_presentation_page():
    ctx,data,p,g=sample('FY26\nRevenue was INR30 crore.')
    data['pages'][0]['elements']=data['pages'][0]['elements'][1:]
    data['pages'][0]['selected_text']='Revenue was INR30 crore.'
    data['pages'][0]['native_text']='Revenue was INR30 crore.'
    p=build_packet(ctx,data,1);out,rej=qualify(ctx,data,p,deterministic_proposals(p),method='test')
    assert not out and rej[0]['reason']=='missing_explicit_financial_period'


def test_same_page_conflicting_years_cannot_anchor_an_unlabelled_chart_value():
    ctx,data,p,g=sample('FY25\nFY26\nRevenue was INR30 crore.')
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej[0]['reason']=='missing_explicit_financial_period'


def test_commissioning_year_cannot_be_relabelled_spending_horizon():
    ctx,data,p,g=sample('We expect capex of INR30 crore for commissioning in FY29.')
    c=g['claims'][0];c['horizon']=c['period'];c['period']=None
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej[0]['reason']=='commissioning_is_not_expenditure_horizon'


def test_true_table_cells_keep_their_own_period_column_and_unit_header():
    from scrapper.documents.extraction import DocumentElement
    table=DocumentElement(0,'table','native',rows=(('INR crore','FY25','FY26'),('Revenue','20','30')))
    data=canonical('INR crore FY25 FY26 Revenue 20 30',elements=(table,));ctx=context()
    p=build_packet(ctx,data,1);g=deterministic_proposals(p)
    assert len(g['claims'])==2
    for c in g['claims']:
        v=p['values'][c['value']]
        assert len(v['column_period_ids'])==1
        wrong=next(pid for pid,pd in p['periods'].items() if pd['period']!=p['periods'][c['period']]['period'])
        forged=deepcopy(c);forged['period']=wrong
        out,rej=qualify(ctx,data,p,{'claims':[forged]},method='test')
        assert not out and rej[0]['reason']=='period_not_local_or_unique_context'
    out,rej=qualify(ctx,data,p,g,method='test')
    assert len(out)==2 and not rej
    assert {(r.normalized_value,r.reporting_period) for r in out}=={('200000000','FY25'),('300000000','FY26')}
    assert all(validate_evidence(r,data) for r in out)
    assert all(any(e['kind']=='table_cell' for e in r.evidence) for r in out)


def test_inline_named_management_heading_reuses_grounded_roster():
    text='Mr. Ravi Kumar -- CEO\nRavi Kumar: Revenue was INR30 crore in FY26.'
    ctx,data,p,g=sample(text);ctx=context(text,kind='transcript');p=build_packet(ctx,data,1)
    out,rej=qualify(ctx,data,p,deterministic_proposals(p),method='test')
    assert len(out)==1 and not rej and out[0].speaker['name']=='Ravi Kumar'
    assert any(e['quote']=='Ravi Kumar:' for e in out[0].evidence)


def test_historical_claim_cannot_have_generated_future_horizon():
    ctx,data,p,g=sample();g['claims'][0]['horizon']=g['claims'][0]['period']
    out,rej=qualify(ctx,data,p,g,method='test')
    assert not out and rej[0]['reason']=='historical_claim_has_forecast_horizon'


def test_page_wide_scope_is_not_borrowed_by_unrelated_frame():
    ctx,data,p,g=sample('At consolidated level.\nRevenue was INR30 crore in FY26.')
    p=build_packet(ctx,data,1)
    assert all(not f['scope_ids'] for f in p['frames'].values() if 'Revenue' in f['text'])


def test_post_holdout_capri_period_and_scope_failure_is_now_quarantined():
    """Known failure diagnostic; never an unseen-validation result."""
    from pathlib import Path
    from scrapper.research.document_intelligence.evaluation import context_for
    root=Path(__file__).resolve().parents[2]/'artifacts/phase5/bottleneck/v4'
    manifest=json.loads((root/'holdout/inputs/manifest.json').read_text())
    row=next(r for r in manifest['documents'] if r['key']=='capri')
    data=json.loads((root/'final-prepared/canonical/capri.json').read_text())
    ctx=context_for(row);p=build_packet(ctx,data,7)
    proposals=deterministic_proposals(p)
    out,rej=qualify(ctx,data,p,proposals,method='post-holdout-safety-diagnostic')
    assert not any(r.metric=='leverage' and r.original_value=='2.8x' for r in out)
    assert any(x['reason']=='period_anchor_shared_with_another_numeric_event' for x in rej)
