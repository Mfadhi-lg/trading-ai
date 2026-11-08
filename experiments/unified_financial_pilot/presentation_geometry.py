"""Conservative presentation context, using original boundaries, never nearest text.

This is a packet adapter, not a PDF reconstruction or financial validator. All
canonical blocks and boundary alternatives survive. Only uniquely enclosed spans
with a distinct typographic heading get a proposed heading/content relationship.
"""
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .provider import digest
from scrapper.research.document_intelligence.native_layout import enrich_native_layout


def contains(outer, inner, tolerance=0.01):
    return bool(outer and inner and outer[0]-tolerance <= inner[0] and
                outer[1]-tolerance <= inner[1] and outer[2]+tolerance >= inner[2] and
                outer[3]+tolerance >= inner[3])


def box_union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def heading_spans(words):
    """Require larger leading text, a lower body, and one contiguous title group.

    Multiple same-size headings across columns are not a single title. No text
    meaning, company names, percentages or page positions enter this test.
    """
    if not words:
        return []
    height = lambda w: w['bbox'][3]-w['bbox'][1]
    largest = max(height(w) for w in words)
    heads = [w for w in words if height(w) >= largest*0.95]
    body = [w for w in words if height(w) < largest/1.10 and w['text'].strip('• ')]
    if not body or max(w['bbox'][3] for w in heads) > min(w['bbox'][1] for w in body):
        return []
    lines = defaultdict(list)
    for w in heads:
        lines[round(w['bbox'][1], 1)].append(w)
    for line in lines.values():
        line.sort(key=lambda w: w['bbox'][0])
        if any(b['bbox'][0]-a['bbox'][2] > largest*2 for a, b in zip(line, line[1:])):
            return []
    # Wrapped headings must share a left edge; separate title groups stay unknown.
    if max(min(w['bbox'][0] for w in line) for line in lines.values()) - min(
            min(w['bbox'][0] for w in line) for line in lines.values()) > largest:
        return []
    return [w['id'] for w in sorted(heads, key=lambda w: (w['bbox'][1], w['bbox'][0]))]


