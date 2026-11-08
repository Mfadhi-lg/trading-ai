"""Facility-specific agency table events, retaining unresolved dates explicitly."""
import re

from .evidence import cell_evidence, text_evidence, element_evidence
from .schema import ResearchEvidence
from dataclasses import replace

GRADE = re.compile(r'\b(?:(CARE|IND|BWR)\s+)?(AAA|AA[+-]?|A[1-4][+]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC|CC|C|D)(?![A-Za-z0-9+-])', re.I)
ACTION = re.compile(r'\b(upgrad\w*|downgrad\w*|reaffirm\w*|affirm\w*|assign\w*|withdraw\w*|withdrawn|revis\w*)\b', re.I)
EVENTS = {'upgraded': 'UPGRADE', 'downgraded': 'DOWNGRADE', 'reaffirmed': 'AFFIRMATION',
          'assigned': 'ASSIGNMENT', 'withdrawn': 'WITHDRAWAL', 'revised': 'OUTLOOK_REVISION'}


def action_code(text):
    m = ACTION.search(text)
    if not m:
        return None
    raw = m.group().lower()
    return next((code for prefix, code in [('upgrad','upgraded'),('downgrad','downgraded'),
                 ('reaffirm','reaffirmed'),('affirm','reaffirmed'),('assign','assigned'),
                 ('withdraw','withdrawn'),('revis','revised')] if raw.startswith(prefix)), None)


def grades(text):
    result = []
    agency = None
    text = re.sub(r'\s+', ' ', text)
    for m in GRADE.finditer(text):
        if not m[1] and not re.search(r'/\s*$', text[:m.start()]):
            continue
        agency = m[1].upper() if m[1] else agency
        if not agency:
            continue
        grade = agency + ' ' + m[2].upper()
        tail = text[m.end():]
        outlook = re.match(r'\s*[;/]\s*(Stable|Positive|Negative)\b', tail, re.I)
        result.append((grade, outlook[1].lower() if outlook else None))
    return result


