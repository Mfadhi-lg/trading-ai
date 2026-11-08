"""Material field checklist: omissions, evidence failures and parser gaps differ."""
from .credit_v1 import FIELD_NAMES
from .evidence import canonical_hash


def coverage_checklist(catalog, claims=(), *, absence_reviews=None):
    pages = [p['page'] for p in catalog['pages']]
    unresolved = [r['id'] for r in catalog['regions'] if r['state'] != 'AVAILABLE']
    parser_pages = [p['page'] for p in catalog['pages'] if p['state'] != 'AVAILABLE']
    result = {}
    for field in FIELD_NAMES:
        relevant = [c for c in claims if field in c['fields']]
        accepted = [c['claim_id'] for c in relevant if c['evidence_decision'] == 'SUPPORTED']
        pending = [c['claim_id'] for c in relevant if c['evidence_decision'] == 'NEEDS_REVIEW']
        failed = [c['claim_id'] for c in relevant if c['evidence_decision'] == 'WITHHELD']
        status = 'PARTIAL' if accepted else 'EVIDENCE_FAILURE' if failed else 'NEEDS_REVIEW' if pending else 'MISSING_CANDIDATE'
        review = (absence_reviews or {}).get(field)
        if review:
            if (relevant or review.get('catalog_sha256') != canonical_hash(catalog)
                    or review.get('reviewed_pages') != pages or review.get('decision') != 'NOT_DISCLOSED'
                    or not review.get('reviewer') or not review.get('reason') or not review.get('exact_source_review')):
                raise ValueError('Invalid full-document absence review')
            status = 'NOT_DISCLOSED'
        result[field] = {'status': status, 'accepted_claim_ids': accepted, 'review_claim_ids': pending,
            'failed_claim_ids': failed, 'absence_review': review,
            'parser_unresolved_pages': parser_pages, 'unresolved_region_ids': unresolved,
            'complete': bool(review), 'note': 'A supported atom establishes partial coverage only; source recall requires review.'}
    return {'catalog_sha256': canonical_hash(catalog), 'included_pages': pages,
        'fields': result, 'full_document_recall_verified': False}
