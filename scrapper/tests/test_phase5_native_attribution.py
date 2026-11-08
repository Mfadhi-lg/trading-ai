"""Source-structure regressions; frozen unseen labels are not used for development."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from scrapper.documents.extraction import DocumentElement
from scrapper.tests.test_phase5_document_intelligence import canonical,context
from scrapper.research.document_intelligence.claim_candidates import build_packet,deterministic_proposals,qualify
from scrapper.research.document_intelligence.evidence import validate_evidence,reconcile
from scrapper.research.document_intelligence.native_layout import enrich_native_layout


def table_case(rows, *, reference=None, kind='presentation'):
    data=canonical('Source financial table',elements=(DocumentElement(0,'table','native',rows=tuple(tuple(r) for r in rows),reference=reference),))
    ctx=context(kind=kind);p=build_packet(ctx,data,1)
    out,rej=qualify(ctx,data,p,deterministic_proposals(p),method='regression')
    return data,p,out,rej


def test_ambiguous_cell_keeps_neighboring_financial_evidence():
    data,p,out,rej=table_case([['INR crore','FY25','FY26'],['Revenue','20','30'],['Margin','10% and 12%','14%']])
    assert {(r.original_value,r.reporting_period) for r in out}=={('20','FY25'),('30','FY26')}
    assert any(d['reason']=='multiple_values_in_cell_unresolved' for d in p['diagnostics'])
    assert all(validate_evidence(r,data) for r in out)


@pytest.mark.parametrize('unit,raw,value,currency,kind',[
    ('₹ in crore','(2.5)','-25000000.0','INR','currency'),
    ('₹ lakh','1,234.50','123450000.00','INR','currency'),
    ('USD million','2.5','2500000.0','USD','currency'),
    ('percent','12.5','12.5',None,'percent'),
    ('times','2.8','2.8',None,'ratio'),
])
def test_unit_row_headers_override_common_currency(unit,raw,value,currency,kind):
    label='Operating margin' if kind=='percent' else 'Overall gearing' if kind=='ratio' else 'Revenue'
    data,p,out,rej=table_case([['₹ crore','FY26'],[f'{label} ({unit})',raw]])
    assert len(out)==1 and not rej
    assert (out[0].normalized_value,out[0].currency,out[0].unit)==(value,currency,kind)
    assert validate_evidence(out[0],data)


def test_conflicting_units_quarantine_bare_value():
    _,p,out,_=table_case([['INR crore','FY26'],['Revenue (USD million)','20']])
    # Row-level unit takes precedence over a global currency caption.
    assert len(out)==1 and out[0].currency=='USD'
    _,p,out,_=table_case([['INR crore','FY26 USD million'],['Revenue (INR lakh)','20']])
    assert not out and any(d['reason']=='unit_missing_or_conflicting' for d in p['diagnostics'])


def test_hierarchical_year_and_quarter_headers():
    data,p,out,rej=table_case([['INR crore','FY26','FY26'],['','Q1','Q2'],['Revenue','20','30']])
    assert {(r.original_value,r.reporting_period) for r in out}=={('20','Q1FY26'),('30','Q2FY26')}
    assert all(validate_evidence(r,data) for r in out)


def test_conflicting_period_levels_are_rejected():
    _,p,out,rej=table_case([['INR crore','FY25'],['','Q1FY26'],['Revenue','20']])
    assert not out and rej[0]['reason']=='multiple_periods_without_unique_value_anchor'


def test_standalone_consolidated_column_sections_remain_distinct():
    data,p,out,_=table_case([['INR crore','Standalone','Consolidated'],['','FY26','FY26'],['Revenue','20','30']])
    assert {(r.original_value,r.dimensions['entity_scope']) for r in out}=={('20','standalone'),('30','consolidated')}
    assert all(validate_evidence(r,data) for r in out)


def test_named_subsidiary_rows_are_not_assigned_to_issuer():
    data,p,out,rej=table_case([['INR crore','Revenue FY26'],['Example Subsidiary Ltd','20'],['Other Subsidiary Ltd','30']])
    assert not rej and {(r.original_value,r.dimensions['entity_scope']) for r in out}=={('20','Example Subsidiary Ltd'),('30','Other Subsidiary Ltd')}


@pytest.mark.parametrize('heading,kind,basis',[
    ('Actual FY26','HISTORICAL_FACT','ACTUAL'),('Guidance FY27','GUIDANCE','PROJECTED'),
    ('Target FY28','TARGET','PROJECTED'),('May reach FY29','CONDITIONAL_FORECAST','CONDITIONAL')])
def test_statement_types_follow_financial_header(heading,kind,basis):
    data,p,out,rej=table_case([['INR crore',heading],['Revenue','20']])
    assert not rej and len(out)==1
    r=out[0];assert (r.statement_kind,r.value_basis)==(kind,basis)
    assert (r.reporting_period is not None)==(basis=='ACTUAL')
    assert (r.time_horizon is not None)==(basis!='ACTUAL')


def test_shifted_header_needs_cell_geometry():
    rows=[['INR crore','FY26',''],['Revenue','','30']]
    _,p,out,_=table_case(rows)
    assert not out
    geom={'cell_bboxes':[[[0,0,100,20],[100,0,200,20],None],[[0,20,100,40],[100,20,110,40],[110,20,200,40]]]}
    data,p,out,rej=table_case(rows,reference=geom)
    assert not rej and len(out)==1 and out[0].reporting_period=='FY26'
    val=next(e for e in out[0].evidence if e.get('row')==1 and e.get('column')==2)
    assert val['bbox']==[110,20,200,40]
    bad=deepcopy(out[0]);next(e for e in bad.evidence if e==val)['bbox']=[0,0,1,1]
    assert not validate_evidence(bad,data)


def test_repeated_financial_labels_with_different_values_still_reconcile():
    _,_,out,_=table_case([['INR crore','FY26'],['Revenue','20'],['Revenue','30']])
    assert len(out)==2 and all(r.status=='AMBIGUOUS' for r in out)
    _,_,out,_=table_case([['INR crore','FY26'],['Revenue from Operations','20'],['Total Income','30']])
    assert len(out)==2 and all(r.status=='VERIFIED' for r in out)


def test_missing_units_or_nonfinancial_dates_do_not_become_facts():
    assert not table_case([['Particulars','FY26'],['Revenue','30']])[2]
    assert not table_case([['INR crore','December 31, 2025'],['Revenue','30']])[2]


def known_document(key,pn):
    root=Path(__file__).resolve().parents[2]/'artifacts/phase5/bottleneck/v4'
    manifest=json.loads((root/'holdout/inputs/manifest.json').read_text());row=next(r for r in manifest['documents'] if r['key']==key)
    original=json.loads((root/f'final-prepared/canonical/{key}.json').read_text());body=(root/'holdout/inputs'/row['path']).read_bytes()
    data=enrich_native_layout(original,body,content_sha256=row['content_sha256'],pages={pn})
    from scrapper.research.document_intelligence.evaluation import context_for
    ctx=context_for(row);p=build_packet(ctx,data,pn);out,rej=qualify(ctx,data,p,deterministic_proposals(p),method='known-source-regression')
    return original,data,p,out,rej


def test_genuine_protean_missing_borders_use_original_word_spans():
    original,data,p,out,_=known_document('protean',19)
    claims=[r for r in out if r.metric=='revenue' and r.dimensions.get('row_label')=='Revenue from Operations']
    assert {(r.original_value,r.reporting_period) for r in claims}=={('308','Q4FY26'),('222','Q4FY25'),('229','Q3FY26'),('998','FY26'),('841','FY25')}
    for r in claims:
        assert r.status=='VERIFIED' and validate_evidence(r,data)
        assert r.dimensions['financial_relationship']['source_kind']=='element_text'
    assert original['pages'][18]['elements'][5]['rows']==data['pages'][18]['elements'][5]['rows']
    assert data['pages'][18]['elements'][5]['rows'][1][1]==''


def test_genuine_tata_detached_row_and_entity_headers():
    _,data,p,out,_=known_document('tata_consumer',7)
    claims=[r for r in out if r.metric=='revenue' and r.unit=='currency']
    assert {(r.original_value,r.dimensions.get('entity_scope')) for r in claims}=={('3,811','India Beverages'),('2,149','India Foods'),('940','US Coffee'),('1,506','International Tea'),('767','Tata Coffee (incl. Vietnam)'),('9,250','consolidated')}
    assert all(r.reporting_period=='9MFY22' and validate_evidence(r,data) for r in claims)
    assert any(d['reason']=='multiple_values_in_cell_unresolved' for d in p['diagnostics'])


def test_genuine_care_scope_and_comparative_periods():
    for key,scope,values in [('ceat','consolidated',{'635.28','471.37','298.01'}),('dhampur_bio','standalone',{'49','12','-35'})]:
        _,data,p,out,_=known_document(key,4);claims=[r for r in out if r.metric=='profit']
        assert len(claims)==3 and {r.original_value for r in claims}==values
        assert {r.reporting_period for r in claims}=={'FY24','FY25','H1FY26'}
        assert all(r.dimensions['entity_scope']==scope and validate_evidence(r,data) for r in claims)


def test_original_hash_mismatch_fails_closed():
    with pytest.raises(ValueError,match='original_content_hash_mismatch'):
        enrich_native_layout({},b'wrong',content_sha256='0'*64,pages={1})


def test_contradictory_scope_headers_are_quarantined():
    for rows in [
        [['INR crore','Standalone FY26'],['','Consolidated'],['Revenue','20']],
        [['INR crore','Standalone and consolidated FY26'],['Revenue','20']],
    ]:
        _,p,out,_=table_case(rows)
        assert not out
        assert any(d['reason']=='conflicting_scope_headers' for d in p['selection_rejections'])
