"""Secondary, source-passage-only Jev/general-model comparison; never PDF ranking."""
import fcntl
import json
import re
import time
from decimal import Decimal

from .protocol import JEV, CATEGORIES, DIRECTIONS, classification_questions
from .runner import OUT, load, dump, sha, now, api_key, request, budget_charge_bound, can_reserve, reasoning_settings, check_freeze


def prepare():
    path = OUT / 'classification-inputs-public.json'
    if path.exists():
        return load(path)
    rows = load(OUT / 'development/manifest.json')['documents']
    policy = load(OUT / 'sharing-review.json')['documents']
    passages = []
    for old in load(OUT / 'source-review/classification-inputs.json'):
        if policy[old['content_sha256']]['external_allowed']:
            passages.append({k: old[k] for k in ('passage_id', 'document_id', 'content_sha256', 'source_page', 'exact_source_quote', 'source_evidence_ids')})
    icra = next(r for r in rows if 'ICRA_Venus' in r['path'])
    ex = load(OUT / 'development' / icra['content_sha256'] / 'canonical.json')
    units = load(OUT / 'development' / icra['content_sha256'] / 'source-units.json')
    for key, pn, pattern in [
        ('icra_downgrade_watch', 1, r'Venus Jewel: Ratings downgraded[\s\S]*?Negative Implications'),
        ('icra_liquidity', 2, r'The firm’s liquidity position[\s\S]*?June 30, 2025\.'),
        ('icra_risk', 1, r'The latest round of US tariffs[\s\S]*?credit profile is expected to remain moderated\.'),
    ]:
        text = ex['pages'][pn - 1]['native_text']
        match = re.search(pattern, text)
        if not match:
            continue
        quote = match.group()
        passages.append({'passage_id': key, 'document_id': icra['document_id'], 'content_sha256': icra['content_sha256'],
            'source_page': pn, 'exact_source_quote': quote,
            'source_evidence_ids': [u['record']['record_id'] for u in units if u['original_page'] == pn and quote in u['search_text']]})
    for p in passages:
        row = next(r for r in rows if r['document_id'] == p['document_id'])
        p['state'] = {'passage': p['exact_source_quote'], 'document_id': p['document_id'],
            'page': p['source_page'], 'source_type': row['document_type']}
        if row['document_type'] == 'credit_rating':
            p['state']['speaker_or_agency'] = 'ICRA'
        baseline = load(OUT / 'development' / p['content_sha256'] / 'baseline.json')
        categories = set(); identifiers = []
        for record in baseline['focused_records']:
            if record['status'] not in {'VERIFIED', 'EVIDENCED_STATEMENT'} or record['dimensions'].get('evidence_only'):
                continue
            evidence = record.get('evidence') or []
            quote = (evidence[0].get('quote') or '') if evidence else ''
            compact = lambda s: re.sub(r'\s+', '', s).lower()
            if len(quote) < 12 or compact(quote) not in compact(p['exact_source_quote']):
                continue
            identifiers.append(record['record_id'])
            crosswalk = {'credit_rating': 'rating' if record['metric'] == 'rating_action' else 'liquidity_leverage_working_capital',
                'order_book': 'orders', 'management_guidance': 'guidance', 'capex_capacity': 'capex_capacity', 'operational_risk': 'risk'}
            categories.update(crosswalk[c] for c in record['dimensions'].get('disclosure_categories', []) if c in crosswalk)
        p['deterministic'] = {'categories': sorted(categories), 'record_ids': identifiers, 'event_direction': None,
            'direction_supported': False, 'method': 'Existing focused claims with primary evidence contained in this exact passage; taxonomy crosswalk only. No new directional rules.'}
    result = {'frozen_at': now(), 'passages': passages, 'questions': classification_questions(),
        'holdout_sources_included': False, 'secondary_only_not_document_reading_accuracy': True,
        'independent_semantic_ground_truth': False}
    dump(path, result)
    return result


