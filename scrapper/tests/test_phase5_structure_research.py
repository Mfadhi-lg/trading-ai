"""Small offline structure contract; no frozen holdout predictions as fixtures."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from scrapper.tests.test_phase5_document_intelligence import canonical,context
from scrapper.research.document_intelligence.claim_candidates import build_packet,deterministic_proposals,qualify
from scrapper.research.document_intelligence.structure_research import ground_cells,packet_from_cells,embedded_chart_parts,vector_chart_labels
from scrapper.research.document_intelligence.evidence import validate_evidence


def fixture():
    data=canonical('INR crore FY26\nRevenue 20')
    lines=[('INR crore FY26',[('INR',0,3,[0,0,12,10]),('crore',4,9,[13,0,30,10]),('FY26',10,14,[50,0,70,10])]),('Revenue 20',[('Revenue',0,7,[0,20,35,30]),('20',8,10,[50,20,60,30])])]
    for e,(text,words) in zip(data['pages'][0]['elements'],lines):
        e['bbox']=[0,words[0][3][1],75,words[0][3][3]];e['reference']={'native_words':[{'text':t,'start':s,'end':en,'bbox':b} for t,s,en,b in words]}
    p=build_packet(context(),data,1)
    cells=[{'row':r,'column':c,'bbox':[x,y,x+40,y+12]} for r,y in enumerate([0,20]) for c,x in enumerate([0,45])]
    return data,p,cells


def test_model_cell_text_is_never_promoted_to_native_evidence():
    data,p,cells=fixture();cells[-1]['text']='999'
    q,grounded=packet_from_cells(p,cells,[0,0,90,40],method='synthetic')
    out,rej=qualify(context(),data,q,deterministic_proposals(q),method='test')
    assert not rej and len(out)==1 and out[0].original_value=='20'
    assert out[0].reporting_period=='FY26' and validate_evidence(out[0],data)
    assert all(e['kind']!='table_cell' for e in out[0].evidence)


def test_overlapping_model_cells_quarantine_shared_words():
    _,p,cells=fixture();cells.append(deepcopy(cells[-1]));grounded,rejected=ground_cells(p,cells)
    assert any(x['reason']=='overlapping_structure_cells' for x in rejected)
    assert all('20' not in c['text'] for c in grounded)


def test_sparse_embedded_chart_missing_values_are_not_zero_filled():
    body=BytesIO()
    with ZipFile(body,'w') as z:z.writestr('ppt/charts/chart1.xml','''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:plotArea><c:barChart><c:ser><c:cat><c:strLit><c:pt idx="0"><c:v>FY25</c:v></c:pt><c:pt idx="1"><c:v>FY26</c:v></c:pt></c:strLit></c:cat><c:val><c:numLit><c:ptCount val="2"/><c:pt idx="1"><c:v>20</c:v></c:pt></c:numLit></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>''')
    charts=embedded_chart_parts(body.getvalue(),['ppt/charts/chart1.xml']);points=charts[0]['series'][0]['points']
    assert points[0]['value'] is None and points[0]['numeric_class']=='unsupported_missing'
    assert points[1]['value']=='20' and points[1]['numeric_class']=='exact_embedded'
    assert charts[0]['title']==[] and charts[0]['axes']==[]


def test_unlabelled_bar_has_no_invented_height_value_or_axis():
    _,p,_=fixture()
    result=vector_chart_labels(p,{'rects':[{'x0':45,'top':12,'x1':65,'bottom':25,'width':20,'height':13,'fill':True,'non_stroking_color':(1,0,0)}]},[0,0,90,40])
    assert result['points']==[] and result['estimated_values']==[] and result['y_axis'] is None


def test_explicit_plural_crore_source_alias_keeps_decimal_and_currency():
    from scrapper.research.document_intelligence.numbers import normalize_value
    assert normalize_value('Rs. 1.25 Crs.')['normalized_value']=='12500000.00'
    assert normalize_value('(2)',unit_hint='₹ Crs.')['normalized_value']=='-20000000'
    assert normalize_value('20 Crs.')['currency'] is None
    assert normalize_value('20 Crsiness') is None


def test_explicit_financial_scope_heading_is_linked_to_unique_table():
    from scrapper.documents.extraction import DocumentElement
    data=canonical('Standalone Profit & Loss\nRs. Crs FY26 Revenue 20',elements=(
        DocumentElement(0,'text_block','native',text_raw='Standalone Profit & Loss',bbox=(0,10,200,30)),
        DocumentElement(1,'table','native',rows=(('Rs. Crs','FY26'),('Revenue','20')),bbox=(0,50,300,100)),))
    p=build_packet(context(),data,1);out,rej=qualify(context(),data,p,deterministic_proposals(p),method='test')
    assert not rej and len(out)==1 and out[0].dimensions['entity_scope']=='standalone'
    assert validate_evidence(out[0],data)
    assert any(e['quote']=='Standalone Profit & Loss' for e in out[0].evidence)
