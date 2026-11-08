"""Research-only adapters to the canonical official transcript segmenter."""
from dataclasses import replace
import re

from ...documents.transcript import (parse_official_transcript, OfficialTranscript,
                                    OfficialTranscriptSegment, TranscriptFragment)
from .evidence import text_evidence

TIMESTAMP = re.compile(r"^(?:Sh|Mr\.?|Ms\.?)\s+(.+?)\s+\d{2}:\d{2}:\d{2}\s*[–—-]\s*\d{2}:\d{2}:\d{2}\s*\((.+)\)\s*$")
TITLE = r"(?:Managing\s+Director|Chief\s+Financial\s+Officer|CFO|CEO|Executive\s+Director|Company\s+Secretary|Head[^\n,;]{0,20}Investor\s+Relations|President)"


def name_key(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def roster(extraction):
    result = {}
    patterns = [
        re.compile(r"(?:MR\.?|MS\.?|Sh)\s+([A-Z][A-Za-z .]+?)\s*(?:--|[–—,-])\s*(" + TITLE + r")", re.I),
        re.compile(r"(?:MR\.?|MS\.?)\s+([A-Z][A-Za-z .]+?),\s*(MD)[,\s]+(?:of\s+)?([A-Z][A-Z]+)\b", re.I),
    ]
    for page in extraction['pages'][:4]:
        for pattern in patterns:
            for m in pattern.finditer(page['selected_text']):
                name = re.sub(r'\s+', ' ', m[1]).strip()
                item = {'role': 'management', 'title': re.sub(r'\s+', ' ', m[2]),
                        'identity_evidence': text_evidence(page, m.group(), start=m.start())}
                if len(m.groups()) > 2 and m[3]:
                    item['entity_scope'] = m[3]
                result[name_key(name)] = item
    # Explicit moderator introductions establish analysts; absence in a roster
    # alone is never proof that an unnamed participant is an analyst.
    for page in extraction['pages']:
        for m in re.finditer(r'(?i)(?:next|first)\s+question\s+is\s+from\s+(?:the\s+line\s+of\s+)?([A-Z][A-Za-z .]+?)(?:\s+from\s+|\s+of\s+|\.|\n)', page['selected_text']):
            key = name_key(m[1])
            if key not in result:
                result[key] = {'role': 'analyst', 'identity_evidence': text_evidence(page, m.group(), start=m.start())}
    return result


def parse(extraction):
    ordinary = parse_official_transcript(extraction)
    if not any(TIMESTAMP.match(line.strip()) for page in extraction['pages'][2:] for line in page['selected_text'].splitlines()):
        return ordinary
    # Timestamped headings have a body on following lines. Read the exact page
    # text so no synthetic heading/body changes enter canonical evidence.
    segments, fragments, name, heading = [], [], None, None
    section = 'unknown'
    def flush():
        nonlocal fragments
        if name and fragments:
            segments.append(OfficialTranscriptSegment(len(segments), name, name,
                '\n'.join(f.body_raw for f in fragments), section,
                fragments[0].page_number, fragments[-1].page_number, None, None, 'native', tuple(fragments)))
        fragments = []
    # Skip participant/timing index before the explicit Transcript heading.
    started = False
    for page in extraction['pages']:
        for position, raw in enumerate(page['selected_text'].splitlines()):
            line = raw.strip()
            if line.lower() == 'transcript:':
                started = True
                continue
            if not started:
                continue
            if m := TIMESTAMP.match(line):
                flush()
                name, heading = m[1].strip(), m[2].lower()
                section = 'qa' if re.search(r'q\s*&\s*a|questions?', heading) else 'prepared_remarks'
            elif name and line and not line.startswith('©'):
                fragments.append(TranscriptFragment(page['page_or_slide_number'], None, position,
                    'selected_page_text', page['selected_text_origin'], raw, raw, None))
    flush()
    return replace(ordinary, segments=tuple(segments))


def exact_body(container, body):
    """Locate compacted tokens and return the original whitespace-bearing span."""
    match = re.search(r'\s+'.join(re.escape(t) for t in body.split()), container)
    return (match.group(), match.start()) if match else (None, None)


def corroborated_body(container, body):
    """Recover split native tokens only against an exact canonical page span.

    Never concatenate digits speculatively. The returned value is an unchanged
    slice of container, which must itself contain the unsplit native token.
    """
    tokens = body.split()
    pattern = ''
    for i, token in enumerate(tokens):
        if i:
            prev = tokens[i-1]
            numeric_gap = prev[-1:].isdigit() and token[:1].isdigit()
            fiscal_gap = prev.upper() == 'FY' and token[:1].isdigit()
            pattern += r'\s*' if numeric_gap or fiscal_gap else r'\s+'
        pattern += re.escape(token)
    match = re.search(pattern, container) if pattern else None
    return (match.group(), match.start()) if match else (None, None)
