"""Offline atomic adapter. Proposals never inherit parent scope, dates or admission.

A reviewed packet is a curated trust input, not automatic semantic entailment.
Every atom and all supporting locators are pinned together by its review.
"""
from copy import deepcopy
from .evidence import canonical_hash
from .credit_v1 import attach_evidence, FIELD_NAMES, PHASE5_MAP


def derive_claims(packet, parents, context, extraction):
    parents = {c['claim_id']: c for c in parents}
    results = []
    seen = set()
    for proposal in packet['candidates']:
        atom = deepcopy(proposal)
        key = atom['atomic_key']
        if key in seen:
            raise ValueError('Duplicate atomic key')
        seen.add(key)
        parent = parents.get(atom['parent_claim_id'])
        decision, reason = 'NEEDS_REVIEW', 'No pinned atomic source review'
        evidence = []
        review = packet.get('reviews', {}).get(key)
        identity_ok = (packet['source_sha256'] == context.content_sha256 and
                       packet['canonical_sha256'] == canonical_hash(extraction) and parent is not None and
                       atom['parent_candidate_sha256'] == canonical_hash(parent.get('original_candidate')))
        if not identity_ok:
            decision, reason = 'WITHHELD', 'Source or retained parent identity mismatch'
        else:
            try:
                evidence = attach_evidence(context, extraction, atom['support'])
                if (extraction['extraction_status'] != 'ok' and 'visual_sources' not in atom) or any(e['text_origin'] != 'native' for e in evidence):
                    raise ValueError('Unreviewed extraction')
            except (ValueError, KeyError, TypeError):
                evidence = []
                decision, reason = 'WITHHELD', 'Atomic source locators cannot be verified'
            if evidence and review:
                if review['candidate_sha256'] != canonical_hash(atom):
                    decision, reason = 'WITHHELD', 'Atomic candidate changed since source review'
                else:
                    decision, reason = review['decision'], review['reason']
        if 'visual_sources' not in atom and any('\ufffd' in e.get('quote', '') for e in evidence):
            decision, reason = 'WITHHELD', 'Damaged native evidence requires separately reviewed visual binding'
        if 'visual_sources' in atom:
            # Opt-in only: historical atomic outputs remain byte-compatible.
            try:
                from .credit_visual import validate_visual
                if not identity_ok or not evidence or not atom['visual_sources']:
                    raise ValueError('Missing visual/native source binding')
                if atom['derivation_basis'] != 'source_review_addition':
                    raise ValueError('Visual correction must be separately versioned reviewer derivation')
                for binding in atom['visual_sources']:
                    validate_visual(binding, context, extraction, atom['support'])
                if not review or review.get('verification_method') != 'visual_pdf_inspection' or not review.get('reviewer') or not review.get('reviewed_at') or review.get('exact_source_review') is not True:
                    decision, reason = 'NEEDS_REVIEW', 'Exact original-page visual review required'
                elif review.get('candidate_sha256') != canonical_hash(atom):
                    decision, reason = 'WITHHELD', 'Visual atom changed since review'
            except (ValueError, KeyError, TypeError, OSError):
                decision, reason = 'WITHHELD', 'Visual original-page binding cannot be verified'
        required = ('statement', 'value', 'unit', 'period', 'period_kind', 'accounting_scope',
                    'attribution', 'statement_kind', 'qualifications', 'conditions', 'derivation_basis')
        if any(k not in atom for k in required) or not atom.get('fields') or not set(atom['fields']).issubset(FIELD_NAMES):
            raise ValueError('Incomplete atomic proposal contract')
        if decision not in PHASE5_MAP:
            raise ValueError('Invalid atomic decision')
        claim = {k: atom[k] for k in required}
        if 'disclosure_category' in atom:
            claim['disclosure_category'] = atom['disclosure_category']
        claim.update(claim_id=canonical_hash({'source': context.content_sha256, 'atom': atom}),
            atomic_key=key, fields=atom['fields'], evidence=evidence,
            evidence_decision=decision, phase5_status=PHASE5_MAP[decision],
            field_completeness={k: 'KNOWN' if atom[k] is not None else 'UNRESOLVED'
                                for k in ('value', 'unit', 'period', 'accounting_scope', 'attribution')},
            validation={'source_locators': bool(evidence), 'reason': reason,
                        'semantic_basis': 'pinned whole-atom offline source review; not automatic entailment'},
            origin={'mode': 'deterministic_extraction', 'implementation': 'credit_atomic.derive_claims',
                    'parent_claim_id': atom['parent_claim_id'],
                    'parent_candidate_sha256': atom['parent_candidate_sha256'],
                    'derivation_basis': atom['derivation_basis']})
        if 'visual_sources' in atom:
            claim['visual_sources'] = deepcopy(atom['visual_sources'])
            claim['verification_methods'] = ['visual_pdf_inspection']
        results.append(claim)
    return results
