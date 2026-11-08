"""Additive offline page-25 inspection. No Client, provider calls or output repair."""
import argparse
from copy import deepcopy
import html
import json
from pathlib import Path
import shutil

from .provider import digest, save, stable
from .presentation_geometry import presentation_context
from .runner import make_packet
from scrapper.research.document_intelligence.offline import deny_network

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'artifacts/unified-financial-pilot-v0.1'
DEFAULT=BASE/'presentation-geometry-v0.1'

# These are audit regression expectations only. No production/packet logic reads them.
EXPECTATIONS=[
    ('C18','Customers – 90% of India top 20','Pharma Analytical'),
    ('C19','40% revenues from overseas','Pharma Analytical'),
    ('C21','75% of revenues from overseas','Clinical Research'),
    ('C22','Amongst Top 3 in India','Preclinical'),
    ('C24','Top 2 in India','Food & Environment Testing'),
    ('C26','The only private EU commission approved lab in India','Food & Environment Testing'),
]


def norm(text):
    return ' '.join(text.replace('•',' ').split())


def build(output):
    output=Path(output).resolve()
    if not output.is_relative_to(BASE) or output==BASE or (output/'packet.json').exists():
        raise ValueError('Use an unused isolated pilot output directory')
    output.mkdir(exist_ok=True)
    manifest=json.loads((BASE/'pilot_manifest.json').read_text())
    doc=next(d for c in manifest['companies'] if c['company_id']=='VIMTALABS'
             for d in c['documents'] if d['document_type']=='presentation')
    canonical=json.loads((BASE/'replayed-final/VIMTALABS/presentation.canonical.json').read_text())
    geometry=presentation_context(doc,canonical,[25])
    packet,messages=make_packet(doc,canonical,'qualitative',25)
    key=next(a['cache_key'] for a in json.loads((BASE/'inference/inference_usage.json').read_text())['requests']
             if a['packet']['document_id']==doc['document_id'] and a['packet']['pages']==[25])
    request_path=BASE/'inference/requests'/f'{key}.json'
    response_path=BASE/'inference/responses'/f'{key}.json'
    before=json.loads(request_path.read_text())
    raw=json.loads(response_path.read_text())
    original=BASE/'vimta-audit/source/presentation-25.png'
    shutil.copyfile(original,output/'original-page-25.png')
    full=dict(document_id=doc['document_id'],source_sha256=doc['sha256'],geometry=geometry,
              review_reference=dict(pdf=doc['source_path'],physical_page=25,
                  render='original-page-25.png',render_sha256=digest(original.read_bytes())),
              qwen_packet=packet,qwen_messages=messages,
              geometry_sha256=digest(stable(geometry).encode()))
    save(output/'packet.json',full)
    save(output/'qwen-context.json',packet['representation'])
    save(output/'qwen-messages.json',messages)
    save(output/'before-source-packet.json',json.loads(before['messages'][1]['content']))
    p=geometry['pages'][0]
    assertions=[]
    for cid,needle,heading in EXPECTATIONS:
        matches=[r for r in p['regions'] if needle in norm(r['text'])]
        assert len(matches)==1 and matches[0]['heading']==heading
        r=matches[0]
        assertions.append(dict(candidate_id=cid,expectation=needle,expected_heading=heading,
            region_id=r['id'],heading_spans=r['heading_spans'],source_span_ids=r['spans'],
            fragment_ids=[f['id'] for f in r['fragments']],status='structurally_distinguishable',
            human_review_status='Needs human review'))
    save(output/'regression-associations.json',assertions)
    save(output/'unresolved-layout.json',dict(unassigned_spans=p['ambiguities'],
        note='Six title words and two printed slide-number tokens remain outside business panels.',
        remaining=['Boundary grouping is a proposal from image extents and typography, not certified semantics',
                   'Background image strips, coextensive layers and icons remain in boundary inventory',
                   'No date/period or accounting scope is established by these panels',
                   'Bullets and wrapped lines remain separate original fragments',
                   'Geographical qualification retention does not prove model semantic fidelity']))
    queue=deepcopy(json.loads((BASE/'vimta-audit/human_candidate_review_template.json').read_text()))
    original_candidates=json.loads((BASE/'vimta-audit/model_candidates.original.json').read_text())
    presentations={r['evidence_id'] for r in original_candidates if r['document_type']=='presentation'}
    for row in queue['observations']:
        if row['evidence_id'] in presentations:
            row.update(business_association=None,period_association=None,source_region=None)
    queue['instructions'] += (' Presentation supplement: check business_association, period_association and source_region. '
        'This is the same 30 candidates, with no corrected model outputs or human decisions. '
        'Historical candidates have no region citations and remain unresolved; do not silently retrofit them.')
    assert len(queue['observations'])==30
    save(output/'human-review-queue-supplement.json',queue)
    comparison=dict(before=dict(request=str(request_path.relative_to(ROOT)),request_sha256=digest(request_path.read_bytes()),
        response=str(response_path.relative_to(ROOT)),response_sha256=digest(response_path.read_bytes()),
        model=before['model'],input=before['messages'][1]['content'],input_tokens=raw['usage']['prompt_tokens'],
        representation='D03 native page text, tables=[]; no geometry, region IDs or rendered image'),
        after=dict(word_spans=len(p['spans']),canonical_blocks=len(p['blocks']),regions=len(p['regions']),
            boundaries=len(p['boundaries']),unassigned_spans=len(p['ambiguities']),
            input_upper_bound=len(stable(messages).encode())+1024,existing_input_limit=16000,
            context_sha256=packet['representation_sha256'],all_six_structurally_distinguishable=True),
        correctness=None,material_recall=None,new_inference_calls=0,
        readiness='Packet ready for separately authorized regression; current ledger has used all six authorized attempts. '
                  'No budget/call limit reset or replacement response. Presentation prompt removes the four-disclosure cap; '
                  'separately review output budget (4096 tokens suggested) before execution.')
    save(output/'comparison.json',comparison)
    checklist='''# Focused human reviewer checklist

Open the original PDF at physical page 25 (printed slide 24), alongside inspection.html.
This supplement preserves the existing 30-candidate queue and evidence IDs. No reviewer
or decision has been filled in. Codex annotations are not independent human review.

- Check C18/C19 against the Pharma Analytical panel: customer-base claim and 40% overseas revenues.
- Check C21 against Clinical Research: 75% overseas revenues.
- Check C22 against Preclinical: Amongst Top 3 in India.
- Check C24/C26 against Food & Environment Testing: Top 2 and private EU commission approved lab.
- Check C20 against Electronics & Electricals: retain Southern India literally. Packet retention does not prove model fidelity.
- Compare each proposed box, heading and cited fragment with the PDF; reject incorrect or overlapping membership.
- Check value-to-business, value-to-period, unit/scale, financial scope, original region and every material qualification separately. Mark unknowns unresolved; do not inherit period/scope from filing metadata.
- Review the original candidates, not a presumed replacement answer. Record actual reviewer identity/date and independence only when true. Keep financial correctness and overall material recall unmeasured until independent source-reference review.
'''
    (output/'REVIEWER-CHECKLIST.md').write_text(checklist)
    render(output,full,before,assertions,comparison)
    return comparison


