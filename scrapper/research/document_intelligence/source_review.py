"""Bounded offline canonical-source search. Hits are never financial claims.

SQLite FTS5 is disposable and in memory. Page hits preserve reading order and
context without claiming that adjacent numbers, headings or columns are related.
Original table cells and supported chart references remain independently visible.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote

from .evidence import canonical_hash, text_evidence, cell_evidence, element_evidence, validate_evidence
from .numbers import values_in
from .offline import _input_path, deny_network
from .schema import DocumentContext, ResearchEvidence

# Topic searches, not financial relationship or adverse-polarity classifiers.
# Deliberately generic vocabulary; no issuer names or specific event rules.
TOPICS = {
    'order_book': '"order book" OR "order inflow" OR "order intake" OR "orders received" OR backlog',
    'management_guidance': 'guidance OR outlook OR forecast OR target OR expects OR expected',
    'capex_capacity': 'capex OR "capital expenditure" OR capacity OR expansion OR commissioning',
    'credit_rating': 'rating OR liquidity OR solvency OR leverage OR debt OR "cash flow"',
    'operational_risk': 'risk OR uncertainty OR crisis OR freight OR disruption OR slowdown OR shortage OR challenge OR constraints',
}


def source_units(context, extraction, original):
    """Index all selected native/OCR pages, exact table rows and chart metadata.

    Full pages are explicit context containers, not synthetic cross-block claims.
    A chart image with no searchable metadata cannot supply searchable numbers.
    """
    source = context.to_dict()
    source['canonical_extraction_sha256'] = canonical_hash(extraction)
    for page in extraction['pages']:
        units = []
        if (page.get('selected_text') or '').strip():
            units.append(('page', page['selected_text'], [text_evidence(page, page['selected_text'])]))
        for element in page['elements']:
            if element['element_kind'] == 'table':
                for ri, row in enumerate(element.get('rows') or []):
                    ev = [cell_evidence(page, element, ri, ci) for ci, value in enumerate(row) if value.strip()]
                    if ev:
                        units.append(('table_row', '\n'.join(e['quote'] for e in ev), ev))
            elif element['element_kind'] == 'chart':
                ref = element.get('reference') or {}
                text = ' '.join(str(v) for v in [element.get('text_raw'), ref.get('title_text')]
                                if v)
                ev = [element_evidence(page, element)]
                for si, series in enumerate(ref.get('embedded_series') or []):
                    text += ' ' + str(series.get('name') or '')
                    for ci, (period, value) in enumerate(zip(series['categories'], series['values'])):
                        if value is None:
                            continue
                        text += ' ' + str(period or '') + ' ' + str(value)
                        ev.append(dict(page=page['page_or_slide_number'], element=element['element_position'],
                            kind='chart_cell', series=si, category=ci, quote=value, period=period,
                            series_name=series['name'], title=ref.get('title_text'), text_origin='native'))
                # Retain even unsearchable charts for direct browsing.
                units.append(('chart', text, ev))
        for kind, text, evidence in units:
            record = ResearchEvidence(source=dict(source), metric='source_review', statement_kind='UNKNOWN',
                status='AMBIGUOUS', evidence=evidence, parser_version='phase5-source-review-1.0.0',
                dimensions={'evidence_only': True}, reasons=['search_hit_is_not_a_financial_claim',
                'metric_value_scope_time_and_speaker_relationships_not_established',
                'surrounding_page_context_does_not_bind_columns_or_statements'])
            if any(e.get('text_origin') == 'ocr' for e in evidence):
                record.reasons.append('ocr_transcription_requires_original_page_review')
            if not validate_evidence(record, extraction):
                raise ValueError('Invalid canonical source locator')
            yield dict(record=record.to_dict(), kind=kind, search_text=text,
                disclosure_category=None, category_status='UNESTABLISHED',
                original_document=str(original), original_page=page['page_or_slide_number'],
                source_href=original.as_uri() + '#page=' + str(page['page_or_slide_number']),
                extraction_status=extraction.get('extraction_status'),
                observed_values=[dict(original=m.group(), **n) for m, n in values_in(text)],
                observation_warning='Unbound source observations; no financial relationships asserted')


class SourceIndex:
    def __init__(self, units):
        self.units = list(units)
        self.db = sqlite3.connect(':memory:')
        try:
            self.db.execute('CREATE VIRTUAL TABLE passages USING fts5(body, tokenize="unicode61")')
            self.db.executemany('INSERT INTO passages(rowid,body) VALUES (?,?)',
                ((i + 1, u['search_text']) for i, u in enumerate(self.units)))
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def search(self, query=None, *, category=None, limit=20):
        if not 1 <= limit <= 200:
            raise ValueError('Search limit must be 1–200')
        if category is not None and category not in TOPICS:
            raise ValueError('Unknown disclosure topic')
        if query is not None:
            tokens = re.findall(r'\w+', query, re.UNICODE)
            if not tokens:
                return []
            expression = ' AND '.join('"' + t + '"' for t in tokens)
        elif category:
            expression = TOPICS[category]
        else:
            raise ValueError('Supply a literal text query or category')
        if query is not None and category:
            expression = '(' + expression + ') AND (' + TOPICS[category] + ')'
        ids = self.db.execute('SELECT rowid FROM passages WHERE passages MATCH ? ORDER BY bm25(passages), rowid LIMIT ?',
                              (expression, limit)).fetchall()
        return [dict(self.units[i - 1], search_topic=category, query=query or TOPICS[category],
                     topic_warning='Search topic is a suggestion, not an established disclosure category') for (i,) in ids]

    @property
    def storage_bytes(self):
        return self.db.execute('PRAGMA page_count').fetchone()[0] * self.db.execute('PRAGMA page_size').fetchone()[0]


def load_units(manifest_path):
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text())
    if not 1 <= len(manifest['documents']) <= 12:
        raise ValueError('Offline review is bounded to 1–12 documents')
    keys = {f.name for f in fields(DocumentContext)}
    for row in manifest['documents']:
        original = _input_path(path.parent, row['path'])
        canonical = _input_path(path.parent, row['canonical_path'])
        if original.stat().st_size > 50 * 1024 * 1024 or canonical.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('Offline input exceeds 50 MiB')
        if hashlib.sha256(original.read_bytes()).hexdigest() != row['content_sha256']:
            raise ValueError('Original content hash mismatch')
        if hashlib.sha256(canonical.read_bytes()).hexdigest() != row['canonical_file_sha256']:
            raise ValueError('Canonical file hash mismatch')
        retained = json.loads(canonical.read_text())
        context = DocumentContext(**{k: v for k, v in row.items() if k in keys})
        if (retained['content']['content_sha256'] != context.content_sha256
                or f"artifact:{retained['artifact']['artifact_id']}" != context.document_id
                or retained['content']['content_id'] != context.content_id):
            raise ValueError('Canonical original identity mismatch')
        yield from source_units(context, retained['extraction'], original)


def render_html(hits, output):
    esc = lambda value: html.escape(str(value))
    cards = []
    for hit in hits:
        r = hit['record']; s = r['source']
        # Relative original links work when opening the report directly offline.
        import os
        href = quote(os.path.relpath(hit['original_document'], output.parent), safe='/') + '#page=' + str(hit['original_page'])
        cells = ''.join('<pre>' + esc(e.get('quote') if e.get('quote') is not None else
                         json.dumps(e.get('reference'), ensure_ascii=False, indent=2)) + '</pre>' for e in r['evidence'])
        cards.append('<article><h2>' + esc(s.get('company_name') or 'Company UNKNOWN') + ' · ' + esc(s['document_type']) +
            '</h2><p>Version ' + esc(s['version']) + ' · Physical page/slide ' + esc(hit['original_page']) +
            ' · ' + esc(hit['kind']) + '</p><p>Search topic: ' + esc(hit['search_topic']) +
            ' · Disclosure category: UNESTABLISHED · AMBIGUOUS / review only</p><a href="' + esc(href) +
            '">Open original document at page</a><details open><summary>Original canonical source (unchanged)</summary>' + cells +
            '</details><details><summary>Exact source locators / provenance</summary><pre>' +
            esc(json.dumps(r['evidence'], ensure_ascii=False, indent=2)) + '</pre></details>' +
            '<details><summary>Unbound value observations</summary><pre>' + esc(json.dumps(hit['observed_values'], ensure_ascii=False, indent=2)) +
            '</pre></details><p>Not an accepted financial claim: ' + esc('; '.join(r['reasons'])) + '</p></article>')
    output.write_text('<!doctype html><meta charset="utf-8"><title>Offline source review</title>'
        '<meta name="viewport" content="width=device-width"><style>body{font:16px system-ui;margin:32px auto;max-width:1000px;padding:0 20px;color:#18202a}'
        'article{border-top:1px solid #bbb;margin-top:28px;padding-top:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f6;padding:16px}'
        'h2{font-size:19px}input{font:inherit;padding:8px;width:90%}</style><h1>Offline source review</h1>'
        '<p>Search topics are suggestions. All hits remain AMBIGUOUS. Original page context may include unrelated columns, questions, definitions and boilerplate. '
        'OCR text is a transcription. Inspect the linked original. No publication certification or historical ML eligibility is implied.</p>'
        '<label>Filter displayed results <input id="filter" placeholder="company, topic or source phrase"></label><p>' + str(len(hits)) +
        ' results in this bounded export</p>' + ''.join(cards) +
        '<script>document.getElementById("filter").addEventListener("input",function(){for(const a of document.querySelectorAll("article"))'
        '{a.hidden=!a.textContent.toLowerCase().includes(this.value.toLowerCase())}})</script>')


def run(manifest, output, *, query=None, category=None, limit=20):
    started = time.perf_counter()
    output = Path(output).resolve(); root = Path(manifest).resolve().parent
    from ...config import STORE, DOCUMENT_STORE, EXPORTS, DB_PATH
    if (output.exists() or output.is_relative_to(root) or root.is_relative_to(output)
            or output.is_relative_to(Path('/Volumes')) or any(output.is_relative_to(p.resolve())
            for p in (STORE, DOCUMENT_STORE, EXPORTS, DB_PATH.parent))):
        raise ValueError('Use a new output directory outside inputs and production stores')
    with deny_network():
        index = SourceIndex(load_units(manifest))
        try:
            hits = index.search(query, category=category, limit=limit) if query or category else [
                h for topic in TOPICS for h in index.search(category=topic, limit=limit)]
            report = dict(index_units=len(index.units), hits=len(hits), index_bytes=index.storage_bytes,
                seconds=round(time.perf_counter() - started, 6), accepted_financial_claims=0,
                category_labels='UNESTABLISHED; search_topic is a suggestion', network='denied',
                sqlite='in-memory disposable FTS5', manifest_sha256=hashlib.sha256(Path(manifest).read_bytes()).hexdigest())
        finally:
            index.close()
        output.mkdir(parents=True)
        (output / 'review.json').write_text(json.dumps(hits, ensure_ascii=False, indent=2) + '\n')
        render_html(hits, output / 'review.html')
        report['export_bytes'] = sum(p.stat().st_size for p in output.iterdir())
        (output / 'run.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--query', help='Literal words, AND matched; no executable FTS syntax')
    parser.add_argument('--category', choices=TOPICS)
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.output, query=args.query, category=args.category, limit=args.limit), indent=2))


if __name__ == '__main__':
    main()