def run():
    check_freeze(OUT / 'development')
    inputs = prepare()
    models = {r['id']: r for r in load(OUT / 'catalog.json')['response']['data']}
    caps = load(OUT / 'capability-review.json')
    av = {r['requested_id']: r for r in load(OUT / 'availability.json')}
    jev = load(OUT / 'jev-exact-id-endpoint-check.json')['endpoint_response']['data']
    models[JEV] = {'context_length': jev['endpoints'][0]['context_length'], 'pricing': jev['endpoints'][0]['pricing']}
    schema = {'type': 'object', 'required': ['event_category', 'event_direction'], 'additionalProperties': False,
        'properties': {'event_category': {'type': 'string', 'enum': list(CATEGORIES)}, 'event_direction': {'type': 'string', 'enum': list(DIRECTIONS)}}}
    key = api_key()
    if not key:
        raise ValueError('OPENROUTER_API_KEY_missing')
    policy = load(OUT / 'sharing-review.json')['documents']
    if any(not policy[p['content_sha256']]['external_allowed'] for p in inputs['passages']):
        raise ValueError('Classification source sharing restriction')
    with (OUT / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = load(OUT / 'spend-ledger.json')
        for model in (JEV, 'anthropic/claude-haiku-5.5', 'xiaomi/mimo-v2.6-flash', 'deepseek/deepseek-v4.1-flash'):
            endpoint = jev['endpoints'][0] if model == JEV else next(e for e in av[model]['endpoints']['data']['endpoints'] if e['tag'] == caps[model]['endpoint_tag'])
            bound, prompt_price, completion_price = budget_charge_bound(models[model], endpoint)
            for p in inputs['passages']:
                output = OUT / 'classification-results' / (model.replace('/', '__') + '-' + p['passage_id'] + '.json')
                rid = str(output.relative_to(OUT))
                if output.exists() or any(r['request_id'] == rid for r in ledger['requests']):
                    continue
                if not can_reserve(ledger, bound):
                    return {'status': 'budget_stop'}
                provider = {'only': [endpoint['tag']], 'allow_fallbacks': False, 'data_collection': 'deny', 'zdr': True,
                    'max_price': {'prompt': float(prompt_price * 1000000), 'completion': float(completion_price * 1000000)}}
                if model == JEV:
                    endpoint_path = '/systemone'
                    payload = {'model': JEV, 'state': p['state'], 'questions': inputs['questions'], 'provider': provider}
                else:
                    endpoint_path = '/chat/completions'
                    payload = {'model': model, 'max_tokens': 256, 'stream': False, 'provider': {**provider, 'require_parameters': True},
                        'response_format': {'type': 'json_schema', 'json_schema': {'name': 'passage_classification', 'strict': True, 'schema': schema}},
                        'messages': [{'role': 'system', 'content': 'Classify the supplied source passage only. Return event_category and event_direction JSON using these identical typed-question definitions: ' + json.dumps(inputs['questions'])},
                            {'role': 'user', 'content': json.dumps(p['state'], ensure_ascii=False)}]}
                    if 'reasoning' in endpoint['supported_parameters']:
                        payload['reasoning'] = reasoning_settings(models[model])
                    if 'temperature' in endpoint['supported_parameters']:
                        payload['temperature'] = 0
                entry = {'request_id': rid, 'at': now(), 'reserved_usd': str(bound), 'actual_usd': None, 'status': 'in_flight'}
                ledger['requests'].append(entry); dump(OUT / 'spend-ledger.json', ledger)
                started = time.perf_counter()
                result = {'requested_model': model, 'passage_id': p['passage_id'], 'purpose': 'secondary_classification_only',
                    'input_file_sha256': sha(OUT / 'classification-inputs-public.json'), 'request': payload, 'deterministic': p['deterministic']}
                try:
                    response = request(endpoint_path, payload=payload, key=key)
                    result['response'] = response
                    cost = (response.get('usage') or {}).get('cost')
                    entry.update(actual_usd=str(cost) if cost is not None else None, status='completed' if cost is not None else 'cost_unresolved')
                    if model == JEV:
                        result['classification'] = {k: response['answers'][k]['choice'] for k in ('event_category', 'event_direction')}
                    else:
                        result['classification'] = json.loads(response['choices'][0]['message']['content'])
                    classification = result['classification']
                    result['classification_schema_valid'] = set(classification) == {'event_category', 'event_direction'} and classification['event_category'] in CATEGORIES and classification['event_direction'] in DIRECTIONS
                except Exception as exc:
                    result.update(failure_type=type(exc).__name__, http_status=getattr(exc, 'code', None))
                    entry['status'] = 'failed_billing_uncertain'
                result['seconds'] = time.perf_counter() - started
                dump(output, result); dump(OUT / 'spend-ledger.json', ledger)
                print(json.dumps({'model': model, 'passage': p['passage_id'], 'classification': result.get('classification'), 'status': entry['status'], 'actual_usd': entry['actual_usd']}), flush=True)
                if entry['status'] != 'completed':
                    break
                if Decimal(entry['actual_usd']) > bound:
                    return {'status': 'charge_bound_exceeded_stop'}
    return {'status': 'classification_complete_feasible_cases', 'source_passages': len(inputs['passages'])}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
