"""Conservative source links for native financial tables (offline research only)."""
import re
from .context import UNIT, table_period
from .numbers import NUMBER, normalize_value, values_in
from .evidence import cell_evidence, text_evidence

SCALAR = re.compile(rf'\(?{NUMBER}\)?')


def cell_box(element, row, column):
    boxes = (element.get('reference') or {}).get('cell_bboxes') or []
    return boxes[row][column] if row < len(boxes) and column < len(boxes[row]) else None


def _contains_x(outer, inner):
    return bool(outer and inner and outer[0] <= inner[0]+1 and outer[2] >= inner[2]-1)


def _scalar(text):
    return bool(SCALAR.fullmatch(text.strip()) or normalize_value(text.strip()))


def _span(atom, start, end):
    return {**atom['evidence'], 'start': atom['evidence']['start']+start,
            'end': atom['evidence']['start']+end, 'quote': atom['text'][start:end]}


def _approach(extraction, target):
    declarations=[]
    for page in extraction['pages']:
        if page['page_or_slide_number'] > target:break
        elements=[e for e in page['elements'] if e['text_origin']=='native' and e.get('text_raw')]
        for i,e in enumerate(elements):
            text=e['text_raw'];match=re.match(r'^Analytical approach\s*:\s*(.*)$',text,re.I)
            if not match:continue
            support=[text_evidence(page,text,element=e)];body=match[1]
            if not body and i+1<len(elements):
                nxt=elements[i+1];a=e.get('bbox');b=nxt.get('bbox')
                if not a or not b or not ((0<=b[1]-a[3]<30) or (abs(a[1]-b[1])<3 and b[0]>=a[2])):continue
                body=nxt['text_raw'];support.append(text_evidence(page,body,element=nxt))
            m=re.match(r'^(Standalone|Consolidated)\b',body,re.I)
            if m and not re.search(r'\bstandalone\b.*\bconsolidated\b|\bconsolidated\b.*\bstandalone\b',body,re.I):
                declarations.append((m[1].lower(),support))
    return declarations[0] if len(declarations)==1 else None


