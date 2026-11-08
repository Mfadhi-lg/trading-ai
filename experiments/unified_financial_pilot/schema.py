"""Strict shared evidence contract. A source match is not semantic admission."""
from dataclasses import asdict, dataclass, fields
import re
from typing import Literal

CATEGORIES = ('Financial resilience', 'Growth and order visibility', 'Execution',
              'Competitive position', 'Management guidance and consistency', 'Business risks',
              'Unresolved information')
KINDS = ('reported_fact', 'management_claim', 'management_guidance',
         'credit_agency_expectation', 'derived_calculation', 'analyst_inference', 'unclassified')

BASE_CHECKS = ('value', 'unit', 'period', 'scope', 'column_association', 'attribution',
               'qualifications', 'rating_action_status')
PRESENTATION_CHECKS = ('business_association', 'period_association', 'source_region')


def review_checks(document_type):
    return BASE_CHECKS + (PRESENTATION_CHECKS if document_type == 'presentation' else ())


@dataclass
class Evidence:
    company_id: str
    document_id: str
    document_type: Literal['credit_rating', 'presentation', 'transcript']
    source_filename: str
    source_sha256: str
    physical_page: int
    category: str
    metric: str
    statement: str
    quote: str
    reported_value: str | None = None
    unit: str | None = None
    scale: str | None = None
    period: str | None = None
    effective_date: str | None = None
    scope: str | None = None
    table_chart_context: str | None = None
    speaker_name: str | None = None
    speaker_role: str | None = None
    attribution: str | None = None
    statement_kind: str = 'unclassified'
    qualifiers: list[str] | None = None
    related_pages: list[int] | None = None
    extraction_method: str = 'native_source_passage'
    representation_id: str | None = None
    validation_status: str = 'needs_review'
    anchor_verified: bool = False
    unresolved_reasons: list[str] | None = None
    review: dict | None = None
    source_region_id: str | None = None
    source_span_ids: list[str] | None = None

    def __post_init__(self):
        for name in ('company_id','document_id','document_type','source_filename','source_sha256','category','metric','statement','quote'):
            if not isinstance(getattr(self,name),str) or not getattr(self,name).strip():
                raise ValueError('Missing required string: '+name)
        if self.category not in CATEGORIES or self.statement_kind not in KINDS:
            raise ValueError('Invalid category or statement kind')
        if self.document_type not in ('credit_rating', 'presentation', 'transcript'):
            raise ValueError('Invalid document type')
        if type(self.physical_page) is not int or self.physical_page < 1:
            raise ValueError('Physical pages are one-based integers')
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in ('qualifiers', 'unresolved_reasons', 'source_span_ids'):
                if value is not None and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
                    raise ValueError(f'Invalid {f.name}')
            elif f.name == 'related_pages':
                if value is not None and (not isinstance(value, list) or any(type(v) is not int or v < 1 for v in value)):
                    raise ValueError('Invalid related pages')
            elif f.name in ('anchor_verified',):
                if type(value) is not bool: raise ValueError('Invalid boolean')
            elif f.name == 'review':
                if value is not None and not isinstance(value, dict): raise ValueError('Invalid review')
            elif f.name != 'physical_page' and value is not None and not isinstance(value, str):
                raise ValueError(f'Invalid {f.name}')
        if not self.quote.strip() or not self.metric.strip() or not self.statement.strip():
            raise ValueError('Empty evidence')
        if len(self.source_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.source_sha256):
            raise ValueError('Invalid source hash')

    def to_dict(self):
        row = asdict(self)
        # Historical records/IDs and replay artifacts keep their exact contract.
        for key in ('source_region_id', 'source_span_ids'):
            if row[key] is None:
                del row[key]
        return row


def validate(record: Evidence, pages: dict[int, str], expected_hash: str, *, region_context=None) -> Evidence:
    """Only human adjudication can admit semantics; quoted text alone is insufficient."""
    reasons = []
    region = next((r for r in (region_context or {}).get('regions', [])
                   if r['id'] == record.source_region_id and r['status']=='proposed'), None)
    fragments = {f['id']:f for f in (region or {}).get('fragments', [])}
    cited = record.source_span_ids or []
    region_bound = bool(region and cited and set(cited) <= fragments.keys() and
                        (region_context or {}).get('physical_page') == record.physical_page)
    fragment_match = region_bound and any(record.quote in fragments[s]['text'] for s in cited)
    record.anchor_verified = (record.source_sha256 == expected_hash and
                              (record.quote in pages.get(record.physical_page, '') or fragment_match))
    if not record.anchor_verified: reasons.append('Original physical-page quote/hash mismatch')
    if record.reported_value is not None and not re.search(r'(?<![\w.,])'+re.escape(record.reported_value)+r'(?![\w.,])',record.quote):
        reasons.append('Value not verbatim in quote; no numerical admission')
    if record.reported_value in ('-', '–', '—', ''):
        reasons.append('Dash/empty value is missing, never zero')
    if record.related_pages and any(p not in pages for p in record.related_pages):
        reasons.append('Related page outside source')
    checks = review_checks(record.document_type)
    review = record.review or {}
    if not (review.get('reviewer') and review.get('reviewed_at') and
            review.get('independent') is True and
            all(review.get(k) in ('correct', 'not_applicable') for k in checks) and
            (record.document_type != 'presentation' or
             (region_bound and fragment_match and review.get('business_association')=='correct' and
              review.get('source_region')=='correct'))):
        reasons.append('Independent source review required for semantic admission')
    record.validation_status = 'accepted' if not reasons else 'needs_review'
    record.unresolved_reasons = reasons
    return record
