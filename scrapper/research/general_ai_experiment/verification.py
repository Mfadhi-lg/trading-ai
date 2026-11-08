"""Independent mechanical checks. Source matches NEVER imply semantic acceptance."""
import re
from scrapper.research.document_intelligence.evidence import text_evidence, validate_evidence
from scrapper.research.document_intelligence.schema import ResearchEvidence
from scrapper.research.document_intelligence.transcripts import exact_body, parse as parse_transcript, name_key, roster
from scrapper.research.document_intelligence.numbers import normalize_value, values_in


def verify(event, context, extraction, units):
    rejected, pending, checks, evidence = [], [], {}, []
    checks['document_identity'] = event['document_id'] == context.document_id
    if not checks['document_identity']:
        rejected.append('wrong_document_identity')
    page = next((p for p in extraction['pages'] if p['page_or_slide_number'] == event['source_page']), None)
    checks['page_identity'] = page is not None
    quote = event['exact_source_quote']
    quote_match_mode = None
    if page:
        if quote in page['selected_text'] and page['selected_text_origin'] == 'native':
            evidence = [text_evidence(page, quote)]
            quote_match_mode = 'exact'
        else:
            for element in page['elements']:
                if element.get('text_origin') == 'native' and quote in (element.get('text_raw') or ''):
                    evidence = [text_evidence(page, quote, element=element)]
                    quote_match_mode = 'exact'
                    break
            if not evidence and page['selected_text_origin'] == 'native':
                original_span, offset = exact_body(page['selected_text'], quote)
                if original_span:
                    evidence = [text_evidence(page, original_span, start=offset)]
                    quote_match_mode = 'whitespace_only_reanchored_to_unchanged_canonical_span'
                elif re.search(r'\\u[0-9a-fA-F]{4}', quote):
                    # Source packets are JSON; some models copy literal JSON
                    # Unicode escapes. Decode only that lossless representation,
                    # and accept only when it reanchors to the unchanged source.
                    decoded = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), quote)
                    original_span, offset = exact_body(page['selected_text'], decoded)
                    if original_span:
                        evidence = [text_evidence(page, original_span, start=offset)]
                        quote_match_mode = 'json_unicode_escapes_and_whitespace_reanchored_to_original'
                        pending.append('literal_json_escape_in_model_quote_representation')
    probe = ResearchEvidence(source=context.to_dict(), metric='general_ai_proposal',
        statement_kind='UNKNOWN', status='AMBIGUOUS', evidence=evidence,
        parser_version='general-ai-experiment-v1', dimensions={'review_only': True})
    checks['exact_native_quote'] = bool(evidence) and validate_evidence(probe, extraction)
    if not checks['exact_native_quote']:
        rejected.append('quote_not_in_identified_native_page_or_element_visual_review_required')
    # Strict lexical checks are necessary, not proof that a number belongs to a metric.
    for field, name in [('reported_value', 'verbatim_value'), ('unit', 'verbatim_unit'),
                        ('entity', 'entity_in_quote'), ('segment_or_facility', 'scope_in_quote'),
                        ('period_or_as_of_date', 'period_in_quote')]:
        value = event[field]
        matches = value in quote if value is not None else None
        if field == 'reported_value' and value is not None and re.search(r'\d', value):
            prefix = r'(?<![\d.,])' if value[:1].isdigit() else ''
            suffix = r'(?!\d|[.,]\d)' if value[-1:].isdigit() else ''
            matches = re.search(prefix + re.escape(value) + suffix, quote) is not None
        if field == 'unit' and value is not None and not matches and event['reported_value']:
            # 'Rs. crore' can be separated by the numeric value in the original.
            # Establish only the same scale/currency, never a metric relationship.
            proposed = normalize_value(event['reported_value'], unit_hint=value)
            claimed_unit = normalize_value('1', unit_hint=value)
            compatible_label = claimed_unit is not None and proposed is not None and all(
                claimed_unit[k] == proposed[k] for k in ('currency', 'unit', 'scale'))
            if proposed and compatible_label:
                matches = any(all(n[k] == proposed[k] for k in ('normalized_value', 'currency', 'unit', 'scale'))
                              for _, n in values_in(quote))
        checks[name] = matches
        if value is not None and not matches:
            if field in {'reported_value', 'unit'}:
                rejected.append(field + '_not_verbatim_in_quote')
            else:
                pending.append(field + '_relationship_requires_separate_source_review')
    numeric = bool(re.search(r'\d', event['reported_value'] or ''))
    if numeric and not event['unit']:
        pending.append('numeric_unit_or_scale_not_established')
    checks['speaker_attribution'] = None
    if context.document_type == 'transcript' and evidence:
        transcript = parse_transcript(extraction)
        original_quote = evidence[0]['quote']
        matches = [s for s in transcript.segments if s.page_start <= event['source_page'] <= s.page_end
                   and exact_body(s.text_raw, original_quote)[0]]
        identities = {name_key(s.speaker_normalized): s for s in matches}
        claimed = name_key(event['speaker_or_agency'] or '')
        if len(identities) == 1 and claimed:
            actual, segment = next(iter(identities.items()))
            checks['source_speaker_normalized'] = segment.speaker_normalized
            if claimed == actual:
                checks['speaker_attribution'] = True
            elif claimed in roster(extraction):
                checks['speaker_attribution'] = False
                rejected.append('speaker_attribution_conflicts_with_unique_canonical_segment')
    pending.extend(['metric_value_unit_relationship_requires_review', 'rating_action_requires_review',
                    'actual_forecast_period_requires_review', 'speaker_attribution_requires_review',
                    'event_category_and_direction_requires_review'])
    source_ids = [u['record']['record_id'] for u in units
                  if u['original_page'] == event['source_page']
                  and quote in u['search_text']]
    return {'event': event, 'source_verification': 'rejected' if rejected else 'source_matched',
        'semantic_review_status': 'pending', 'historical_ml_eligible': False,
        'checks': checks, 'rejection_reasons': rejected, 'review_reasons': pending,
        'quote_match_mode': quote_match_mode,
        'canonical_evidence': evidence, 'source_evidence_ids': source_ids,
        'source_href': next((u['source_href'] for u in units if u['original_page'] == event['source_page']), None)}
