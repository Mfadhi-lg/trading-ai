"""Read-only offline preflight verification; never contacts a provider."""
from .prepare_vimta_regression import OUTPUT, read
from .provider import digest, stable
from .runner import make_packet
from .build_geometry_review import BASE
from scrapper.research.document_intelligence.offline import deny_network
from pathlib import Path
import json


def verify():
    plan = read(OUTPUT / 'request-plan.json')
    payload = read(OUTPUT / 'request.json')
    full = read(OUTPUT / 'packet.json')
    packet = full['qwen_packet']
    messages = payload['messages']
    assert digest(stable(payload).encode()) == plan['request_sha256']
    assert digest(stable(dict(packet=packet, payload=payload,
                             config=plan['configuration'])).encode()) == plan['request_id']
    assert digest(stable(dict(packet=packet, messages=messages,
                             config=plan['configuration'])).encode()) == plan['legacy_client_cache_key']
    assert digest(stable(full['geometry']).encode()) == plan['geometry_sha256']
    assert digest(stable(packet['representation']).encode()) == plan['representation_sha256']
    assert digest(messages[0]['content'].encode()) == plan['prompt_sha256']
    assert digest(stable(messages).encode()) == plan['messages_sha256']
    assert messages == read(OUTPUT / 'qwen-messages.json')
    assert json.loads(messages[1]['content']) == read(OUTPUT / 'qwen-context.json') == packet['representation']
    manifest = read(BASE / 'pilot_manifest.json')
    doc = next(d for c in manifest['companies'] if c['company_id'] == 'VIMTALABS'
               for d in c['documents'] if d['document_type'] == 'presentation')
    canonical = read(BASE / 'replayed-final/VIMTALABS/presentation.canonical.json')
    rebuilt, rebuilt_messages = make_packet(doc, canonical, 'qualitative', 25)
    assert rebuilt == packet and rebuilt_messages == messages
    before = read(OUTPUT / 'preservation-before.json')
    allowed = read(OUTPUT / 'validation.json')['intentionally_changed_files']
    changed = [p for p, h in before.items() if not Path(p).is_file() or digest(Path(p).read_bytes()) != h]
    assert set(changed) == set(allowed)
    ledger = read(BASE / 'inference/inference_usage.json')
    assert len(ledger['requests']) == 6
    assert sum(a['charged_or_reserved_usd'] for a in ledger['requests']) == plan['historical_charged_or_reserved_usd']
    assert plan['new_budget_approved'] is False and plan['requests_sent'] == 0
    assert plan['new_request_limit'] == 1 and plan['automatic_retries'] == 0
    assert payload['provider']['allow_fallbacks'] is False
    assert payload['max_tokens'] == 4096 and packet['pages'] == [25]
    assert all(isinstance(m['content'], str) for m in messages)
    assert len(stable(messages).encode()) + 1024 == plan['input_tokens_upper_bound'] <= 16000
    verification = read(BASE / 'provider_verification.json')
    assert digest((BASE / 'provider_verification.json').read_bytes()) == plan['provider_verification_sha256']
    for e in verification['catalog']['endpoints']:
        if e['status'] != 0:
            continue
        assert plan['input_tokens_upper_bound'] + 4096 <= e['context_length']
        assert 4096 <= e['max_completion_tokens']
    page = full['geometry']['pages'][0]
    assert len(page['regions']) == 5 and len(page['spans']) == 109
    blocks = {b['id']: b for b in page['blocks']}
    spans = {s['id']: s for s in page['spans']}
    used = []
    for r in page['regions']:
        assert r['status'] == 'proposed'
        assert all(spans[s]['region'] == r['id'] and spans[s]['regions'] == [r['id']] for s in r['spans'])
        used.extend(r['spans'])
        assert r['spans'] == [s for f in r['fragments'] for s in f['spans']]
        for f in r['fragments']:
            a, b = f['offsets']
            assert f['text'] == blocks[f['block']]['text'][a:b]
    used.extend(s['id'] for s in page['spans'] if s['region'] is None)
    assert sorted(used) == sorted(spans) and len(set(used)) == len(used)
    comparison = read(OUTPUT / 'comparison.json')
    assert comparison['geometry_demonstrably_helped'] is None
    for row in comparison['disclosures']:
        assert row['after']['status'] == 'Unreviewed'
        assert row['human_review']['status'] == 'Unreviewed'
        assert all(v is None for k, v in row['human_review'].items() if k != 'status')
    return dict(status='PASS', protected_files_unchanged=len(before)-len(changed),
                requests_sent=0, historical_attempts=6, request_id=plan['request_id'])


if __name__ == '__main__':
    with deny_network():
        print(json.dumps(verify(), indent=2))
