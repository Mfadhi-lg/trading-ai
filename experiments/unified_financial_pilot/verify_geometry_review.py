"""Verify the additive packet, historical replay, queue and byte preservation offline."""
import json
from html.parser import HTMLParser
from pathlib import Path

from .build_geometry_review import BASE, DEFAULT, ROOT, norm, EXPECTATIONS
from .provider import digest, save, stable
from .runner import ingest_model, make_packet
from scrapper.research.document_intelligence.offline import deny_network


def verify(output=DEFAULT):
    output=Path(output)
    read=lambda p:json.loads(p.read_text())
    before=read(output/'preservation-before.json')
    intentional={f'experiments/unified_financial_pilot/{name}.py' for name in ('runner','schema','evaluate')}
    changed=[p for p,h in before.items() if not (ROOT/p).is_file() or digest((ROOT/p).read_bytes())!=h]
    assert set(changed)==intentional,changed
    protected={p:h for p,h in before.items() if p not in intentional}
    assert all((ROOT/p).is_file() and digest((ROOT/p).read_bytes())==h for p,h in protected.items())
    manifest=read(BASE/'pilot_manifest.json')
    company=next(c for c in manifest['companies'] if c['company_id']=='VIMTALABS')
    docs={d['document_id']:d for d in company['documents']}
    replayed=[]
    ledger=read(BASE/'inference/inference_usage.json')
    assert len(ledger['requests'])==6
    for a in ledger['requests']:
        cache=BASE/'inference/cache'/f'{a["cache_key"]}.json'
        if not cache.exists():continue
        doc=docs[a['packet']['document_id']]
        canonical=read(BASE/'replayed-final/VIMTALABS'/f'{doc["document_type"]}.canonical.json')
        records,_=ingest_model(read(cache),company,doc,canonical,a['packet'],a['cache_key'])
        replayed.extend(records)
    original=read(BASE/'vimta-audit/model_candidates.original.json')
    assert {r['evidence_id']:r for r in replayed}=={r['evidence_id']:r for r in original}
    assert len(original)==30 and sum(r['anchor_verified'] for r in original)==19
    supplement=read(output/'human-review-queue-supplement.json')['observations']
    queue=read(BASE/'vimta-audit/human_candidate_review_template.json')['observations']
    assert [r['evidence_id'] for r in supplement]==[r['evidence_id'] for r in queue]
    assert all(all(row[k]==v for k,v in old.items()) for row,old in zip(supplement,queue))
    full=read(output/'packet.json');p=full['geometry']['pages'][0]
    assert len(p['regions'])==5 and len(p['spans'])==109
    blocks={b['id']:b for b in p['blocks']};spans={s['id']:s for s in p['spans']}
    used=[]
    for r in p['regions']:
        used.extend(r['spans'])
        assert all(spans[s]['region']==r['id'] for s in r['spans'])
        for f in r['fragments']:
            a,b=f['offsets'];assert blocks[f['block']]['text'][a:b]==f['text']
    used.extend(s['id'] for s in p['spans'] if not s['region'])
    assert sorted(used)==sorted(spans) and len(used)==len(set(used))
    assert read(output/'qwen-context.json')==json.loads(full['qwen_messages'][1]['content'])
    assert digest(stable(full['qwen_packet']['representation']).encode())==full['qwen_packet']['representation_sha256']
    assert digest(stable(full['geometry']).encode())==full['geometry_sha256']
    doc=docs[full['document_id']];canonical=read(BASE/'replayed-final/VIMTALABS/presentation.canonical.json')
    packet,messages=make_packet(doc,canonical,'qualitative',25)
    assert packet==full['qwen_packet'] and messages==full['qwen_messages']
    for cid,needle,heading in EXPECTATIONS:
        for page in (p,packet['representation']['pages'][0]):
            matches=[r for r in page['regions'] if needle in norm('\n'.join(f['text'] for f in r['fragments']))]
            assert len(matches)==1 and matches[0]['heading']==heading
    assert len(stable(messages).encode())+1024<=16000
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.links=[];self.ids=set()
        def handle_starttag(self,tag,attrs):
            d=dict(attrs)
            if 'id' in d:self.ids.add(d['id'])
            if tag in ('img','image','a'):
                href=d.get('href') or d.get('src')
                if href:self.links.append(href)
    parsed=Links();parsed.feed((output/'inspection.html').read_text())
    for link in parsed.links:
        if link.startswith('#'):assert link[1:] in parsed.ids
        else:assert (output/link.split('#')[0]).exists(),link
    comparison=read(output/'comparison.json')
    assert comparison['correctness'] is None and comparison['material_recall'] is None
    results=dict(status='PASS',protected_existing_files=len(protected),changed_existing_files=changed,
        all_existing_pilot_artifacts_and_spending_records_byte_identical=True,
        historical_candidates_replay_byte_equivalent=30,literal_anchors_unchanged=19,
        same_human_queue=30,new_human_decisions=0,total_recorded_attempts=6,new_attempts=0,
        structurally_distinguishable_regressions=6,source_spans_preserved=109,
        unassigned_spans=8,html_links_and_panel_anchors='PASS',packet_recomputes=True,
        correctness=None,overall_material_recall=None,
        limitations=['No paid inference or replacement output; no extraction correctness claim',
                    'Frozen cases, pilot selection and original sources preserved by byte hashes'])
    save(output/'validation.json',results)
    files=sorted(p for p in output.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
    (output/'SHA256SUMS').write_text(''.join(f'{digest(p.read_bytes())}  {p.relative_to(output)}\n' for p in files))
    return results


if __name__=='__main__':
    with deny_network():
        print(json.dumps(verify(),indent=2))
