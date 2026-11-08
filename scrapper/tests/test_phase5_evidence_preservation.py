"""Review evidence cannot enter the accepted-claim or historical feature path."""
from copy import deepcopy
from scrapper.tests.test_phase5_document_intelligence import canonical, context
from scrapper.research.document_intelligence.extract import extract_intelligence
from scrapper.research.document_intelligence.evidence import validate_evidence
from scrapper.research.document_intelligence.temporal import eligible_at


def test_partial_rating_preserves_native_evidence_without_an_event():
    text='Liquidity: Adequate\nUpward factors:\nSustained PAT of Rs 5 crore\nDownward factors:\nWithdrawal of unsecured loans weakening\nliquidity\nAbout the Company'
    e=canonical(text);e['extraction_status']='partial'
    rows,_=extract_intelligence(context(text,'credit_rating'),e,focus_disclosures=True)
    review=[r for r in rows if r.dimensions.get('evidence_only')]
    assert review and all(r.status=='AMBIGUOUS' for r in review)
    assert not any(r.status in {'VERIFIED','EVIDENCED_STATEMENT'} for r in rows)
    assert any('weakening\nliquidity' in ev.get('quote','') for r in review for ev in r.evidence)
    assert all(validate_evidence(r,e) and not eligible_at(r,'2026-10-14T00:00:00Z') for r in review)
    corrupted=deepcopy(review[0]);corrupted.evidence[0]['quote']+=' fabricated'
    assert not validate_evidence(corrupted,e)


def test_approval_dependency_retained_without_becoming_adverse_claim():
    text='Scheme, subject to receipt of all approvals, is expected to be consummated by Q2FY21'
    e=canonical(text);rows,_=extract_intelligence(context(text),e,focus_disclosures=True)
    review=[r for r in rows if r.metric=='regulatory_risk' and r.dimensions.get('evidence_only')]
    assert review and all(r.status=='AMBIGUOUS' and r.normalized_value is None for r in review)
    assert all('operational_risk' not in r.dimensions.get('disclosure_categories',[]) for r in review)
    assert not any(r.metric=='regulatory_risk' and r.status in {'VERIFIED','EVIDENCED_STATEMENT'} for r in rows)


def test_narrative_table_is_retained_as_cells_without_scalar_claim():
    text='Project\nEstimated capex INR100 crore and capex incurred INR20 crore'
    e=canonical(text);page=e['pages'][0]
    page['elements'].append(dict(element_position=9,element_kind='table',text_origin='native',text_raw=None,bbox=[0,0,100,100],reference={},rows=[['Project'],['Estimated capex INR100 crore and capex incurred INR20 crore']]))
    before=deepcopy(e);rows,_=extract_intelligence(context(text),e,focus_disclosures=True)
    review=[r for r in rows if r.dimensions.get('evidence_only') and any(ev['kind']=='table_cell' for ev in r.evidence)]
    assert review and e==before
    assert all(r.normalized_value is None and r.status=='AMBIGUOUS' and validate_evidence(r,e) for r in review)
    assert all({v['normalized_value'] for v in r.dimensions['observed_values']}=={'1000000000','200000000'} for r in review)
