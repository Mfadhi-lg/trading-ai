"""Offline structure comparison helpers. Model topology never becomes native text.

Cells link to immutable native word spans. Predicted cells are sidecar geometry;
original canonical rows, element positions and evidence locators stay intact.
"""
from copy import deepcopy
import io
import posixpath
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from .claim_candidates import _labels,_periods,_scope
from .context import UNIT
from .numbers import NUMBER,normalize_value,values_in


def inside(outer,inner):
    return outer[0]-1<=inner[0] and outer[1]-1<=inner[1] and outer[2]+1>=inner[2] and outer[3]+1>=inner[3]


def source_words(packet):
    return [dict(w,atom=aid,evidence={**a['evidence'],'start':a['evidence']['start']+w['start'],'end':a['evidence']['start']+w['end'],'quote':w['text']}) for aid,a in packet['atoms'].items() if a['page']==packet['page'] and not a['table'] for w in a.get('native_words',[])]


def ground_cells(packet,cells):
    words=source_words(packet);out=[];rejected=[]
    for i,cell in enumerate(cells):
        ws=[w for w in words if inside(cell['bbox'],w['bbox'])]
        # A word claimed by overlapping model cells is not independent evidence.
        ambiguous=[w for w in ws if sum(inside(c['bbox'],w['bbox']) for c in cells)>1]
        if ambiguous:
            rejected.append({'cell':i,'reason':'overlapping_structure_cells','words':[w['text'] for w in ambiguous]})
            ws=[w for w in ws if w not in ambiguous]
        ws.sort(key=lambda w:(round(w['bbox'][1]/3),w['bbox'][0]))
        fragments=[]
        for w in ws:
            if fragments and fragments[-1]['atom']==w['atom'] and packet['atoms'][w['atom']]['text'][fragments[-1]['end']:w['start']].isspace():
                fragments[-1]['end']=w['end']
            else:fragments.append({'atom':w['atom'],'start':w['start'],'end':w['end']})
        ev=[]
        for f in fragments:
            a=packet['atoms'][f['atom']];e=a['evidence'];ev.append({**e,'start':e['start']+f['start'],'end':e['start']+f['end'],'quote':a['text'][f['start']:f['end']]})
        out.append({**cell,'text':' '.join(e['quote'] for e in ev),'evidence':ev,'words':ws})
    return out,rejected


def packet_from_cells(packet,cells,region,*,method):
    p=deepcopy(packet);p['values']={};p['frames']={};p['diagnostics']=[]
    grounded,rejected=ground_cells(packet,cells);p['diagnostics']+=rejected
    if not grounded:return p,grounded
    first=min((c['row'] for c in grounded if any(re.fullmatch(rf'\(?{NUMBER}\)?',w['text']) for w in c['words'])),default=1)
    header=[c for c in grounded if c['row']<first]
    globalscope=[a for a in packet['atoms'].values() if a['page']==packet['page'] and not a['table'] and a['bbox'] and a['bbox'][3]<region[1] and region[1]-a['bbox'][3]<100 and _scope(a['text'])]
    for c in grounded:
        nums=[w for w in c['words'] if re.fullmatch(rf'\(?{NUMBER}\)?',w['text'])]
        if not nums:continue
        if len(nums)!=1:
            p['diagnostics'].append({'row':c['row'],'column':c['column'],'reason':'multiple_numeric_words_in_structure_cell'});continue
        w=nums[0];raw=w['text']
        row=[x for x in grounded if x['row']==c['row'] and x['column']<c['column']]
        metriccells=[x for x in row if _labels(x['text'])]
        metrics={m for x in metriccells for _,_,m in _labels(x['text'])}
        if len(metrics)!=1:
            p['diagnostics'].append({'row':c['row'],'column':c['column'],'raw':raw,'reason':'metric_missing_or_ambiguous'});continue
        colheaders=[x for x in header if x['column']<=c['column']<x['column']+x.get('col_span',1)]
        ps=[(period,e) for x in colheaders for e in x['evidence'] for _,_,period in _periods(e['quote'])]
        localunits=[(m.group(),e) for x in row+colheaders for e in x['evidence'] if (m:=UNIT.search(e['quote']))]
        units=localunits or [(m.group(),e) for x in header if x['column']==0 for e in x['evidence'] if (m:=UNIT.search(e['quote']))]
        ns=[(normalize_value(raw,unit_hint=u),e) for u,e in units];ns=[(n,e) for n,e in ns if n]
        kinds={tuple(n[k] for k in ('unit','currency','scale')) for n,_ in ns}
        if len(kinds)!=1:
            p['diagnostics'].append({'row':c['row'],'column':c['column'],'raw':raw,'source_unit_text':[u for u,_ in units],'reason':'source_unit_not_supported_by_strict_decimal_normalizer'});continue
        n=ns[0][0];fid=f'{method}:r{c["row"]}c{c["column"]}';pidlist=[]
        for i,(period,e) in enumerate(ps):
            pid=f'{fid}:p{i}';p['periods'][pid]={'period':period,'evidence':e,'page':p['page']};pidlist.append(pid)
        scopes=[(_scope(x['text']),x['evidence']) for x in colheaders+row if _scope(x['text'])]
        if not scopes and len(globalscope)==1:scopes=[(_scope(globalscope[0]['text']),[globalscope[0]['evidence']])]
        sids=[]
        if len({sc for sc,_ in scopes})==1:
            sc,es=scopes[0];sid=f'{fid}:scope';p['scopes'][sid]={'scope':sc,'evidence':es[0],'support_evidence':es[1:],'page':p['page']};sids=[sid]
        metric_ev=[e for x in metriccells for e in x['evidence']]
        aids=list(dict.fromkeys([w['atom']]+[x['atom'] for mc in metriccells for x in mc['words']]))
        p['frames'][fid]={'id':fid,'atoms':aids,'text':' '.join(e['quote'] for e in metric_ev)+' '+raw,'speaker':None,'page':p['page'],'bbox':c['bbox'],'period_ids':pidlist,'scope_ids':sids,'table_row':[method,c['row']]}
        rel={'metrics':list(metrics),'metric_evidence':metric_ev,'period_ids':pidlist,'scope_ids':sids,'support_evidence':[e for x in colheaders for e in x['evidence']],'table':method,'row':c['row'],'column':c['column'],'statement_text':' '.join(x['text'] for x in colheaders+metriccells),'source_kind':'element_text','proposed_structure_bbox':c['bbox']}
        vid=f'{w["atom"]}:structure{w["start"]}'
        p['values'][vid]={'atom':w['atom'],'raw':raw,'start':w['start'],'end':w['end'],'number':n,'frame':fid,'unit_evidence':[e for _,e in ns],'column_period_ids':pidlist,'relationship':rel}
    return p,grounded