def bind_tables(packet, extraction, *, labels, periods_in, scope_in):
    """Add original-span values and relationships; never edit rows or join numbers.

    Headers use original columns or containment of source cell/word rectangles.
    With no geometry, shifted/merged headers stay unresolved. A missing grid cell
    may use an exact native word span in that row and header column; its evidence
    remains element_text, never table_cell.
    """
    pn=packet['page'];page=next(p for p in extraction['pages'] if p['page_or_slide_number']==pn)
    atoms=packet['atoms'];values=packet['values'];diagnostics=packet.setdefault('diagnostics',[])
    grids=[e for e in page['elements'] if e['text_origin']=='native' and e['element_kind']=='table' and len(e.get('rows') or [])>=2 and max(map(len,e['rows']))>=2]
    approach=_approach(extraction,pn) if packet['source']['document_type']=='credit_rating' else None
    for table in grids:
        ep=table['element_position'];rows=table['rows'];tb=table.get('bbox')
        ids={(a['table']['row'],a['table']['column']):aid for aid,a in atoms.items() if a['page']==pn and a['table'] and a['table']['element']==ep}
        first=next((ri for ri,row in enumerate(rows) if any(_scalar(c) for c in row if c.strip()) or (ri>0 and any(labels(c) for c in row if c.strip()))),None)
        if first is None:continue
        header=[(ri,ci,t) for ri,row in enumerate(rows[:first]) for ci,t in enumerate(row) if t.strip()]
        header_text=' '.join(t for _,_,t in header)
        annual=bool(re.search(r'brief\s+(?:consolidated\s+)?financial',header_text,re.I))
        external=[(aid,a) for aid,a in atoms.items() if a['page']==pn and not a['table'] and a['bbox']]
        # A dedicated unit caption next to this table; a narrative mention is not a unit header.
        captions=[]
        if tb:
            for aid,a in external:
                t=a['text'].strip();m=UNIT.search(t)
                if m and not re.search(r'\d',t) and re.fullmatch(r'[\s()]*' + r'(?:In\s+)?' + re.escape(m.group()) + r'[\s()]*',t,re.I) and 0<=tb[1]-a['bbox'][3]<=60:
                    other=[e for e in grids if e is not table and e.get('bbox') and 0<=e['bbox'][1]-a['bbox'][3]<tb[1]-a['bbox'][3]]
                    if not other:captions.append((m.group(),a['evidence']))
        title=[]
        if len(grids)==1 and tb:
            title=[(aid,a) for aid,a in external if a['bbox'][3]<tb[1] and re.search(r'\bsnapshot\b|\bfinancial performance\b|\b(?:standalone|consolidated)\s+(?:profit|financial)',a['text'],re.I)]
        title_periods=[(p,a['evidence']) for _,a in title for _,_,p in periods_in(a['text'])]
        table_title=' '.join(a['text'] for _,a in title)
        def frame_for(ri):
            return next((fid for fid,f in packet['frames'].items() if f.get('table_row')==[ep,ri]),None)
        for ri,row in enumerate(rows[first:],first):
            fid=frame_for(ri)
            if not fid:continue
            row_atoms=[(ids[ri,ci],atoms[ids[ri,ci]]) for ci in range(len(row)) if (ri,ci) in ids]
            row_labels=[(aid,a) for aid,a in row_atoms if labels(a['text']) and not _scalar(a['text'])]
            if not row_labels and tb:
                boxes=[cell_box(table,ri,ci) for ci,c in enumerate(row) if c.strip()]
                boxes=[b for b in boxes if b]
                if boxes:
                    low=min(b[1] for b in boxes);high=max(b[3] for b in boxes)
                    row_labels=[(aid,a) for aid,a in external if a['bbox'][2]<=tb[0]+2 and low<=(a['bbox'][1]+a['bbox'][3])/2<=high and labels(a['text']) and not list(values_in(a['text']))]
            metric_set={m for _,a in row_labels for _,_,m in labels(a['text'])}
            row_label_text=' '.join(a['text'] for _,a in row_labels)
            row_support=[a['evidence'] for _,a in row_labels]
            for ci,raw in enumerate(row):
                aid=ids.get((ri,ci));a=atoms.get(aid)
                explicit=list(values_in(raw)) if raw else []
                if len(explicit)>1:
                    diagnostics.append({'stage':'table_cell_identification','atom':aid,'reason':'multiple_values_in_cell_unresolved','evidence':a['evidence']})
                    for v in values.values():
                        if v['atom']==aid:v['relation_rejection']='multiple_values_in_cell_unresolved'
                    continue
                source_values=[]
                if a and _scalar(raw):
                    source_values=[(aid,raw.strip(),raw.find(raw.strip()),raw.find(raw.strip())+len(raw.strip()),cell_box(table,ri,ci))]
                # Missing borders: use native words only when original cell is empty.
                if not raw.strip() and tb and first>0 and ri>=first:
                    column_boxes=[cell_box(table,h,ci) for h in range(first) if ci<len(rows[h]) and rows[h][ci].strip()]
                    column_boxes=[b for b in column_boxes if b]
                    rboxes=[cell_box(table,ri,c) for c in range(len(row))];rboxes=[b for b in rboxes if b]
                    if column_boxes and rboxes:
                        col=min(column_boxes,key=lambda b:b[2]-b[0]);y0=min(b[1] for b in rboxes);y1=max(b[3] for b in rboxes)
                        found=[]
                        for bid,b in external:
                            for word in b.get('native_words',[]):
                                box=word['bbox'];token=word['text']
                                if any(row[c].strip() and _scalar(row[c]) and _contains_x(cell_box(table,ri,c),box) for c in range(len(row))):continue
                                if _contains_x(col,box) and y0<=(box[1]+box[3])/2<=y1 and _scalar(token):
                                    found.append((bid,token,word['start'],word['end'],box))
                        if len(found)==1:source_values=found
                        elif found:diagnostics.append({'stage':'table_cell_identification','table':ep,'row':ri,'column':ci,'reason':'multiple_native_words_in_missing_cell'})
                for vaid,token,start,end,box in source_values:
                    va=atoms[vaid];headers=[]
                    for hr,hc,ht in header:
                        hb=cell_box(table,hr,hc)
                        if (_contains_x(hb,box) if hb and box else hc==ci):headers.append((hr,hc,ht))
                    column_text=' '.join(t for _,_,t in headers)
                    selected=metric_set
                    metric_evidence=row_support
                    named_scope=None
                    if not selected:
                        selected={m for _,_,t in headers for _,_,m in labels(t)}
                        metric_evidence=[cell_evidence(page,table,h,c) for h,c,t in headers if labels(t)]
                        names=[a for _,a in row_atoms if a['table']['column']<ci and not _scalar(a['text']) and not labels(a['text']) and not UNIT.search(a['text'])]
                        if len(names)==1:named_scope=(names[0]['text'],[names[0]['evidence']])
                    support=[cell_evidence(page,table,h,c) for h,c,_ in headers]
                    unit_options=[]
                    # The row or column's own unit takes precedence over table money scale.
                    local=[(m.group(),a['evidence']) for _,a in row_labels if (m:=UNIT.search(a['text']))]
                    local += [(m.group(),cell_evidence(page,table,h,c)) for h,c,t in headers if (m:=UNIT.search(t))]
                    if local:unit_options=local
                    else:
                        unit_options=[(m.group(),cell_evidence(page,table,h,c)) for h,c,t in header if (m:=UNIT.search(t)) and normalize_value('1',unit_hint=m.group()) and normalize_value('1',unit_hint=m.group())['unit']=='currency']+captions
                    normalized=normalize_value(token)
                    units={tuple((normalize_value('1',unit_hint=u) or {}).get(k) for k in ('unit','currency','scale')) for u,_ in unit_options}
                    if normalized is None and len(units)==1 and unit_options:normalized=normalize_value(token,unit_hint=unit_options[0][0])
                    if not normalized or normalized['approximate'] or not isinstance(normalized['normalized_value'],str):
                        diagnostics.append({'stage':'unit_and_scale_inheritance','atom':vaid,'start':start,'end':end,'raw':token,'reason':'unit_missing_or_conflicting','evidence':_span(va,start,end)})
                        continue
                    # Distinct year and quarter header levels may refine the same FY.
                    ps=[(p,cell_evidence(page,table,h,c)) for h,c,t in headers for _,_,p in periods_in(t)]
                    combined=table_period(column_text,annual=annual)
                    quarter=re.search(r'\b(Q[1-4]|H[12]|9M)\b',column_text,re.I)
                    fys={p for p,_ in ps if re.fullmatch(r'FY\d{2}',p)}
                    if quarter and len(fys)==1:combined=quarter[1].upper()+next(iter(fys))
                    if combined and not ps:ps=[(combined,support[0])] if support else []
                    elif combined and quarter and fys:ps=[(combined,support[0])]
                    names={p for p,_ in ps}
                    specific={p for p in names if not p.startswith('FY')}
                    if len(specific)==1 and all(p.endswith(next(iter(specific))[-4:]) for p in names):
                        ps=[(p,e) for p,e in ps if p in specific];names=specific
                    if not ps and not column_text.strip() and len({p for p,_ in title_periods})==1:ps=title_periods
                    # A date-only annual header must be a Brief Financials March-31 summary.
                    if not ps and combined:ps=[(combined,support[0])] if support else []
                    pids=[]
                    for ix,(p,ev) in enumerate(ps):
                        pid=f'p{pn}e{ep}r{ri}c{ci}:bound_period{ix}';packet['periods'][pid]={'period':p,'evidence':ev,'page':pn};pids.append(pid)
                    scope_options=[]
                    for h,c,t in headers:
                        if sc:=scope_in(t):scope_options.append((sc,[cell_evidence(page,table,h,c)]))
                    # A title-column scope may govern the whole table, but not a different column's scope.
                    if not scope_options:
                        scope_options=[(sc,[cell_evidence(page,table,h,c)]) for h,c,t in header if c<=min((a['table']['column'] for _,a in row_labels if a['table']),default=0) and (sc:=scope_in(t))]
                    for _,ra in row_labels:
                        if sc:=scope_in(ra['text']):scope_options.append((sc,[ra['evidence']]))
                    if named_scope:scope_options=[named_scope]
                    # Detached column names must be fully inside the source column rectangle.
                    if not scope_options and first==0 and box and tb:
                        scopes=[]
                        for bid,b in external:
                            if not (0<=tb[1]-b['bbox'][3]<=60):continue
                            words=[w for w in b.get('native_words',[]) if _contains_x(cell_box(table,ri,ci),w['bbox'])]
                            if words:
                                st,en=words[0]['start'],words[-1]['end'];t=b['text'][st:en]
                                if not UNIT.search(t) and not periods_in(t) and not labels(t) and not re.search(r'\d',t):scopes.append((t,_span(b,st,en)))
                        if scopes:
                            name=' '.join(t for t,_ in scopes).rstrip('#').strip();scope_options=[(scope_in(name) or name,[e for _,e in scopes])]
                    if not scope_options:
                        scope_options=[(sc,[a['evidence']]) for _,a in title if (sc:=scope_in(a['text']))]
                    if not scope_options and approach and annual:scope_options=[approach]
                    conflicting_scope=(len({s for s,_ in scope_options})>1 or any(re.search(r'\bstandalone\b',t,re.I) and re.search(r'\bconsolidated\b',t,re.I) for _,_,t in headers))
                    sids=[]
                    if len({s for s,_ in scope_options})==1:
                        sc,evs=scope_options[0];sid=f'p{pn}e{ep}r{ri}c{ci}:bound_scope';packet['scopes'][sid]={'scope':sc,'evidence':evs[0],'support_evidence':evs[1:],'page':pn};sids=[sid]
                    vid=f'{vaid}:native{start}'
                    # Replace only an identical source span, preserving source atom identity.
                    old=[v for v,x in values.items() if x['atom']==vaid and x['start']==start and x['end']==end]
                    if old:vid=old[0]
                    relation={'metrics':sorted(selected),'metric_evidence':metric_evidence,'period_ids':pids,'scope_ids':sids,
                              'support_evidence':support,'table':ep,'row':ri,'column':ci,
                              'statement_text':' '.join([table_title,column_text,row_label_text]),
                              'source_kind':va['evidence']['kind']}
                    values[vid]={'atom':vaid,'raw':token,'start':start,'end':end,'number':normalized,'frame':fid,
                                 'unit_evidence':[e for _,e in unit_options], 'column_period_ids':pids,'relationship':relation}
                    if conflicting_scope:
                        values[vid]['relation_rejection']='conflicting_scope_headers'
                        diagnostics.append({'stage':'entity_scope_attribution','value':vid,'reason':'conflicting_scope_headers'})
                    if not selected:diagnostics.append({'stage':'financial_metric_recognition','value':vid,'reason':'metric_unresolved'})
                    if len({packet['periods'][p]['period'] for p in pids})!=1:diagnostics.append({'stage':'table_header_binding','value':vid,'reason':'period_missing_or_conflicting'})