def events(context, extraction, normalized):
    result = []
    if normalized.agency_code is None and any(re.search(r'\bBrickwork Ratings\b', p['selected_text'], re.I) for p in extraction['pages']):
        normalized = replace(normalized, agency_code='brickwork', agency_raw='Brickwork Ratings')
    def emit(page, ev, facility, grade, outlook, raw, previous=None, ambiguous=None):
        code = action_code(raw)
        ambiguous = list(ambiguous or [])
        if not code or not grade or not facility or not normalized.agency_code:
            ambiguous.append('unresolved_rating_event_fields')
        if code == 'revised' and 'outlook' not in raw.lower():
            ambiguous.append('rating_revision_type_unknown')
        ev = list(ev)
        for source_page in extraction['pages']:
            for field in ('agency_raw', 'action_date_raw'):
                source_text = getattr(normalized, field)
                if source_text and source_text in source_page['selected_text']:
                    ev.append(text_evidence(source_page, source_text))
                    break
        result.append(ResearchEvidence(source=context.to_dict(), metric='rating_action',
            statement_kind='RATING_EVENT', status='AMBIGUOUS' if ambiguous else 'VERIFIED', evidence=ev,
            original_value=raw, normalized_value={'agency': normalized.agency_code, 'facility': facility,
                'grade': grade, 'previous_grade': previous, 'outlook': outlook, 'action': code},
            value_basis='UNKNOWN', effective_date=normalized.action_date,
            dimensions={'facility': facility, 'entity_scope': 'facility',
                'rating_tenor': 'SHORT_TERM' if grade and re.search(r'\bA[1-4]\+?$', grade) else 'LONG_TERM' if grade else 'UNKNOWN',
                'event_type': EVENTS.get(code, 'UNKNOWN') if not ambiguous else 'UNKNOWN',
                'is_grade_change': code in {'upgraded', 'downgraded'} if not ambiguous else None,
                'as_of_basis': 'explicit_rating_action_date' if normalized.action_date else 'UNKNOWN'},
            reasons=ambiguous + ([] if normalized.action_date else ['effective_rating_date_unknown'])
                    + ['rating_as_of_observation_not_current_status']))
    for page in extraction['pages']:
        for element in page['elements']:
            rows = element.get('rows') or []
            if element['element_kind'] != 'table' or not rows:
                continue
            unsupported = [(ri,ci,raw) for ri,row in enumerate(rows) for ci,raw in enumerate(row)
                if re.search(r'\b(?:CRISIL|ICRA)\s+(?:AAA|AA|A|BBB|BB|B|C|D)',raw,re.I) and ACTION.search(raw)]
            for ri,ci,raw in unsupported:
                result.append(ResearchEvidence(source=context.to_dict(),metric='rating_action',
                    statement_kind='UNKNOWN',status='UNSUPPORTED',original_value=raw,
                    evidence=[cell_evidence(page,element,ri,ci)],
                    reasons=['agency_grade_and_facility_grammar_not_supported']))
            top = [re.sub(r'\s+', ' ', x).lower() for x in rows[0]]
            f = next((i for i, h in enumerate(top) if re.search(r'facilit|instrument type', h)), None)
            a = next((i for i, h in enumerate(top) if 'rating action' in h), None)
            g = next((i for i, h in enumerate(top) if 'rating' in h and 'action' not in h and 'histor' not in h), None)
            brick = (normalized.agency_code == 'brickwork' and len(top) == 4 and 'tenure' in top
                     and len(rows) > 1 and len(rows[1]) == 4 and rows[1][-1].lower() == 'present'
                     and rows[1][-2].lower().startswith('previous'))
            if f is None or g is None or (a is None and not brick):
                continue
            for r, row in enumerate(rows[2:] if brick else rows[1:], 2 if brick else 1):
                if (brick and len(row) != 6) or (not brick and max(f,g,a) >= len(row)):
                    continue
                facility = re.sub(r'\s+', ' ', row[f]).strip()
                rating = row[5] if brick else row[g]
                raw = rating if brick else row[a]
                codes = grades(rating)
                if not ACTION.search(raw) or not codes:
                    continue
                ev = [cell_evidence(page, element, r, c) for c in range(len(row)) if row[c].strip()]
                ev += [cell_evidence(page, element, h, c) for h in range(2 if brick else 1)
                       for c in range(len(rows[h])) if rows[h][c].strip()]
                if len(codes) == 1:
                    previous = grades(row[4])[0][0] if brick and grades(row[4]) else None
                    # CARE/IND often put the comparator in the action cell.
                    # This is prior evidence, never a second current instrument.
                    prior = re.search(r'\bfrom\s+(.+)', raw, re.I)
                    prior_grades = grades(prior[1]) if prior else []
                    if len(prior_grades) == 1 and action_code(raw) in {'upgraded', 'downgraded'}:
                        previous = prior_grades[0][0]
                    distinct = {action_code(m.group()) for m in ACTION.finditer(raw)}
                    if len(distinct) > 1:
                        emit(page, ev, facility, *codes[0], raw, previous,
                             ['multiple_rating_actions_in_single_facility'])
                    else:
                        emit(page, ev, facility, *codes[0], raw, previous)
                else:
                    # Split only when action scope is common or explicitly LT/ST.
                    parts = raw.split('/')
                    if len(parts) == len(codes) and all(ACTION.search(p) for p in parts):
                        for (grade, outlook), part in zip(codes, parts):
                            emit(page, ev, facility, grade, outlook, part.strip())
                    elif len({action_code(m.group()) for m in ACTION.finditer(raw)}) == 1:
                        for grade, outlook in codes:
                            emit(page, ev, facility, grade, outlook, raw)
                    else:
                        emit(page, ev, facility, None, None, raw, ambiguous=['combined_rating_requires_facility_split'])
    # Explicit issuer actions can be absent from instrument tables. Accept only
    # a named issuer rating with both action and grade in the same sentence.
    for page in extraction['pages']:
        for m in re.finditer(r'[^.!?]*\blong.term\s+issuer rating\b[^.!?]*', page['selected_text'], re.I):
            quote = m.group()
            if not re.search(r'has\s+(?:also\s+)?(?:upgraded|downgraded|affirmed|withdrawn)\s+(?:the\s+)?long.term\s+issuer rating', quote, re.I):
                continue
            code = action_code(quote)
            gs = grades(quote)
            if not code or not gs or re.search(r'\b(?:may|can|could|policy)\b', quote, re.I):
                continue
            ev = [text_evidence(page, quote, start=m.start())]
            prev = gs[1][0] if len(gs) == 2 and 'from' in quote.lower() else None
            for act in dict.fromkeys(action_code(x.group()) for x in ACTION.finditer(quote)):
                word = next(x.group() for x in ACTION.finditer(quote) if action_code(x.group()) == act)
                emit(page, ev, 'Long-Term Issuer Rating', *gs[0], word, prev)
    return result
