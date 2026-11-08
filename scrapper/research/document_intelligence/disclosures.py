"""Category qualification for the existing deterministic extractor.

No alternate source representation: every record keeps its canonical citations.
The legacy research export remains available for frozen regression cohorts.
"""
import re

ORDER = {'order_book', 'order_inflows', 'order_book_growth', 'execution_timeline'}
CAPACITY = {'capex', 'capacity_expansion', 'manufacturing_capacity', 'new_facility', 'capacity_utilization', 'project_delay', 'funding_plan', 'execution_timeline'}
GUIDANCE = {'growth_guidance', 'revenue_growth', 'revenue', 'operating_profit', 'profit',
            'operating_margin', 'profit_margin', 'margin_outlook', 'volume_growth',
            'volume', 'demand_outlook', 'capacity_utilization', 'management_outlook'}
RATING = {'liquidity', 'debt', 'leverage', 'interest_coverage', 'debt_servicing',
          'working_capital', 'cash_flow', 'credit_strengths', 'credit_weaknesses', 'rating_sensitivities'}
RISKS = {'project_delay', 'execution_bottleneck', 'working_capital', 'customer_concentration',
         'debt_funded_expansion', 'declining_margins', 'slowing_orders', 'regulatory_risk',
         'demand_outlook', 'supply_chain_risk', 'export_risk', 'debt'}
FOCUSED_METRICS = ORDER | CAPACITY | GUIDANCE | RATING | RISKS
FORWARD = re.compile(r'\b(?:guidance|guiding|expects?|anticipated|anticipate|will|planned|plans?|target\w*|forecast\w*|projected|budget\w*)\b', re.I)
ADVERSE = re.compile(r'\b(?:slowdown|weak demand|subdued demand|margin pressure|pressure on margins|deteriorat\w*|bottleneck\w*|delays?|delayed|disrupt\w*|shortages?|concentration|constraints?|debt pressure|stretched|challeng\w*|compress\w*|declining|slowing)\b', re.I)
NEGATED = re.compile(r'\b(?:no|without|not experiencing|not facing|absence of)\s+(?:material\s+|significant\s+|any\s+)?(?:delays?|disruptions?|pressure|constraints?|bottlenecks?|slowdown|concentration|challenges?)\b', re.I)
FAVOURABLE = re.compile(r'\b(?:healthy|adequate|comfortable|strong|negligible|low utilization|low utilisation)\b', re.I)
PROJECT = re.compile(r'\b(?:plant|project|facility|facilities|capex|capital expenditure|capacity|commission\w*|manufactur\w*)\b', re.I)


def wrapped_passages(page):
    """Join only adjacent aligned native prose blocks; keep original spans.

    No table-to-text conversion, cross-page concatenation or rewritten evidence.
    """
    from .evidence import text_evidence
    from .extract import PATTERNS
    blocks = page['elements']
    for i, element in enumerate(blocks):
        text = element.get('text_raw') or ''
        if element['element_kind'] != 'text_block' or element['text_origin'] != 'native' or not element.get('bbox'):
            continue
        if not any(PATTERNS[m].search(text) for m in FOCUSED_METRICS):
            continue
        parts, evidence, previous = [text], [text_evidence(page, text, element=element)], element
        for nxt in blocks[i+1:i+6]:
            body, a, b = nxt.get('text_raw') or '', previous['bbox'], nxt.get('bbox')
            if (re.search(r'[.!?]$', parts[-1]) or nxt['element_kind'] != 'text_block'
                    or nxt['text_origin'] != 'native' or not b or not body
                    or abs(a[0]-b[0]) > 3 or not 0 <= b[1]-a[3] <= 12
                    or re.match(r'[•●✓]|\d+[.)]\s', body)):
                break
            parts.append(body)
            evidence.append(text_evidence(page, body, element=nxt))
            previous = nxt
        if len(parts) > 1:
            yield ' '.join(parts), evidence


