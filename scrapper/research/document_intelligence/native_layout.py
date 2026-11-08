"""Bounded offline enrichment from original PDF bytes, preserving every source ID.

No OCR and no rewritten cells. Missing borders remain missing: native word spans
can be related to an existing table column, but are never manufactured cells.
"""
from copy import deepcopy
import hashlib
import io


def enrich_native_layout(extraction, body, *, content_sha256, pages):
    if hashlib.sha256(body).hexdigest() != content_sha256:
        raise ValueError('original_content_hash_mismatch')
    import pdfplumber
    from ...documents.extraction import normalize_extracted_text
    result = deepcopy(extraction)
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in result['pages']:
            pn = page['page_or_slide_number']
            if pn not in pages:
                continue
            source = pdf.pages[pn-1]
            text = normalize_extracted_text(source.extract_text(x_tolerance=2, y_tolerance=3, layout=False))
            if text != page['native_text']:
                raise ValueError('canonical_native_text_mismatch')
            words = source.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False, use_text_flow=True)
            lines = {}
            for word in words:
                lines.setdefault(round(float(word['top']), 1), []).append(word)
            native = {}
            for line in lines.values():
                line.sort(key=lambda w: (float(w['x0']), float(w['x1'])))
                raw = ' '.join(str(w['text']) for w in line).strip()
                box = [round(float(v), 3) for v in (min(w['x0'] for w in line), min(w['top'] for w in line), max(w['x1'] for w in line), max(w['bottom'] for w in line))]
                spans = [];offset = 0
                for word in line:
                    token = str(word['text']);start = raw.find(token, offset)
                    if start < 0:
                        raise ValueError('native_word_span_mismatch')
                    spans.append({'start': start, 'end': start+len(token), 'text': token,
                                  'bbox': [round(float(word[k]), 3) for k in ('x0','top','x1','bottom')]})
                    offset = start+len(token)
                native[(raw, tuple(box))] = spans
            tables = source.find_tables()
            for element in page['elements']:
                if element['text_origin'] != 'native':
                    continue
                reference = dict(element.get('reference') or {})
                if element.get('text_raw'):
                    spans = native.get((element['text_raw'], tuple(element.get('bbox') or [])))
                    if spans:
                        reference['native_words'] = spans
                if element['element_kind'] == 'table':
                    ti = reference.get('table_index')
                    if ti is not None:
                        table = tables[ti]
                        rows = [[normalize_extracted_text(c) for c in row] for row in table.extract()]
                        if rows != element['rows']:
                            raise ValueError('canonical_table_cells_mismatch')
                        reference['cell_bboxes'] = [[([round(float(v),3) for v in box] if box else None) for box in row.cells] for row in table.rows]
                if reference:
                    reference['native_layout_sha256'] = content_sha256
                    element['reference'] = reference
    return result
