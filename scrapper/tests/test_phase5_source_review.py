"""Source retrieval preserves exact evidence and cannot accept claims."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from scrapper.tests.test_phase5_document_intelligence import canonical, context
from scrapper.research.document_intelligence.source_review import SourceIndex, source_units, load_units, render_html
from scrapper.research.document_intelligence.schema import ResearchEvidence
from scrapper.research.document_intelligence.evidence import validate_evidence
from scrapper.research.document_intelligence.temporal import eligible_at


@pytest.mark.parametrize('origin', ['native', 'ocr'])
def test_search_across_blocks_partial_and_ocr_without_claims(origin):
    text = 'Current capacity 5 mtpa\nUnrelated column\nPotential to expand to 19 mtpa'
    e = canonical(text, origin=origin, status='partial'); before = deepcopy(e)
    units = list(source_units(context(text), e, Path('/private/tmp/original.pdf')))
    index = SourceIndex(units)
    try:
        hit = index.search('expand 19')[0]
        assert hit['record']['evidence'][0]['quote'] == text
        assert index.search(category='capex_capacity')
        assert hit['disclosure_category'] is None
        r = ResearchEvidence(**{k: v for k, v in hit['record'].items() if k != 'disclosure_status'})
        assert validate_evidence(r, e) and not eligible_at(r, '2026-10-15T00:00:00Z')
        assert r.status == 'AMBIGUOUS' and r.normalized_value is None and r.dimensions['evidence_only']
        assert ('ocr_transcription_requires_original_page_review' in r.reasons) == (origin == 'ocr')
        r.evidence[0]['start'] += 1
        assert not validate_evidence(r, e)
        assert e == before
    finally:
        index.close()


def test_original_cells_and_sparse_chart_preserved(tmp_path):
    e = canonical('Slide'); p = e['pages'][0]
    p['elements'] += [dict(element_position=9, element_kind='table', text_origin='native', rows=[['Capex', 'INR20 crore']], reference={}, bbox=None),
        dict(element_position=10, element_kind='chart', text_origin='native', text_raw=None, bbox=None,
            reference={'title_text':'Order book', 'embedded_series':[{'name':'Reported','categories':['FY25','FY26'],'values':['5',None]}]})]
    units = list(source_units(context('Slide'), e, tmp_path / 'original.pdf'))
    index = SourceIndex(units)
    try:
        table = index.search('capex')[0]
        assert [x['quote'] for x in table['record']['evidence']] == ['Capex','INR20 crore']
        chart = index.search('order book')[0]
        assert chart['kind'] == 'chart' and chart['record']['evidence'][1]['quote'] == '5'
        assert len(chart['record']['evidence']) == 2
        render_html([table, chart], tmp_path / 'review.html')
        assert 'original.pdf#page=1' in (tmp_path / 'review.html').read_text()
    finally:
        index.close()


def test_literal_query_does_not_execute_fts_or_html(tmp_path):
    text = 'Risk <script>alert(1)</script> capacity'
    index = SourceIndex(source_units(context(text), canonical(text), tmp_path / 'original.pdf'))
    try:
        assert not index.search('risk OR nonexistent')
        render_html(index.search('risk'), tmp_path / 'review.html')
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in (tmp_path / 'review.html').read_text()
        with pytest.raises(ValueError):
            index.search('risk', limit=201)
    finally:
        index.close()


def test_manifest_hash_tampering_rejected(tmp_path):
    import hashlib
    original = tmp_path / 'a.pdf'; original.write_bytes(b'pdf')
    retained = tmp_path / 'a.json'; retained.write_text('{}')
    m = {'documents':[dict(path='a.pdf', canonical_path='a.json', content_sha256=hashlib.sha256(b'pdf').hexdigest(), canonical_file_sha256='0'*64)]}
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(m))
    with pytest.raises(ValueError, match='Canonical file hash'):
        list(load_units(path))