def project_sections(extraction):
    """Bounded native project headings and completion statements, no rewriting.

    A heading must be a standalone project/plant/port/terminal label. Ordinary
    sentences, bullets, footer text and unlabelled chart values cannot open one.
    Multi-column lines with multiple project titles remain unsupported.
    """
    from .evidence import text_evidence
    from .context import period_name
    result = {}
    for page in extraction['pages']:
        if page['selected_text_origin'] != 'native':
            continue
        text = page['selected_text']
        headings = []
        for match in re.finditer(r'[^\n]+', text):
            label = match.group().strip()
            if (not 5 <= len(label) <= 95 or not re.match(r'[A-Z]',label)
                    or label.lower() in {'greenfield port','commercial port','terminals','brownfield expansion'}
                    or re.search(r'\b(?:is|was|were|will|of|by|capacity|capex|revenue|presentation|construction|completed|site image)\b', label, re.I)):
                continue
            if re.search(r'\b(?:Port|Plant|Terminal|Pipeline Project)\b|^Expansion at\b|^LPG at\b', label, re.I):
                headings.append((match.start(),match.end(),label))
        sections = []
        for i,(start,end,label) in enumerate(headings):
            stop = headings[i+1][0] if i+1<len(headings) else len(text)
            body = text[end:stop]
            timelines = []
            progress = []
            for match in re.finditer(r'[^\n]*(?:construction work is ongoing|construction.*(?:in progress|under progress)|engineering works underway)[^\n]*',body,re.I):
                progress.append(text_evidence(page,match.group(),start=end+match.start()))
            for match in re.finditer(r'[^\n]*(?:completion by|completed by|commence in|completion in)[^\n]*', body, re.I):
                period = period_name(match.group())
                month = re.search(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}\b',match.group(),re.I)
                horizon = period or (month.group().upper() if month else None)
                if horizon:
                    timelines.append((horizon,text_evidence(page,match.group(),start=end+match.start())))
            sections.append(dict(start=start,end=stop,label=label,body=body,
                evidence=text_evidence(page,text[start:headings[i][1]],start=start),timelines=timelines,progress=progress))
        result[page['page_or_slide_number']] = sections
    return result


def sensitivity_passages(extraction):
    """Explicit agency sensitivity headings and their bounded original bodies."""
    from .evidence import text_evidence
    pattern=re.compile(r'(?P<heading>(?:Upward|Downward|Positive|Negative) factors)\s*:\s*\n'
        r'(?P<body>[\s\S]{15,1400}?)(?=\n\s*(?:(?:Upward|Downward|Positive|Negative) factors|About the Company|Liquidity\s*:|CRISIL Ratings has a policy)|\Z)',re.I)
    for page in extraction['pages']:
        if page['selected_text_origin']!='native':
            continue
        for match in pattern.finditer(page['selected_text']):
            yield match.group(),[text_evidence(page,match.group(),start=match.start())]


def review_passages(context, extraction):
    """Discover original evidence independently of complete-claim admission.

    Reuse recognition and qualification, but never promote these records. Native
    neighbours are review context, not asserted label/value or time bindings.
    Narrative table cells stay cells; no rectangular financial layout is assumed.
    """
    from .extract import PATTERNS
    from .ratings import ACTION
    from .evidence import text_evidence, cell_evidence
    from .schema import ResearchEvidence
    from .numbers import values_in

    def emit(text, evidence):
        metrics = [m for m in sorted(FOCUSED_METRICS) if PATTERNS[m].search(text)]
        if context.document_type == 'credit_rating' and ACTION.search(text) and re.search(r'\brating\w*\b', text, re.I):
            metrics.append('rating_action')
        for metric in metrics:
            raw = ResearchEvidence(source=context.to_dict(), metric=metric,
                statement_kind='UNKNOWN', status='AMBIGUOUS', evidence=list(evidence), claim_text=text)
            qualified = qualify([raw], context)
            record = qualified[0] if qualified else raw
            record.status = 'AMBIGUOUS'
            record.statement_kind, record.value_basis = 'UNKNOWN', 'UNKNOWN'
            record.reporting_period = record.time_horizon = record.as_of_date = record.effective_date = None
            record.dimensions.update(evidence_only=True,
                missing_or_ambiguous_fields=['metric_value_relationship', 'unit_relationship',
                    'period_or_horizon', 'entity_scope', 'actual_projected_status'],
                observed_values=[dict(original=m.group(), **n) for m, n in values_in(text)])
            record.reasons = list(dict.fromkeys(record.reasons + ['source_passage_for_manual_review',
                'complete_financial_attribution_not_established'] +
                (['document_extraction_incomplete'] if extraction.get('extraction_status') != 'ok' else []) +
                ([] if record.dimensions.get('disclosure_categories') else ['disclosure_category_not_established'])))
            yield record

    for page in extraction['pages']:
        if page['selected_text_origin'] != 'native':
            continue
        blocks = page['elements']
        for i, element in enumerate(blocks):
            if element['element_kind'] == 'table':
                for ri, row in enumerate(element.get('rows') or []):
                    # Preserve row relationships and each exact cell locator.
                    ev = [cell_evidence(page, element, ri, ci) for ci, value in enumerate(row) if value.strip()]
                    if ev:
                        yield from emit(' '.join(e['quote'] for e in ev), ev)
            elif element['element_kind'] == 'text_block' and element['text_origin'] == 'native' and element.get('text_raw'):
                text = element['text_raw']
                if not (any(PATTERNS[m].search(text) for m in FOCUSED_METRICS) or
                        (context.document_type == 'credit_rating' and ACTION.search(text) and re.search(r'\brating\w*\b', text, re.I))):
                    continue
                ev = [text_evidence(page, text, element=element)]
                previous = element
                # Native reading order can interleave unrelated columns. Use
                # overlapping source boxes for review context only.
                neighbours = [n for n in blocks if n['element_kind'] == 'text_block'
                              and n['text_origin'] == 'native' and n.get('bbox')
                              and element.get('bbox') and n['bbox'][1] >= element['bbox'][3]
                              and n['bbox'][1] - element['bbox'][3] <= 60
                              and min(n['bbox'][2], element['bbox'][2]) - max(n['bbox'][0], element['bbox'][0])
                              >= .5 * min(n['bbox'][2]-n['bbox'][0], element['bbox'][2]-element['bbox'][0])]
                for nxt in sorted(neighbours, key=lambda n: n['bbox'][1])[:4]:
                    a, b = previous.get('bbox'), nxt.get('bbox')
                    body = nxt.get('text_raw') or ''
                    if (not a or not b or nxt['element_kind'] != 'text_block' or nxt['text_origin'] != 'native'
                            or not body or not 0 <= b[1]-a[3] <= 24
                            or sum(len(e['quote']) for e in ev) + len(body) > 1000):
                        break
                    ev.append(text_evidence(page, body, element=nxt))
                    previous = nxt
                yield from emit(' '.join(e['quote'] for e in ev), ev)
    for text, ev in sensitivity_passages(extraction):
        yield from emit(text, ev)


