"""Credit Rating Intelligence 1.0: pinned, offline replay; no model execution.

Reviewed replay is an explicit trust boundary: exact source checks do not prove
semantic entailment. A changed/new candidate needs a new source review. Neither
reference answers nor benchmark match scores participate in admission.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from datetime import date
import json
from pathlib import Path
import re
import sqlite3
from unittest.mock import patch

from .schema import DocumentContext, ResearchEvidence
from .evidence import canonical_hash, text_evidence, validate_evidence
from .offline import deny_network, extract_local
from .ratings import events
from ...documents.credit_rating import normalize_credit_rating

VERSION = 'credit-rating-intelligence/1.0.0'
GROUPS = {
    'rating': ['facility', 'tenor', 'current_rating', 'previous_rating', 'action', 'outlook',
               'facility_amount', 'watch', 'withdrawal', 'action_date'],
    'liquidity': ['assessment', 'cash_and_liquid_balances', 'bank_limit_utilisation',
                  'repayment_obligations', 'cash_accruals', 'explanations_and_constraints'],
    'assessment': ['accounting_approach', 'revenue_and_profitability', 'revenue_explanations',
                   'profitability_explanations', 'debt_and_leverage', 'interest_coverage_and_debt_protection',
                   'working_capital_intensity', 'operational_strengths', 'business_weaknesses',
                   'concentration_risks', 'capex_and_execution_risks', 'agency_expectations',
                   'upgrade_sensitivities', 'downgrade_sensitivities', 'material_conditions',
                   'raw_material_and_forex_risk', 'pricing_and_cost_pass_through',
                   'order_book_and_execution', 'payment_and_covenant_risk', 'group_support'],
}
FIELD_NAMES = [f'{g}.{f}' for g, fs in GROUPS.items() for f in fs]
PHASE5_MAP = {'SUPPORTED': 'EVIDENCED_STATEMENT', 'NEEDS_REVIEW': 'AMBIGUOUS', 'WITHHELD': 'UNSUPPORTED'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def checked_file(root, descriptor):
    p = (root / descriptor['path']).resolve()
    if digest(p) != descriptor['sha256']:
        raise ValueError(f'Input hash mismatch: {p}')
    return p


@contextmanager
def offline_guard():
    def denied(*args, **kwargs):
        raise RuntimeError('Credit V1 forbids database connections')
    with deny_network(), patch.object(sqlite3, 'connect', denied):
        yield


def parse_saved_response(response):
    content = response['choices'][0]['message']['content'].strip()
    # Remove only a transport-level Markdown fence; never repair JSON or fields.
    if content.startswith('```json\n') and content.endswith('```'):
        content = content[8:-3].strip()
    return json.loads(content)['disclosures']


def field_state(claim_ids, *, complete_source_review=False, explicit_absence=False):
    if claim_ids:
        return 'DISCLOSED'
    if complete_source_review and explicit_absence:
        return 'NOT_DISCLOSED'
    return 'UNRESOLVED'


def coverage_claim_ids(accepted, field):
    """Count reviewed atomic coverage once while retaining all raw disclosures.

    A child replaces its parent's coverage link only for the same field. Exact
    repeated atomic propositions share a link even when proposal keys differ.
    This is conservative identity deduplication, not semantic equivalence NLP.
    """
    relevant = [c for c in accepted if field in c['fields']]
    parents = {c['origin']['parent_claim_id'] for c in relevant if 'atomic_key' in c}
    seen, ids = set(), []
    for claim in relevant:
        if claim['claim_id'] in parents:
            continue
        if 'atomic_key' in claim:
            key = canonical_hash({k: claim[k] for k in (
                'statement', 'value', 'unit', 'period', 'period_kind',
                'accounting_scope', 'attribution', 'statement_kind', 'conditions', 'qualifications')})
            if key in seen:
                continue
            seen.add(key)
        ids.append(claim['claim_id'])
    return ids


def attach_evidence(context, extraction, evidence):
    record = ResearchEvidence(context.to_dict(), 'credit_disclosure', 'AGENCY_ASSESSMENT',
                              'EVIDENCED_STATEMENT', evidence=evidence)
    if not evidence or not validate_evidence(record, extraction):
        raise ValueError('Source-region integrity failure')
    result = []
    for ev in evidence:
        region = f"p{ev['page']}:e{ev.get('element')}:r{ev.get('row')}:c{ev.get('column')}:{ev['start']}-{ev['end']}"
        result.append({**ev, 'source_document_sha256': context.content_sha256,
                       'region_id': region, 'canonical_sha256': canonical_hash(extraction),
                       'validation': 'EXACT_SOURCE_LOCATOR_VALIDATED'})
    return result


def replay_candidate(candidate, review, context, extraction, origin):
    """CandidateProvider boundary shared by future live and current replay intake.

    Reviews pin the whole candidate and extraction, not a keyword/reference match.
    Without that prior whole-passage review the claim stays NEEDS_REVIEW.
    """
    decision, reason = 'NEEDS_REVIEW', 'No pinned whole-claim source review'
    evidence = []
    if review:
        if review['candidate_sha256'] != canonical_hash(candidate):
            decision, reason = 'WITHHELD', 'Candidate changed since source review'
        elif review['canonical_sha256'] != canonical_hash(extraction):
            decision, reason = 'WITHHELD', 'Source representation changed since review'
        else:
            decision, reason = review['decision'], review['reason']
            pages = {p['page_or_slide_number']: p for p in extraction['pages']}
            try:
                for pn in review['support_pages']:
                    page = pages[pn]
                    if page['selected_text_origin'] != 'native' or not page['selected_text'].strip():
                        raise ValueError('Unreviewed OCR/unusable page')
                    evidence.append(text_evidence(page, page['selected_text']))
                evidence = attach_evidence(context, extraction, evidence)
            except (KeyError, ValueError):
                evidence = []
                decision, reason = 'WITHHELD', 'Reviewed source pages cannot be resolved'
    claim_id = canonical_hash({'source': context.content_sha256, 'candidate': candidate, 'origin': origin})
    return dict(claim_id=claim_id, fields=review['fields'] if review else [],
                statement=candidate.get('statement'), original_candidate=candidate,
                value=candidate.get('reported_value'), unit=candidate.get('unit'),
                period=candidate.get('period'), accounting_scope=candidate.get('accounting_scope'),
                attribution=candidate.get('attribution_or_speaker'),
                statement_kind=candidate.get('statement_kind'), qualifications=candidate.get('source_qualifications', []),
                evidence_decision=decision, phase5_status=PHASE5_MAP[decision],
                evidence=evidence, validation={'source_locators': bool(evidence),
                    'semantic_basis': 'pinned whole-claim offline source review; not automatic entailment',
                    'reason': reason}, origin=origin)


def deterministic_ratings(context, extraction):
    if extraction['extraction_status'] != 'ok':
        return []
    normalized = normalize_credit_rating(extraction)
    claims = []
    for record in events(context, extraction, normalized):
        if not validate_evidence(record, extraction):
            raise ValueError('Rating evidence failed')
        ev = record.evidence
        cell = next((e for e in ev if e['kind'] == 'table_cell'), None)
        # Join by physical table row, never facility name (EKC has two LT rows).
        amount = next((a for a in normalized.actions if cell and
            a.source_row_position == cell['row'] and
            normalized.source_tables[a.source_table_index].page_number == cell['page'] and
            normalized.source_tables[a.source_table_index].element_position == cell['element']), None)
        value = dict(record.normalized_value, tenor=record.dimensions['rating_tenor'],
                     facility_id=f"{context.content_sha256}:p{cell['page']}:e{cell['element']}:r{cell['row']}" if cell else record.record_id,
                     facility_amount={'value': amount.amount_decimal, 'original': amount.amount_display,
                                      'unit': amount.unit_code, 'basis': 'facility limit, not outstanding debt'} if amount and amount.amount_decimal else None,
                     action_date=record.effective_date,
                     watch=None, withdrawal=record.normalized_value['action'] == 'withdrawn')
        native = all(e['text_origin'] == 'native' for e in ev)
        decision = 'SUPPORTED' if record.status == 'VERIFIED' and native and extraction['extraction_status'] == 'ok' else 'NEEDS_REVIEW'
        fs = ['rating.facility', 'rating.tenor', 'rating.current_rating', 'rating.action']
        if value['outlook']: fs.append('rating.outlook')
        if value['previous_grade']: fs.append('rating.previous_rating')
        if value['facility_amount']: fs.append('rating.facility_amount')
        if value['withdrawal']: fs.append('rating.withdrawal')
        claims.append(dict(claim_id=record.to_dict()['record_id'], fields=fs,
            statement=record.original_value, value=value, unit=None, period=None,
            accounting_scope='facility', attribution=normalized.agency_code, qualifications=record.reasons,
            statement_kind='RATING_EVENT', evidence_decision=decision, phase5_status=record.status,
            evidence=attach_evidence(context, extraction, ev),
            validation={'source_locators': True, 'reason': '; '.join(record.reasons)},
            origin={'mode': 'deterministic_extraction', 'implementation': 'ratings.events + D04 normalization'}))
    # Only an explicit section label is normalized; surrounding numbers are not inferred.
    for page in extraction['pages']:
        m = re.search(r'(?m)^Liquidity:\s*(Strong|Adequate|Stretched|Weak)\s*$', page['selected_text'])
        if m and page['selected_text_origin'] == 'native' and extraction['extraction_status'] == 'ok':
            ev = attach_evidence(context, extraction, [text_evidence(page, m.group(), start=m.start())])
            claims.append(dict(claim_id=canonical_hash(ev), fields=['liquidity.assessment'],
                statement=m.group(), value=m[1].upper(), unit=None, period=None, accounting_scope=None,
                attribution=normalized.agency_code, qualifications=[], statement_kind='AGENCY_ASSESSMENT',
                evidence_decision='SUPPORTED', phase5_status='EVIDENCED_STATEMENT', evidence=ev,
                validation={'source_locators': True, 'reason': 'Explicit liquidity section label'},
                origin={'mode': 'deterministic_extraction'}))
    return claims


def build_record(manifest_path, *, root=None):
    """Read-only callable interface. Returns a JSON-serializable V1 record."""
    manifest_path = Path(manifest_path).resolve()
    root = Path(root).resolve() if root else Path(__file__).resolve().parents[3]
    with offline_guard():
        config = read_json(manifest_path)
        original = checked_file(root, config['pdf'])
        canonical_path = checked_file(root, config['canonical']) if config.get('canonical') else None
        extraction = read_json(canonical_path) if canonical_path else extract_local(original.read_bytes(), '.pdf')
        context = DocumentContext(**config['context'])
        if context.content_sha256 != digest(original):
            raise ValueError('Document identity/hash mismatch')
        from pypdf import PdfReader
        page_count = len(PdfReader(str(original)).pages)
        if len(extraction['pages']) != page_count or [p['page_or_slide_number'] for p in extraction['pages']] != list(range(1, page_count + 1)):
            raise ValueError('Canonical page coverage mismatch')
        inventory = context.provenance.get('acquisition_receipt') or context.provenance['source_inventory']
        checked_file(root, inventory)
        if context.provenance.get('acquisition_receipt'):
            acquisition = read_json(checked_file(root, inventory))
            for key in ('company_name', 'symbol', 'document_id', 'content_sha256', 'report_date', 'source_url'):
                if acquisition.get(key) != getattr(context, key):
                    raise ValueError('Fresh acquisition identity mismatch: ' + key)
            if config['company_identifier'] != context.symbol:
                raise ValueError('Fresh company identifier mismatch')
        cover = extraction['pages'][0]
        if context.company_name not in cover['selected_text']:
            raise ValueError('Company identity absent from original cover')
        if context.report_date:
            d = date.fromisoformat(context.report_date)
            if d.strftime('%B ') + str(d.day) + d.strftime(', %Y') not in cover['selected_text']:
                raise ValueError('Report date absent from original cover')
        reviews = read_json(checked_file(root, config['reviews']))
        if reviews['source_sha256'] != context.content_sha256:
            raise ValueError('Review belongs to another document')
        claims = deterministic_ratings(context, extraction)
        receipts = [config['pdf'], config.get('canonical'), config['reviews'], inventory]
        for source in config['responses']:
            path = checked_file(root, source)
            raw = read_json(path)
            for index, candidate in enumerate(parse_saved_response(raw)):
                if source.get('selected_indices') is not None and index not in source['selected_indices']:
                    continue
                key = f"{raw['id']}:/disclosures/{index}"
                review = reviews['claims'].get(key)
                if candidate.get('document_type') != 'credit_rating' or candidate.get('company') != context.company_name:
                    raise ValueError('Replay contains a different document family')
                claims.append(replay_candidate(candidate, review, context, extraction,
                    {'mode': 'replayed_ai', 'model': raw['model'], 'response_id': raw['id'],
                     'response_path': source['path'], 'response_sha256': source['sha256'],
                     'json_pointer': f'/disclosures/{index}'}))
            receipts.append(source)
        if config.get('candidate_bundle'):
            from .credit_candidate_provider import load_candidate_bundle
            bundle_path = checked_file(root, config['candidate_bundle'])
            fresh_reviews = read_json(checked_file(root, config['fresh_reviews'])) if config.get('fresh_reviews') else None
            fresh_claims, _ = load_candidate_bundle(bundle_path, context, extraction, reviews=fresh_reviews)
            claims.extend(fresh_claims)
            receipts.extend([config['candidate_bundle'], config.get('fresh_reviews')])
        if config.get('atomic_candidates'):
            from .credit_atomic import derive_claims
            packet = read_json(checked_file(root, config['atomic_candidates']))
            claims.extend(derive_claims(packet, claims, context, extraction))
            receipts.append(config['atomic_candidates'])
        accepted = [c for c in claims if c['evidence_decision'] == 'SUPPORTED']
        queue = [c for c in claims if c['evidence_decision'] != 'SUPPORTED']
        ledger = {}
        for field in FIELD_NAMES:
            ids = coverage_claim_ids(accepted, field)
            pending = [c['claim_id'] for c in queue if field in c['fields']]
            ledger[field] = {'state': field_state(ids), 'claim_ids': ids, 'review_claim_ids': pending,
                'completeness': 'PARTIAL' if ids else 'UNRESOLVED',
                'reason': 'Supported disclosure present; exhaustive recall not established' if ids else 'No accepted finding; absence not established'}
        if any(digest(root / r['path']) != r['sha256'] for r in receipts if r):
            raise ValueError('Input changed during replay')
        identity = context.to_dict()
        identity.update(company_id=config['company_identifier'], company_id_namespace='repository research symbol',
                        source_pdf=config['pdf'], rating_agency=normalize_credit_rating(extraction).agency_code if extraction['extraction_status'] == 'ok' else None,
                        reporting_periods=sorted({c['period'] for c in accepted if c.get('period')}),
                        reporting_periods_note='Literal claim period fields; statements may contain additional comparative periods')
        identity['metadata_status']['company_id'] = 'KNOWN'
        identity['field_states'] = {k: 'DISCLOSED' if identity.get(k) is not None and identity.get(k) != [] else 'UNRESOLVED'
            for k in ('company_id', 'company_name', 'document_id', 'content_sha256', 'rating_agency', 'report_date',
                      'publication_date', 'filing_date', 'reporting_periods', 'source_pdf')}
        identity['evidence'] = attach_evidence(context, extraction, [text_evidence(cover, cover['selected_text'])])
        result = {'schema_version': VERSION, 'identity': identity,
            'certification': 'offline development slice; not production-certified',
            'claims': accepted, 'groups': {g: {f: ledger[f'{g}.{f}'] for f in fs} for g, fs in GROUPS.items()},
            'coverage_ledger': ledger, 'evidence_review_queue': queue,
            'source_coverage': {'physical_pages': page_count,
                'native_pages': [p['page_or_slide_number'] for p in extraction['pages'] if p['selected_text_origin'] == 'native' and p['selected_text'].strip()],
                'replayed_claim_pages': sorted({c['original_candidate']['physical_page'] for c in claims if 'original_candidate' in c and isinstance(c['original_candidate'], dict) and 'physical_page' in c['original_candidate']}),
                'full_document_semantic_review': False, 'omissions': reviews['omissions']},
            'provenance': {'inputs': receipts, 'manifest_sha256': digest(manifest_path),
                           'canonical_sha256': canonical_hash(extraction)},
            'execution': {'network': 'denied', 'database_connections': 'denied', 'inference_calls': 0,
                          'modes': ['deterministic_extraction', 'replayed_ai']}}
        if config.get('atomic_candidates'):
            result['source_coverage']['atomic_review'] = {
                'packet': config['atomic_candidates'],
                'accepted_keys': [c['atomic_key'] for c in accepted if 'atomic_key' in c],
                'note': 'Omissions above retain the historical review assessment. Atomic claims add partial coverage; consult the current ledger. No exhaustive absence review.'}
        if config.get('candidate_bundle'):
            result['execution']['modes'].append('generated_ai')
            result['source_coverage']['fresh_candidate_pages'] = sorted({e['page'] for c in claims if c['origin']['mode'] == 'generated_ai' for e in c['evidence']})
        validate_record(result)
        return result


def validate_record(record):
    """Cross-field invariants in addition to the published JSON Schema."""
    if record['schema_version'] != VERSION or set(record['coverage_ledger']) != set(FIELD_NAMES):
        raise ValueError('V1 contract mismatch')
    accepted = {c['claim_id']: c for c in record['claims']}
    if len(accepted) != len(record['claims']):
        raise ValueError('Duplicate claim identity')
    for claim in record['claims']:
        if claim['evidence_decision'] != 'SUPPORTED' or not claim['evidence']:
            raise ValueError('Unverified claim in accepted collection')
        if not set(claim['fields']).issubset(FIELD_NAMES):
            raise ValueError('Unknown field')
        for ev in claim['evidence']:
            if ev['source_document_sha256'] != record['identity']['content_sha256'] or not ev['quote']:
                raise ValueError('Evidence identity mismatch')
    for field, entry in record['coverage_ledger'].items():
        if entry['state'] != field_state(entry['claim_ids']):
            raise ValueError('Coverage claim/state mismatch')
        for claim_id in entry['claim_ids']:
            if claim_id not in accepted or field not in accepted[claim_id]['fields']:
                raise ValueError('Coverage refers to unaccepted or unrelated claim')
    for claim in record['evidence_review_queue']:
        if claim['evidence_decision'] not in {'NEEDS_REVIEW', 'WITHHELD'}:
            raise ValueError('Invalid review queue decision')


def render_markdown(record):
    identity = record['identity']
    lines = [f"# {identity['company_name']} — Credit Rating Intelligence V1", '', record['certification'], '',
             f"Report date: {identity['report_date']}. Original SHA-256: `{identity['content_sha256']}`.", '',
             'DISCLOSED means at least one supported finding, not complete coverage. Values retain agency definitions and literal periods.', '',
             '## Accepted disclosures', '']
    for c in record['claims']:
        lines += [f"### {', '.join(c['fields'])}", '', c['statement'], '',
                  f"Origin: {c['origin']['mode']}; scope: {c['accounting_scope']}; period: {c['period']}; unit: {c['unit']}.", '',
                  '<details><summary>Exact source support and qualifications</summary>', '']
        for e in c['evidence']:
            lines += [f"Physical page {e['page']}, `{e['region_id']}` ({e['text_origin']}):", '', '```text', e['quote'], '```', '']
        lines += [str(c['qualifications']), '', '</details>', '']
        if 'atomic_key' in c:
            lines += [f"Atomic candidate: {c['atomic_key']}; evidence: {c['evidence_decision']}; "
                      f"date basis: {c['period_kind']}; derivation: {c['derivation_basis']}.", '',
                      f"Conditions: {c['conditions']}", '',
                      f"Field completeness: {c['field_completeness']}", '']
    lines += ['## Unresolved fields', ''] + [f'- {f}: {v["reason"]}' for f, v in record['coverage_ledger'].items() if v['state'] == 'UNRESOLVED']
    lines += ['', '## Material omissions', ''] + [f'- {o}' for o in record['source_coverage']['omissions']]
    lines += ['', '## Evidence review queue', '']
    for c in record['evidence_review_queue']:
        lines += [f"- **{c['evidence_decision']}** {c['statement']} — {c['validation']['reason']}"]
    return '\n'.join(lines) + '\n'


def export_record(record, output, stem):
    output = Path(output).resolve()
    # New-only output, restricted to a development artifact directory or temp.
    repo = Path(__file__).resolve().parents[3]
    allowed = [repo / 'artifacts' / 'credit-rating-intelligence-v1', Path('/tmp').resolve()]
    if not any(output.is_relative_to(p) for p in allowed) or output.exists():
        raise ValueError('Use a new directory under artifacts/credit-rating-intelligence-v1 or /tmp')
    if not re.fullmatch(r'[a-z0-9-]+', stem):
        raise ValueError('Invalid output stem')
    output.mkdir(parents=True)
    for name, data in [(f'{stem}-credit-intelligence.json', record),
                       (f'{stem}-coverage-ledger.json', record['coverage_ledger']),
                       (f'{stem}-evidence-review-queue.json', record['evidence_review_queue'])]:
        (output / name).write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    (output / f'{stem}-credit-intelligence.md').write_text(render_markdown(record))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stem', required=True)
    args = parser.parse_args()
    record = build_record(args.manifest)
    export_record(record, args.output, args.stem)
    print(json.dumps({'accepted': len(record['claims']), 'review': len(record['evidence_review_queue']),
                      'unresolved': [k for k, v in record['coverage_ledger'].items() if v['state'] == 'UNRESOLVED']}))


if __name__ == '__main__':
    main()
