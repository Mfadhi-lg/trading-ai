"""Source-only challenge checklist and secondary classification payloads.

References are provisional researcher notes, never model input or ground truth.
"""
import json
import re
from .protocol import JEV, classification_questions
from .runner import OUT, ROOT, check_freeze, dump, load, sha, freeze, now

CHALLENGES = [
    ('reaffirmed_grade_downgraded_outlook', 'Acuite', 2, r"Acuite has reaffirmed[\s\S]*?outlook is revised from 'Stable' to 'Negative'\.", 'Keep grade reaffirmation and outlook downgrade separate.'),
    ('long_term_upgrade_short_term_affirmation', 'India_Ratings', 2, r'India Ratings and Research[\s\S]*?as follows:', 'Long term IND AA- to IND AA; short term IND A1+ affirmed.'),
    ('advisory_not_reaffirmation', 'CRISIL', 2, r'Please note: This advisory should not be construed as a rating reaffirmation\.', 'Advisory itself is not a new reaffirmation.'),
    ('assigned_upgraded_withdrawn_facilities', 'CARE_', 1, None, 'Keep each facility, action and issue period distinct; cross-check annexures.'),
    ('revised_historical_rating_information', 'Infomerics', 1, r'This is with reference[\s\S]*?2024\.', 'Historical date correction is not a fresh July 2025 action.'),
    ('order_book_excludes_on_hold', 'Acuite', 3, r'The company has an unexecuted order book[\s\S]*?April 30th 2025\.', 'Do not add the excluded Tendaho orders to the unexecuted order book.'),
    ('management_vs_analyst_question', 'concall_transcripts/Everest_Kanto_Cylinder_Q3', 7, r'Puneet Khurana: What we will do[\s\S]*?off hand here\.', 'Nearby analyst EBITDA arithmetic is not management-confirmed guidance.'),
    ('qualitative_not_numeric_guidance', 'concall_transcripts/Everest_Kanto_Cylinder_Q3', 7, r'Sanjiv Kapur: Next quarter[\s\S]*?better than this year\.', 'Qualitative UAE expectations do not establish numerical guidance.'),
    ('subsidiary_segment_attribution', 'concall_transcripts/Everest_Kanto_Cylinder_Q4', 4, r'Puneet Khurana: USA is around[\s\S]*?100 crores\.', 'Different currencies and business scopes must not be combined.'),
    ('similar_chart_different_metrics', 'presentations/Everest_Kanto_Cylinder_Q4', 7, None, 'EBITDA, PBT and PAT are separate; visually review value/column relationships.'),
    ('apparent_financial_inconsistency', 'presentations/Everest_Kanto_Cylinder_Q4', 8, None, 'Compare pages 6/8/17 and distinguish scope, rounding and genuine inconsistencies; preserve every published value.'),
    ('million_crore_foreign_currency', 'India_Ratings', 2, None, 'INR7,000 million is not 7,000 crore; compare original USD quotations separately.'),
    ('report_date_vs_public_availability', 'Brickwork', 1, None, 'Filing cover/report date does not certify exact public availability of this version.'),
]


def prepare_review():
    directory = OUT / 'source-review'
    if (directory / 'freeze.json').exists():
        return {'status': 'review_already_frozen'}
    prepared = OUT / 'development'
    check_freeze(prepared)
    rows = load(prepared / 'manifest.json')['documents']
    checklist, classification = [], []
    for name, hint, pn, pattern, note in CHALLENGES:
        row = next(r for r in rows if hint in r['path'])
        source = prepared / row['content_sha256']
        text = load(source / 'canonical.json')['pages'][pn - 1]['native_text']
        match = re.search(pattern, text) if pattern else None
        quote = match.group() if match else text
        checklist.append({'challenge_id': name, 'document_id': row['document_id'],
            'content_sha256': row['content_sha256'], 'page': pn, 'original': row['path'],
            'source_quote': quote, 'source_quote_sha256': __import__('hashlib').sha256(quote.encode()).hexdigest(),
            'reviewer_note': note, 'label_status': 'provisional_not_independently_adjudicated',
            'coverage': 'annotated_window_only_not_exhaustive_document_labels',
            'source_href': (ROOT / row['path']).as_uri() + '#page=' + str(pn),
            'needs_visual_review': pattern is None, 'pattern_matched': match is not None if pattern else None})
        if match:
            units = load(source / 'source-units.json')
            source_ids = [u['record']['record_id'] for u in units if u['original_page'] == pn and quote in u['search_text']]
            classification.append({'passage_id': name, 'document_id': row['document_id'], 'content_sha256': row['content_sha256'],
                'source_page': pn, 'exact_source_quote': quote, 'source_evidence_ids': source_ids,
                'jev_payload': {'model': JEV, 'state': {'passage': quote, 'document_id': row['document_id'], 'page': pn},
                                'questions': classification_questions()},
                'general_model_task': 'Return JSON with event_category and event_direction using the identical typed-question definitions.',
                'general_model_state': {'passage': quote, 'document_id': row['document_id'], 'page': pn},
                'questions': classification_questions()})
    dump(directory / 'challenge-checklist.json', checklist)
    dump(directory / 'classification-inputs.json', classification)
    dump(directory / 'protocol.json', {'frozen_at': now(), 'secondary_only': True,
        'jev_endpoint': 'https://openrouter.ai/api/v1/systemone',
        'classification_not_document_reading_ranking': True,
        'labels_not_in_model_payload': True,
        'direction_is_business_direction_not_stock_return': True,
        'classification_execution_status': 'not_run_exact_jev_id_absent_and_cloud_key_missing'})
    freeze(directory)
    return {'status': 'source_review_frozen', 'challenges': len(checklist), 'source_verified_passages': len(classification)}


if __name__ == '__main__':
    print(json.dumps(prepare_review(), indent=2))