def qualify(records, context, extraction=None):
    """Narrow semantic gates; ambiguous proposals retain original evidence."""
    output = []
    sections = project_sections(extraction) if extraction and context.document_type == 'presentation' else {}
    for r in records:
        r.evidence = list(r.evidence)
        text = r.claim_text or ' '.join(e.get('quote') or '' for e in r.evidence)
        if r.metric in CAPACITY and r.evidence:
            candidates = []
            for ev in r.evidence:
                if ev.get('kind') != 'page_text':
                    continue
                candidates.extend(s for s in sections.get(ev['page'],[])
                                  if s['start'] <= ev['start'] < s['end'])
            unique = {(s['start'],s['label']):s for s in candidates}
            if len(unique)==1:
                s=next(iter(unique.values()))
                r.dimensions['entity_scope'] = s['label']
                r.dimensions['project_name'] = s['label']
                r.evidence.append(s['evidence'])
                timelines={h for h,_ in s['timelines']}
                if len(timelines)==1 and not r.time_horizon:
                    r.time_horizon=next(iter(timelines))
                    r.evidence.extend(e for _,e in s['timelines'])
                elif len(timelines)>1:
                    r.status='AMBIGUOUS'
                    r.reasons.append('multiple_project_completion_horizons')
                if r.metric=='capex' and re.search(r'\bestimated\b',text,re.I):
                    r.statement_kind,r.value_basis='GUIDANCE','PROJECTED'
                r.dimensions['project_stage_from_section'] = 'UNDER_CONSTRUCTION' if s['progress'] else 'UNKNOWN'
                r.evidence.extend(s['progress'])
        categories = []
        if r.metric in ORDER and (r.metric != 'execution_timeline' or re.search(r'\border',text,re.I)):
            categories.append('order_book')
        if r.metric in CAPACITY and (r.metric not in {'project_delay','execution_timeline'} or PROJECT.search(text) or r.dimensions.get('project_name')):
            categories.append('capex_capacity')
        guidance = r.metric in GUIDANCE and FORWARD.search(text)
        if re.search(r'forward.looking statements|these statements|can be recognized|past performance is not', text, re.I):
            guidance = False
        if guidance:
            # Optimism alone and analyst questions are not management guidance.
            explicit = re.search(r'\b(?:revenue|sales|profit|margin|volume|demand|capacity|growth|guidance|forecast)\b', text, re.I)
            inherited_margin = r.metric == 'margin_outlook' and any(re.search(r'\b(?:EBITDA|operating)\s+margin', e.get('quote') or '', re.I) for e in r.evidence)
            if (explicit or inherited_margin) and (context.document_type == 'presentation' or (r.speaker or {}).get('role') == 'management'):
                categories.append('management_guidance')
        if context.document_type == 'credit_rating' and (r.metric in RATING or r.metric.startswith('rating_')):
            categories.append('credit_rating')
        adverse = bool(ADVERSE.search(text)) and not NEGATED.search(text)
        if r.metric in RISKS and adverse:
            categories.append('operational_risk')
        if r.statement_kind == 'ANALYST_QUESTION':
            continue
        if not categories:
            if r.status == 'UNSUPPORTED':
                output.append(r)
            continue
        r.dimensions['disclosure_categories'] = categories
        if context.document_type == 'credit_rating' and r.effective_date:
            # The existing normalizer's action date can be a report header date.
            # Preserve it separately; do not manufacture a rating effective date.
            r.dimensions['rating_report_date'] = r.effective_date
            if not re.search(r'\b(?:effective (?:from|on)|with effect from|w\.?e\.?f\.?)\b', text, re.I):
                r.effective_date = None
                r.reasons.append('rating_effective_date_not_explicit')
        if r.metric in RISKS:
            r.dimensions['polarity'] = 'ADVERSE' if adverse else 'FAVOURABLE' if FAVOURABLE.search(text) else 'NEUTRAL'
            if not adverse and r.statement_kind == 'RISK_WARNING':
                r.statement_kind = 'QUALITATIVE_OPINION'
            if adverse:
                r.statement_kind = 'RISK_WARNING'
                if re.search(r'\b(?:if|subject to|may|could)\b', text, re.I):
                    r.value_basis = 'CONDITIONAL'
        # Call-cover periods do not date qualitative commentary or project plans.
        from .context import time_context, PERIOD
        explicit_period, _ = time_context(text)
        if r.normalized_value is None and not explicit_period:
            r.reporting_period = None
        if r.metric in CAPACITY:
            if r.metric in {'capacity_expansion', 'manufacturing_capacity', 'new_facility'}:
                from .extract import _bind_number
                candidates = [(m, n) for m, n in _bind_number(text, r.metric)
                              if n['unit'] in {'MW', 'GW', 'MTPA', 'MT', 'tonnes', 'units'}]
                if len(candidates) == 1:
                    m, n = candidates[0]
                    # Exact whitespace must remain available to the verifier.
                    from .transcripts import exact_body
                    raw = next((exact_body(e.get('quote') or '', m.group().strip())[0]
                                for e in r.evidence if exact_body(e.get('quote') or '', m.group().strip())[0]), None)
                    if raw:
                        r.original_value = raw
                        r.normalized_value, r.currency, r.unit, r.scale = (n[k] for k in ('normalized_value', 'currency', 'unit', 'scale'))
                        r.status = 'VERIFIED' if all(e.get('text_origin') == 'native' for e in r.evidence) else 'AMBIGUOUS'
                        r.dimensions['approximate'] = n['approximate']
                elif len(candidates) > 1:
                    r.status = 'AMBIGUOUS'
                    r.reasons.append('multiple_capacity_values_without_unique_binding')
                elif r.normalized_value is None:
                    from .context import PERIOD
                    remainder=PERIOD.sub('',text)
                    if re.search(r'\d',remainder):
                        r.status='AMBIGUOUS'
                        r.reasons.append('capacity_value_or_unit_relationship_unresolved')
            stage = next((stage for pattern, stage in [
                (r'\b(?:commissioned|operationalized|operationalised)\b', 'COMMISSIONED'),
                (r'\b(?:completed|spent|incurred)\b', 'COMPLETED'),
                (r'\b(?:under construction|being built|construction underway)\b', 'UNDER_CONSTRUCTION'),
                (r'\bapproved\b', 'APPROVED'), (r'\b(?:planned|plans?|proposed|estimated|will|expect\w*|target\w*|budget\w*)\b', 'PLANNED')]
                if re.search(pattern, text, re.I)), 'UNKNOWN')
            r.dimensions['project_stage'] = stage
            if r.dimensions.get('project_stage_from_section') == 'UNDER_CONSTRUCTION' and stage in {'PLANNED','UNKNOWN'}:
                stage = r.dimensions['project_stage'] = 'UNDER_CONSTRUCTION'
            if stage in {'COMMISSIONED', 'COMPLETED'} and not FORWARD.search(text):
                r.statement_kind, r.value_basis = 'HISTORICAL_FACT', 'ACTUAL'
                r.time_horizon = None
            elif stage in {'PLANNED', 'APPROVED', 'UNDER_CONSTRUCTION'}:
                r.value_basis = 'CONDITIONAL' if re.search(r'\b(?:if|subject to|may|could)\b', text, re.I) else 'PROJECTED'
                r.statement_kind = 'CONDITIONAL_FORECAST' if r.value_basis == 'CONDITIONAL' else 'GUIDANCE'
                r.reporting_period = None
                r.time_horizon = r.time_horizon or explicit_period
        if r.metric in ORDER:
            future = bool(FORWARD.search(text))
            r.dimensions['order_status'] = 'EXPECTED' if future else 'CONFIRMED' if re.search(r'\b(?:secured|confirmed|stands?|stood|existing|as of|as on|received|awarded|won)\b', text, re.I) else 'UNKNOWN'
            if future:
                r.value_basis = 'CONDITIONAL' if re.search(r'\b(?:if|may|could|subject to)\b', text, re.I) else 'PROJECTED'
                r.statement_kind = 'CONDITIONAL_FORECAST' if r.value_basis == 'CONDITIONAL' else 'TARGET' if r.statement_kind=='TARGET' else 'GUIDANCE'
                r.reporting_period = None
            elif r.dimensions['order_status'] == 'CONFIRMED':
                r.value_basis, r.statement_kind = 'ACTUAL', 'HISTORICAL_FACT'
            r.dimensions['market'] = 'DOMESTIC' if re.search(r'\bdomestic\b', text, re.I) and not re.search(r'\bexports?\b', text, re.I) else 'EXPORT' if re.search(r'\bexports?\b', text, re.I) and not re.search(r'\bdomestic\b', text, re.I) else 'UNKNOWN'
            if r.metric in {'order_book', 'order_inflows', 'order_book_growth'} and r.normalized_value is None:
                r.status = 'AMBIGUOUS'
                r.reasons.append('order_value_relationship_not_established')
            if re.search(r'\b(?:means|defined as|definition)\b', text, re.I):
                r.status = 'AMBIGUOUS'
                r.reasons.append('definition_not_dated_order_observation')
            if r.normalized_value is not None and r.value_basis == 'ACTUAL' and not r.reporting_period and not r.as_of_date:
                r.status = 'AMBIGUOUS'
                r.reasons.append('order_observation_date_relationship_unresolved')
        # Preserve explicitly named subsidiaries/segments; never replace issuer ID.
        named = re.search(r'\b(?:subsidiary|segment|division)\s*[:–-]\s*([A-Za-z][A-Za-z &.-]+?)(?=\s+(?:has|had|will|expects?|order|capex)|[,;]|$)', text, re.I)
        if named:
            r.dimensions['entity_scope'] = named[1].strip()
        elif re.search(r'\b(?:subsidiary|segment|division)\b', text, re.I) and not r.dimensions.get('entity_scope'):
            r.status = 'AMBIGUOUS'
            r.reasons.append('named_scope_relationship_unresolved')
        if r.metric == 'rating_sensitivities':
            r.dimensions['sensitivity_direction'] = 'POSITIVE' if re.search(r'positive factors|upward|upgrade', text, re.I) and not re.search(r'negative factors|downward|downgrade', text, re.I) else 'NEGATIVE' if re.search(r'negative factors|downward|downgrade', text, re.I) and not re.search(r'positive factors|upward|upgrade', text, re.I) else 'UNKNOWN'
            r.value_basis = 'CONDITIONAL'
            if len(text.strip())<35:
                r.status='AMBIGUOUS'
                r.reasons.append('sensitivity_heading_without_condition')
        if r.metric in {'order_book', 'order_inflows', 'capex'} and r.normalized_value is not None and r.currency is None:
            r.status = 'AMBIGUOUS'
            r.reasons.append('currency_not_explicit')
        if r.metric == 'rating_action' and re.search(r'long\s*term\s*/\s*short\s*term', r.dimensions.get('facility',''), re.I):
            r.status = 'AMBIGUOUS'
            r.reasons.append('combined_facility_tenor_identity_unresolved')
        if r.value_basis in {'PROJECTED', 'CONDITIONAL'}:
            r.reporting_period = None
        if r.normalized_value is not None and r.value_basis=='UNKNOWN' and r.metric!='rating_action':
            r.status='AMBIGUOUS'
            r.reasons.append('numeric_disclosure_basis_not_established')
        if r.metric in {'capacity_expansion','manufacturing_capacity'} and re.search(r'\bfrom\b.+\bto\b',text,re.I):
            r.status='AMBIGUOUS'
            r.reasons.append('capacity_from_to_requires_separate_current_and_target_binding')
        if r.metric in GUIDANCE and r.value_basis == 'PROJECTED' and PERIOD.search(text) and not r.time_horizon:
            r.status = 'AMBIGUOUS'
            r.reasons.append('guidance_horizon_relationship_unresolved')
        output.append(r)
    return output
