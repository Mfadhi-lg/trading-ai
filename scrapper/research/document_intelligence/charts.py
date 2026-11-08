"""Read explicit PPTX series values, never infer points from image pixels."""
import io
import re
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .evidence import element_evidence
from .numbers import normalize_value
from .schema import ResearchEvidence


def enrich_pptx_charts(body, extraction):
    from pptx import Presentation
    presentation = Presentation(io.BytesIO(body))
    with ZipFile(io.BytesIO(body)) as archive:
        parts = [name for name in archive.namelist() if re.fullmatch(r'ppt/charts/chart\d+\.xml',name)]
    caches = {part['part']:part for part in embedded_chart_parts(body,parts)}
    for slide, page in zip(presentation.slides, extraction["pages"]):
        # Keep merged-header topology beside the unchanged canonical cells.
        for shape in slide.shapes:
            if not getattr(shape, 'has_table', False):
                continue
            element = next((e for e in page['elements'] if e['element_kind'] == 'table'
                and (e.get('reference') or {}).get('shape_name') == shape.name), None)
            if element is not None:
                element['reference']['merged_cells'] = [
                    {'row': r, 'column': c, 'row_span': cell.span_height, 'column_span': cell.span_width}
                    for r, row in enumerate(shape.table.rows) for c, cell in enumerate(row.cells)
                    if cell.is_merge_origin]
        shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        elements = [e for e in page["elements"] if e["element_kind"] == "chart"]
        if len(shapes) != len(elements):
            raise ValueError("PPTX chart identities cannot be aligned")
        for shape, element in zip(shapes, elements):
            chart = shape.chart
            title = chart.chart_title.text_frame.text if chart.has_title and chart.chart_title.has_text_frame else None
            element["reference"]["title_text"] = title
            series = []
            cache=caches.get(str(chart.part.partname).lstrip('/'))
            if cache:
                element['reference']['embedded_chart_structure']=cache
                for s in cache['series']:
                    # Use each series' own sparse original cache; never borrow
                    # categories from plot zero or collapse missing indices.
                    series.append({'name':s['name'][0] if len(s['name'])==1 else None,
                        'categories':[None if s.get('multi_level_categories') else p['category'] for p in s['points']],
                        'values':[p['value'] for p in s['points']],
                        'source_part':cache['part'],'source_series_index':s['series_index']})
            element["reference"]["embedded_series"] = series
    return extraction


def chart_signals(context, page, element, patterns, metrics):
    reference = element.get("reference") or {}
    title = reference.get("title_text") or ""
    matched = [m for m in sorted(metrics) if patterns[m].search(title)]
    hint = re.search(r"(?:INR|Rs\.?|₹|USD)\s*(?:crores?|millions?|mn|billions?|bn)", title, re.I)
    if len(matched) != 1 or not hint or not reference.get("embedded_series"):
        return []
    output = []
    for i, series in enumerate(reference["embedded_series"]):
        if not series.get('name'):
            continue
        for j, (period, raw) in enumerate(zip(series["categories"], series["values"])):
            if raw is None or not isinstance(period,str) or not re.fullmatch(r"(?:FY\s*\d{2,4}|Q[1-4]\s+FY\s*\d{2,4})", period, re.I):
                continue
            number = normalize_value(raw, unit_hint=hint.group())
            if not number:
                continue
            evidence = [element_evidence(page, element), {
                "page": page["page_or_slide_number"], "element": element["element_position"],
                "kind": "chart_cell", "series": i, "category": j, "quote": raw,
                "period": period, "series_name": series["name"], "title": title,
                "text_origin": "native"}]
            future = bool(re.search(r"guidance|forecast|target|projected", title, re.I))
            output.append(ResearchEvidence(source=context.to_dict(), metric=matched[0],
                statement_kind="GUIDANCE" if future else "HISTORICAL_FACT", status="VERIFIED",
                evidence=evidence, original_value=raw, normalized_value=number["normalized_value"],
                currency=number["currency"], unit=number["unit"], scale=number["scale"],
                value_basis="PROJECTED" if future else "ACTUAL", reporting_period=None if future else period,
                time_horizon=period if future else None, dimensions={"series": series["name"], "chart_title": title, 'numeric_class': 'exact_embedded'},
                section="presentation", reasons=["explicit_embedded_chart_data_only"]))
    return output


