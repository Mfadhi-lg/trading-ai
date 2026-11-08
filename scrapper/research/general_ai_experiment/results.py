"""Summarize measured outputs without presenting source matching as accuracy."""
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from .runner import OUT, load, dump, now, budget_used, context_for, sha
from .verification import verify
from .protocol import validate_output


def summarize():
    manifest = {r['document_id']: r for r in load(OUT / 'development/manifest.json')['documents']}
    for dataset in ('holdout', 'holdout-final'):
        if (OUT / dataset / 'manifest.json').exists():
            manifest.update({r['document_id']: {**r, '_dataset': dataset} for r in load(OUT / dataset / 'manifest.json')['documents']})
    groups = defaultdict(list)
    for path in (OUT / 'runs').glob('*/*.json'):
        row = load(path)
        if row.get('requested_model') and row.get('document_id') and row.get('strategy'):
            if row.get('schema_valid') is True:
                # Qualify all outputs with one verifier version, preserving raw
                # result files from earlier processes and transport revisions.
                import json
                raw = row['response']['choices'][0]['message']['content'] if row.get('response') else row['response_text']
                parsed = json.loads(raw)
                if not validate_output(parsed):
                    raise ValueError('Stored schema-valid output no longer validates')
                source = manifest[row['document_id']]
                directory = OUT / source.get('_dataset', 'development') / source['content_sha256']
                row['proposals'] = [verify(e, context_for(source), load(directory / 'canonical.json'),
                                          load(directory / 'source-units.json')) for e in parsed['events']]
                dump(OUT / 'reverified' / path.parent.name / path.name,
                    {'original_result_sha256': sha(path), 'verifier_sha256': sha(Path(__file__).with_name('verification.py')),
                     'proposals': row['proposals']})
            groups[(row['requested_model'], row['strategy'], path.parent.name)].append((path, row))
    results = []
    for (model, strategy, stage), pairs in sorted(groups.items()):
        rows = [r for _, r in pairs]
        responses = [r for r in rows if r.get('response') or 'response_text' in r]
        proposals = [p for r in rows for p in r.get('proposals', [])]
        usage = [(r.get('response') or {}).get('usage') or ({'prompt_tokens': r['input_tokens'], 'completion_tokens': r['output_tokens'], 'cost': 0} if 'input_tokens' in r else {}) for r in rows]
        costs = [u['cost'] for u in usage if u.get('cost') is not None]
        result = {'model': model, 'strategy': strategy, 'stage': stage,
            'request_count': len(rows), 'response_count': len(responses),
            'failed_requests': sum(r.get('status') == 'failed' for r in rows),
            'schema_valid_requests': sum(r.get('schema_valid') is True for r in rows),
            'schema_rate_over_attempts': sum(r.get('schema_valid') is True for r in rows) / len(rows),
            'schema_rate_over_responses': sum(r.get('schema_valid') is True for r in rows) / len(responses) if responses else None,
            'truncated_responses': sum((r.get('response') or {}).get('choices', [{}])[0].get('finish_reason') == 'length' for r in responses),
            'abstaining_requests': sum(r.get('abstained') is True for r in rows),
            'proposed_events': len(proposals),
            'source_matched_proposals': sum(p['source_verification'] == 'source_matched' for p in proposals),
            'canonical_quote_matched_proposals': sum(p['checks'].get('exact_native_quote') is True for p in proposals),
            'mechanically_rejected_proposals': sum(p['source_verification'] == 'rejected' for p in proposals),
            'semantically_accepted_events': 0,
            'semantic_review_pending': len(proposals),
            'unsupported_fabricated_claims': None,
            'event_precision': None, 'event_recall': None, 'numeric_accuracy': None,
            'unit_accuracy': None, 'entity_scope_accuracy': None, 'time_basis_accuracy': None,
            'chart_relationship_accuracy': None,
            'accuracy_status': 'independent_adjudication_pending_not_inferred_from_quote_matches',
            'seconds': sum(r.get('seconds', 0) for r in rows),
            'prompt_tokens': sum(u.get('prompt_tokens', 0) for u in usage),
            'completion_tokens': sum(u.get('completion_tokens', 0) for u in usage),
            'actual_reported_cost_usd': str(sum((Decimal(str(c)) for c in costs), Decimal('0'))),
            'cost_responses_known': len(costs),
            'result_files': [str(p.relative_to(OUT)) for p, _ in pairs]}
        results.append(result)
    ledger = load(OUT / 'spend-ledger.json') if (OUT / 'spend-ledger.json').exists() else {'requests': []}
    actual = sum((Decimal(r['actual_usd']) for r in ledger['requests'] if r['actual_usd'] is not None), Decimal('0'))
    summary = {'at': now(), 'model_results': results,
        'actual_reported_inference_cost_usd': str(actual),
        'actual_cost_complete': all(r['actual_usd'] is not None for r in ledger['requests']),
        'actual_plus_unresolved_max_reservations_usd': str(budget_used(ledger)),
        'ceiling_usd': '10', 'requests_in_ledger': len(ledger['requests']),
        'uncertain_billing_requests': [r['request_id'] for r in ledger['requests'] if r['actual_usd'] is None],
        'important': 'Schema rate and source match counts are measured; financial-event correctness requires independent adjudication. Source rejections are not automatically hallucinations.',
        'holdout_status': 'executed_with_provisional_labels_independent_human_adjudication_pending' if any(k[2].startswith('holdout') for k in groups) else 'not_yet_executed',
        'original_phase5_replaced': False}
    dump(OUT / 'measured-results.json', summary)
    return summary


if __name__ == '__main__':
    import json
    print(json.dumps(summarize(), indent=2))