def page_context(page):
    """Build from D03 enriched native words; useful for synthetic offline tests too."""
    pn = page['page_or_slide_number']
    prefix = f'p{pn}'
    blocks = []
    spans = []
    boundaries = []
    ambiguities = []
    for e in page['elements']:
        eid = f'{prefix}-e{e["element_position"]}'
        if e.get('text_raw'):
            block = dict(id=eid, text=e['text_raw'], bbox=e.get('bbox'), spans=[])
            for i, w in enumerate((e.get('reference') or {}).get('native_words', [])):
                span = dict(id=f'{eid}-w{i}', block=eid, text=w['text'],
                            offsets=[w['start'], w['end']], bbox=w['bbox'])
                if block['text'][w['start']:w['end']] != w['text']:
                    raise ValueError('Source span does not match original block')
                spans.append(span)
                block['spans'].append(span['id'])
            if not block['spans']:
                ambiguities.append(dict(source_id=eid, reason='No word geometry; block retained without panel assignment'))
            blocks.append(block)
        if e['element_kind'] in ('image', 'table', 'panel') and e.get('bbox'):
            # Coextensive image layers are one geometric boundary, not competing panels.
            same = next((b for b in boundaries if max(abs(a-c) for a, c in zip(b['bbox'], e['bbox'])) <= 1), None)
            if same:
                same['sources'].append(eid)
            else:
                boundaries.append(dict(id=f'{prefix}-r{len(boundaries)}', bbox=e['bbox'],
                                       kind=e['element_kind'], sources=[eid]))
    byid = {s['id']: s for s in spans}
    for b in boundaries:
        enclosed = [s for s in spans if contains(b['bbox'], s['bbox'])]
        b['heading_spans'] = heading_spans(enclosed)
        b['membership_basis'] = 'unique boundary containment with larger leading heading'
        b['eligible'] = bool(b['heading_spans'])
    candidates = [b for b in boundaries if b['eligible']]
    for s in spans:
        enclosing = [b for b in candidates if contains(b['bbox'], s['bbox'])]
        # Choose a leaf only within a true containment hierarchy. Overlaps remain ambiguous.
        leaves = [b for b in enclosing if not any(
            c is not b and contains(b['bbox'], c['bbox']) for c in enclosing)]
        s['regions'] = [b['id'] for b in leaves]
        s['region'] = leaves[0]['id'] if len(leaves) == 1 else None
        if len(leaves) != 1:
            ambiguities.append(dict(source_id=s['id'], candidates=s['regions'],
                                    reason='Competing boundaries' if leaves else 'No supported panel boundary/heading'))
    regions = []
    blockmap = {b['id']: b for b in blocks}
    for b in candidates:
        members = [s for s in spans if s['region'] == b['id']]
        heads = [sid for sid in b['heading_spans'] if byid[sid]['region'] == b['id']]
        # A boundary whose heading was claimed by another/ambiguous region is unresolved.
        status = 'proposed' if heads == b['heading_spans'] and members else 'ambiguous'
        grouped = defaultdict(list)
        for s in members:
            grouped[s['block']].append(s)
        fragments = []
        for bid, ws in grouped.items():
            ws.sort(key=lambda w: w['offsets'][0])
            # Keep separate runs if another region's words intervene in a canonical line.
            runs = []
            for w in ws:
                gap = blockmap[bid]['text'][runs[-1][-1]['offsets'][1]:w['offsets'][0]] if runs else ''
                if runs and not gap.strip():
                    runs[-1].append(w)
                else:
                    runs.append([w])
            for run in runs:
                start, end = run[0]['offsets'][0], run[-1]['offsets'][1]
                fragments.append(dict(block=bid, offsets=[start, end],
                    text=blockmap[bid]['text'][start:end], bbox=box_union([s['bbox'] for s in run]),
                    spans=[s['id'] for s in run]))
        fragments.sort(key=lambda f: (f['bbox'][1], f['bbox'][0]))
        for i, f in enumerate(fragments):
            f['id'] = f'{b["id"]}-f{i}'
            f['reading_order'] = i
        regions.append(dict(id=b['id'], bbox=b['bbox'], boundary_sources=b['sources'],
            status=status, heading_spans=heads, spans=[sid for f in fragments for sid in f['spans']],
            fragments=fragments, heading=' '.join(byid[s]['text'] for s in heads),
            text='\n'.join(f['text'] for f in fragments),
            relationship='shared panel enclosure; semantic checks still require human review'))
    regions.sort(key=lambda r: (r['bbox'][1], r['bbox'][0]))
    for i, r in enumerate(regions):
        r['reading_order'] = i
    return dict(physical_page=pn, coordinates='PDF points; origin top-left; bbox x0,y0,x1,y1',
        layout=page.get('layout'), canonical_text=page['selected_text'], blocks=blocks,
        tables=[dict(id=f'{prefix}-e{e["element_position"]}', bbox=e.get('bbox'),
                     rows=e.get('rows'), reference=e.get('reference'))
                for e in page['elements'] if e['element_kind']=='table'],
        spans=spans, boundaries=boundaries, regions=regions, ambiguities=ambiguities,
        reading_order='top-to-bottom then left-to-right; region-local fragments preserve source offsets',
        limitations=['Image bounds are proposed visual enclosures, not certified semantic panels',
            'No nearest-text association; unbounded or overlapping content stays unresolved',
            'No period, accounting scope, unit or qualification is inferred from enclosure',
            'Original strings retained; joined region text is a view, not a verbatim page quote',
            'Raster content, icons and visual meaning require original-slide review'])


def presentation_context(doc, canonical, pages):
    body = Path(doc['source_path']).read_bytes()
    if digest(body) != doc['sha256']:
        raise ValueError('Original PDF hash mismatch')
    enriched = enrich_native_layout(deepcopy(canonical), body, content_sha256=doc['sha256'], pages=pages)
    return dict(kind='presentation_geometry/0.1', pages=[page_context(p) for p in enriched['pages']
                if p['page_or_slide_number'] in pages],
                note='Original rendered slide retained for local review; no image is sent for text tasks')


def model_context(geometry):
    """Lossless region/fragment view for bounded inference; full word map stays local.

    IDs/offsets resolve into the full inspection packet. Every source word is in a
    region fragment or the unresolved list; no word is silently discarded.
    """
    pages = []
    for p in geometry['pages']:
        pages.append(dict(physical_page=p['physical_page'], layout=p['layout'],
            coordinates=p['coordinates'], regions=[dict(id=r['id'], bbox=r['bbox'],
                status=r['status'], heading=r['heading'], reading_order=r['reading_order'],
                fragments=[dict(id=f['id'], block=f['block'], offsets=f['offsets'],
                    bbox=f['bbox'], text=f['text']) for f in r['fragments']]) for r in p['regions']],
            unresolved_spans=[dict(id=s['id'], text=s['text'], bbox=s['bbox'], candidates=s['regions'])
                              for s in p['spans'] if not s['region']],
            unresolved_blocks=[b for b in p['blocks'] if not b['spans']],
            tables=p['tables'],
            limitations=p['limitations']))
    return dict(kind=geometry['kind'], pages=pages, note=geometry['note'])