# Re-export the shared native cache reader for existing research callers.
from .charts import embedded_chart_parts


def vector_chart_labels(packet,page_objects,region):
    """Recover only explicit labels attached to source rectangles and categories.

    No interpolation, baseline-height digitization or unshown numeric axes.
    Unresolved color/legend relationships stay unresolved.
    """
    words=[w for w in source_words(packet) if inside(region,w['bbox'])]
    categories=[w for w in words if re.fullmatch(r'Q[1-4]FY\d{2}|20\d{2}E?',w['text'])]
    rects=[r for r in page_objects['rects'] if inside(region,[r['x0'],r['top'],r['x1'],r['bottom']]) and r.get('fill') and 5<r['width']<90 and r['height']>2]
    # Explicit small swatch immediately left of an explicit legend label.
    legend={}
    for r in rects:
        if r['width']>20 or r['height']>15:continue
        labels=[w for w in words if 0<=w['bbox'][0]-r['x1']<12 and abs((w['bbox'][1]+w['bbox'][3])/2-(r['top']+r['bottom'])/2)<6 and not re.search(r'\d',w['text'])]
        if len(labels)==1:legend[str(r.get('non_stroking_color'))]=labels[0]
    captions=[a for a in packet['atoms'].values() if a['page']==packet['page'] and not a['table'] and a['bbox'] and inside(region,a['bbox']) and _labels(a['text']) and not any(w['text']==a['text'] for w in categories)]
    title=captions[0]['text'] if len(captions)==1 else None
    simple_name=re.sub(r'\s*\(.*?\)|\*','',title).strip() if title else None
    points=[];rejected=[]
    for cat in categories:
        x=(cat['bbox'][0]+cat['bbox'][2])/2
        bars=[r for r in rects if r['x0']<=x<=r['x1'] and 0<=cat['bbox'][1]-r['bottom']<180]
        for r in bars:
            candidates=[w for w in words if re.fullmatch(NUMBER,w['text']) and w not in categories and r['x0']-2<=(w['bbox'][0]+w['bbox'][2])/2<=r['x1']+2 and (r['top']<=w['bbox'][1] and w['bbox'][3]<=r['bottom'] or 0<=r['top']-w['bbox'][3]<14)]
            if len(candidates)!=1:
                rejected.append({'category':cat['text'],'bar_bbox':[r['x0'],r['top'],r['x1'],r['bottom']],'reason':'numeric_label_missing_or_ambiguous'});continue
            value=candidates[0];series=legend.get(str(r.get('non_stroking_color')))
            points.append({'category':cat['text'],'series':series['text'] if series else simple_name,'value':value['text'].replace(',',''),'numeric_class':'directly_labeled','value_evidence':value['evidence'],'category_evidence':cat['evidence'],'series_evidence':series['evidence'] if series else captions[0]['evidence'] if title else None,'bar_bbox':[r['x0'],r['top'],r['x1'],r['bottom']]})
    return {'classification':'vector_pdf','title':title,'unit_caption':title if title and re.search(r'Rs|₹|units|GWh',title) else None,'points':points,'legend_labels':[x['text'] for x in legend.values()],'x_axis_kind':'explicit_category_labels' if categories else None,'y_axis':None,'estimated_values':[],'rejected':rejected,'policy':'Unshown numeric axes, missing source points and unsupported series are unresolved.'}