def render(output,full,before,assertions,comparison):
    esc=lambda x:html.escape(str(x))
    pre=lambda x:'<pre>'+esc(json.dumps(x,ensure_ascii=False,indent=2))+'</pre>'
    p=full['geometry']['pages'][0]
    w,h=p['layout']['width_points'],p['layout']['height_points']
    svg=[f'<svg viewBox="0 0 {w} {h}" aria-label="Original slide with geometry overlays"><image href="original-page-25.png" width="{w}" height="{h}"/>']
    for r in p['regions']:
        x,y,x1,y1=r['bbox']
        svg.append(f'<a href="#{r["id"]}"><rect class="panel" x="{x}" y="{y}" width="{x1-x}" height="{y1-y}"/><text x="{x}" y="{y-4}">{esc(r["id"])}</text></a>')
    for s in p['spans']:
        x,y,x1,y1=s['bbox']
        svg.append(f'<rect class="word" x="{x}" y="{y}" width="{x1-x}" height="{y1-y}"><title>{esc(s["id"]+": "+s["text"])}</title></rect>')
    svg.append('</svg>')
    body=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
      '<title>Vimta page 25 — source geometry inspection</title>',
      '<style>body{font:15px system-ui;color:#183237;margin:24px;line-height:1.5}h1{font-size:25px}a{color:#006b76}main{display:grid;grid-template-columns:minmax(520px,1.2fr) minmax(400px,1fr);gap:24px}.slide{position:sticky;top:15px;align-self:start}svg,img{width:100%}.panel{fill:none;stroke:#007c86;stroke-width:1.2}.word{fill:transparent;stroke:#b36326;stroke-width:.25}svg text{font-size:8px;fill:#006b76}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f5;padding:14px;font-size:12px}article{border-top:1px solid #b6c9cb;padding:12px 0}blockquote{white-space:pre-wrap;margin:12px 0}summary{cursor:pointer}button{padding:6px 10px;margin:6px}small{color:#526568}.off .word{display:none}@media(max-width:1000px){main{display:block}.slide{position:static}}</style>',
      '<h1>Physical page 25 · source geometry inspection</h1>',
      '<p>Five proposed panels · six regression associations structurally distinguishable · no inference run. Correctness and material recall remain unmeasured.</p>',
      '<p><a href="packet.json">Full machine packet</a> · <a href="qwen-context.json">Exact Qwen context</a> · <a href="qwen-messages.json">Exact messages</a> · <a href="comparison.json">Before/after</a> · <a href="unresolved-layout.json">Unresolved</a> · <a href="REVIEWER-CHECKLIST.md">Human checklist</a> · <a href="human-review-queue-supplement.json">Same 30 candidates</a></p>',
      '<main><div class="slide"><button onclick="this.parentElement.classList.toggle(\'off\')">Toggle word boxes</button>'+''.join(svg)+
      '<p>Teal: proposed panel boundaries. Thin brown: individual source words. Hover for IDs; click a panel for exact fragments.</p>'+
      '<details><summary>Original render without overlays</summary><img alt="Original physical page 25" src="original-page-25.png"></details>'+
      '<p><a href="'+esc(full['review_reference']['pdf'])+'#page=25">Original local PDF · physical page 25</a></p></div><div>',
      '<h2>Proposed panel membership</h2><p>Containment plus larger leading heading; no nearest-text assignment. These are structural proposals requiring actual human source review.</p>']
    for r in p['regions']:
        body.append('<article id="'+r['id']+'"><b>'+esc(r['heading'])+'</b> · '+esc(r['id'])+
            '<p>Boundary '+esc(r['bbox'])+' · source elements '+esc(r['boundary_sources'])+'</p><blockquote>'+esc(r['text'])+
            '</blockquote><details><summary>Exact fragments, bounding boxes and IDs</summary>'+pre(r['fragments'])+'</details></article>')
    body += ['<h2>Offline regression expectations</h2>',pre(assertions),
        '<h2>Before: actual paid request context</h2>',pre(json.loads(before['messages'][1]['content'])),
        '<p>D03 merged same-baseline columns before packet construction. The old packet then discarded text-block/image bounds, page layout, element identities and region-local reading order. No tables were detected; no slide image was sent.</p>',
        '<h2>After: exact structured context that would be sent</h2>',pre(full['qwen_packet']['representation']),
        '<details><summary>Exact system/user messages</summary>',pre(full['qwen_messages']),'</details>',
        '<h2>Lost or unresolved information</h2>',pre(p['ambiguities']),pre(p['limitations']),
        '<p>The six page-title words and two printed page numbers remain unassigned. There are no competing memberships for the six disputed disclosures. Image pixels, icons, typography and visual interpretation remain available in the original slide, not the text model context. No period/accounting-scope inference or financial validation is claimed.</p>',
        '<details><summary>All retained original blocks and boundary alternatives</summary>',pre(p['blocks']),pre(p['boundaries']),'</details>',
        '</div></main></html>']
    (output/'inspection.html').write_text('\n'.join(body))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default=str(DEFAULT))
    args=parser.parse_args()
    with deny_network():
        print(json.dumps(build(args.output),indent=2))