NS={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart','a':'http://schemas.openxmlformats.org/drawingml/2006/main','p':'http://schemas.openxmlformats.org/presentationml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def embedded_chart_parts(body,parts):
    """Read sparse original OOXML caches without python-pptx chart coercion.

    Unknown/missing points remain None. Series indices are never collapsed, and
    existing formula and relationship part identities accompany exact values.
    """
    result=[]
    with ZipFile(io.BytesIO(body)) as z:
        locations={}
        for name in z.namelist():
            if not re.fullmatch(r'ppt/slides/slide\d+\.xml',name):continue
            relname=posixpath.join(posixpath.dirname(name),'_rels',posixpath.basename(name)+'.rels')
            if relname not in z.namelist():continue
            rels={r.get('Id'):posixpath.normpath(posixpath.join(posixpath.dirname(name),r.get('Target'))) for r in ET.fromstring(z.read(relname))}
            slide=ET.fromstring(z.read(name));pn=int(re.search(r'slide(\d+)',name)[1])
            for pos,shape in enumerate(slide.find('p:cSld/p:spTree',NS)):
                chart=shape.find('.//c:chart',NS)
                if chart is None:continue
                part=rels.get(chart.get('{'+NS['r']+'}id'))
                off=shape.find('.//p:xfrm/a:off',NS);ext=shape.find('.//p:xfrm/a:ext',NS)
                box=([int(off.get('x'))/12700,int(off.get('y'))/12700,(int(off.get('x'))+int(ext.get('cx')))/12700,(int(off.get('y'))+int(ext.get('cy')))/12700] if off is not None and ext is not None else None)
                locations[part]={'page':pn,'shape_tree_index':pos,'shape_id':next((x.get('id') for x in shape.iter() if x.tag.endswith('}cNvPr')),None),'bbox':box,'slide_part':name}
        for part in parts:
            root=ET.fromstring(z.read(part));series=[]
            for si,ser in enumerate(root.findall('c:chart/c:plotArea/*/c:ser',NS)):
                name=[x.text for x in ser.findall('c:tx//c:v',NS)]
                multi=[{int(x.get('idx')):x.find('c:v',NS).text for x in level.findall('c:pt',NS)}
                       for level in ser.findall('c:cat//c:multiLvlStrCache/c:lvl',NS)]
                cat={int(x.get('idx')):x.find('c:v',NS).text for x in ser.findall('c:cat//c:pt',NS)}
                val={int(x.get('idx')):x.find('c:v',NS).text for x in ser.findall('c:val//c:pt',NS)}
                count=max([int(x.get('val')) for x in ser.findall('c:val//c:ptCount',NS)]+[max(cat.keys()|val.keys(),default=-1)+1])
                series.append({'series_index':si,'name':name,'categories':cat,'multi_level_categories':multi,'values':val,'points':[{'index':i,'category':cat.get(i),'value':val.get(i),'numeric_class':'exact_embedded' if i in val else 'unsupported_missing','source_part':part,'source_series_index':si} for i in range(count)],'formulas':[x.text for x in ser.findall('.//c:f',NS)]})
            axes=[]
            for axis in root.findall('c:chart/c:plotArea/*',NS):
                if axis.tag.rsplit('}',1)[-1] not in {'catAx','valAx','dateAx'}:continue
                axes.append({'kind':axis.tag.rsplit('}',1)[-1],'id':(axis.find('c:axId',NS).get('val') if axis.find('c:axId',NS) is not None else None),'title':[x.text for x in axis.findall('c:title//a:t',NS)],'minimum':(axis.find('c:scaling/c:min',NS).get('val') if axis.find('c:scaling/c:min',NS) is not None else None),'maximum':(axis.find('c:scaling/c:max',NS).get('val') if axis.find('c:scaling/c:max',NS) is not None else None)})
            result.append({'part':part,'original_part_sha256':__import__('hashlib').sha256(z.read(part)).hexdigest(),'classification':'native_pptx_embedded','title':[x.text for x in root.findall('c:chart/c:title//a:t',NS)],'series':series,'axes':axes,'legend_present':root.find('c:chart/c:legend',NS) is not None,'source':locations.get(part),'admission':'exact cache evidence sidecar; no financial metric/unit/scope inferred'})
    return result


