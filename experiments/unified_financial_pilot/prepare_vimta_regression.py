"""Offline-only V0.2 preparation. Deliberately contains no inference entrypoint."""
import json
from pathlib import Path
import subprocess

from .build_geometry_review import BASE, ROOT, EXPECTATIONS, norm
from .presentation_geometry import presentation_context
from .provider import digest, save, stable
from .runner import make_packet, PRESENTATION_VERSION
from scrapper.research.document_intelligence.offline import deny_network

OUTPUT = BASE / 'vimta-page25-regression-v0.2'
STATUSES = ['Correct', 'Incorrect', 'Incomplete', 'Unsupported', 'Unreviewed']


def read(path):
    return json.loads(path.read_text())


def prepare(output=OUTPUT):
    output = Path(output).resolve()
    if output != OUTPUT.resolve() or (output / 'request.json').exists():
        raise ValueError('Use the reserved, unused V0.2 package; never overwrite a prepared request')
    before = read(output / 'preservation-before.json')
    manifest = read(BASE / 'pilot_manifest.json')
    doc = next(d for c in manifest['companies'] if c['company_id'] == 'VIMTALABS'
               for d in c['documents'] if d['document_type'] == 'presentation')
    canonical = read(BASE / 'replayed-final/VIMTALABS/presentation.canonical.json')
    geometry = presentation_context(doc, canonical, [25])
    packet, messages = make_packet(doc, canonical, 'qualitative', 25)
    p = geometry['pages'][0]
    assert len(p['regions']) == 5 and len(p['spans']) == 109
    blocks = {b['id']: b for b in p['blocks']}
    spans = {s['id']: s for s in p['spans']}
    used, fragments = [], []
    for r in p['regions']:
        assert r['status'] == 'proposed'
        assert all(spans[s]['region'] == r['id'] and spans[s]['regions'] == [r['id']]
                   for s in r['spans'])
        used.extend(r['spans'])
        for f in r['fragments']:
            a, b = f['offsets']
            assert f['text'] == blocks[f['block']]['text'][a:b]
            assert f['spans'] == [s['id'] for s in p['spans'] if s['block'] == f['block']
                                  and a <= s['offsets'][0] and s['offsets'][1] <= b]
            fragments.append(f['id'])
    for s in p['spans']:
        a, b = s['offsets']
        assert s['text'] == blocks[s['block']]['text'][a:b]
    used.extend(s['id'] for s in p['spans'] if s['region'] is None)
    assert sorted(used) == sorted(spans) and len(set(used)) == 109
    assert len(set(fragments)) == len(fragments)
    # Read the JSON example itself, rather than merely searching prompt prose.
    example = json.JSONDecoder().raw_decode(messages[0]['content'].split('Return JSON only: ', 1)[1])[0]
    assert example['evidence'][0]['source_region_id'] == 'pN-rR'
    assert example['evidence'][0]['source_span_ids'] == ['pN-rR-fF']
    verification = read(BASE / 'provider_verification.json')
    assert verification['model'] == 'qwen/qwen3.8-27b'
    assert verification['base_url'] == 'https://openrouter.ai/api/v1'
    endpoints = [e for e in verification['catalog']['endpoints'] if e['status'] == 0]
    upper = len(stable(messages).encode()) + 1024
    output_tokens = 4096
    assert upper <= 16000
    for e in endpoints:
        assert upper + output_tokens <= e['context_length']
        assert output_tokens <= e['max_completion_tokens']
        assert e.get('max_prompt_tokens') is None or upper <= e['max_prompt_tokens']
    pricing = verification['pricing_per_token']
    payload = dict(model=verification['model'], messages=messages, temperature=0, max_tokens=output_tokens,
                   provider=dict(max_price={k: pricing[k] * 1e6 for k in ('prompt', 'completion')},
                                 allow_fallbacks=False), reasoning=dict(effort='none'))
    assert all(isinstance(m['content'], str) for m in messages)
    assert json.loads(messages[1]['content']) == packet['representation']
    config = dict(endpoint=verification['base_url'], model=verification['model'],
                  max_tokens=output_tokens, prompt_version=PRESENTATION_VERSION, temperature=0)
    # Include the complete payload so routing/reasoning changes also change identity.
    request_id = digest(stable(dict(packet=packet, payload=payload, config=config)).encode())
    client_key = digest(stable(dict(packet=packet, messages=messages, config=config)).encode())
    ledger = read(BASE / 'inference/inference_usage.json')
    assert len(ledger['requests']) == 6
    assert all(request_id != a['cache_key'] and client_key != a['cache_key'] for a in ledger['requests'])
    assert not (BASE / 'inference/cache' / (client_key + '.json')).exists()
    old = next(a for a in ledger['requests'] if a['packet']['document_id'] == doc['document_id']
               and a['packet']['pages'] == [25])
    old_request = BASE / 'inference/requests' / (old['cache_key'] + '.json')
    old_response = BASE / 'inference/responses' / (old['cache_key'] + '.json')
    raw = read(old_response)
    old_geometry = read(BASE / 'presentation-geometry-v0.1/packet.json')
    assert geometry == old_geometry['geometry']
    assert packet['representation_sha256'] == old_geometry['qwen_packet']['representation_sha256']
    assert messages != old_geometry['qwen_messages']
    # Render directly from the hash-verified original PDF, physical page 25 only.
    subprocess.run(['pdftoppm', '-f', '25', '-l', '25', '-singlefile', '-scale-to', '1400',
                    '-png', doc['source_path'], str(output / 'original-page-25')],
                   check=True, capture_output=True, timeout=30)
    audit = read(BASE / 'vimta-audit/candidate_audit.json')
    rows = []
    expectations = EXPECTATIONS[:2] + [
        ('C20', 'One of the leading labs in Southern India', 'Electronics & Electricals')
    ] + EXPECTATIONS[2:] + [
        ('C16', '#1 in India', 'Pharma Analytical'),
        ('C17', 'Among the most reputed CROs in India', 'Clinical Research'),
        ('C23', 'Serving 7 of India’s top 10 food companies, large PSUs and Infra companies', 'Food & Environment Testing'),
        ('C25', 'India’s most comprehensive and integrated provider of non-clinical drug discovery and development services', 'Preclinical'),
    ]
    for cid, needle, heading in expectations:
        region = [r for r in p['regions'] if needle in norm(r['text'])]
        assert len(region) == 1 and region[0]['heading'] == heading
        region = region[0]
        previous = next(a for a in audit if a['candidate_id'] == cid)
        rows.append(dict(candidate_id=cid, expected_disclosure=needle, expected_business=heading,
                         expected_region_id=region['id'],
                         available_fragment_ids=[f['id'] for f in region['fragments']],
                         before=dict(statement=previous['reported_fact'], metric=previous['metric'],
                                     quote=previous['model_quote'],
                                     provisional_machine_assessment=previous['provisional_category'],
                                     audit_notes=previous['audit_notes'],
                                     source_region_id=None, source_span_ids=None),
                         after=dict(status='Unreviewed', model_item_ids=[], machine_checks=None),
                         human_review=dict(status='Unreviewed', reviewer=None, reviewed_at=None,
                                           independent=None, business_association=None, period_association=None,
                                           source_region=None, fragment_citations=None, qualifications=None,
                                           unsupported_claims=None, notes=None)))
    changed = [path for path, sha in before.items()
               if not Path(path).is_file() or digest(Path(path).read_bytes()) != sha]
    assert set(changed) == {str(ROOT / 'experiments/unified_financial_pilot' / name)
                            for name in ('runner.py', 'test_presentation_geometry.py')}, changed
    reserve = upper * pricing['prompt'] + output_tokens * pricing['completion']
    plan = dict(status='PREPARED_NOT_SENT_AWAITING_EXPLICIT_ADDITIONAL_BUDGET',
                request_id=request_id, legacy_client_cache_key=client_key, configuration=config,
                request_sha256=digest(stable(payload).encode()),
                prompt_sha256=digest(messages[0]['content'].encode()),
                messages_sha256=digest(stable(messages).encode()),
                representation_sha256=packet['representation_sha256'],
                geometry_sha256=digest(stable(geometry).encode()),
                provider_verification_sha256=digest((BASE / 'provider_verification.json').read_bytes()),
                provider_verified_at=verification['verified_at'],
                provider_limit_basis='Retained verified catalog snapshot; refresh without overwriting before paid execution',
                input_tokens_upper_bound=upper, input_limit=16000, output_limit=output_tokens,
                min_provider_context_tokens=min(e['context_length'] for e in endpoints),
                min_provider_output_tokens=min(e['max_completion_tokens'] for e in endpoints),
                new_request_limit=1, automatic_retries=0, provider_fallbacks=False,
                new_budget_approved=False, images_sent=False, requests_sent=0,
                cost_reservation_usd=reserve, actual_new_api_cost_usd=0, new_api_time_seconds=0,
                historical_attempts_consumed=6,
                historical_reported_cost_usd=sum(a.get('cost_usd') or 0 for a in ledger['requests']),
                historical_charged_or_reserved_usd=sum(a['charged_or_reserved_usd'] for a in ledger['requests']),
                unknown_cost_reservations=[{k: a.get(k) for k in ('cache_key', 'status', 'charged_or_reserved_usd')}
                                           for a in ledger['requests'] if a.get('cost_usd') is None],
                historical_request=str(old_request.relative_to(ROOT)),
                historical_response=str(old_response.relative_to(ROOT)),
                historical_page25_time_seconds=old['latency_seconds'],
                historical_page25_cost_usd=old['cost_usd'], historical_page25_usage=raw.get('usage'),
                controlled_comparison_limitations=[
                    'Historical input was flattened; V0.2 uses geometry and a revised citation example.',
                    'Historical output cap was 2200; V0.2 cap is 4096; V0.1 also removed the four-item cap.',
                    'Provider routing can vary; report actual returned provider/model.',
                    'Any improvement is observed end-to-end evidence, not isolation of geometry as sole cause.'])
    save(output / 'request.json', payload)
    save(output / 'packet.json', dict(geometry=geometry, qwen_packet=packet))
    save(output / 'qwen-messages.json', messages)
    save(output / 'qwen-context.json', packet['representation'])
    save(output / 'request-plan.json', plan)
    save(output / 'comparison.json', dict(disclosures=rows, allowed_statuses=STATUSES,
         correct_business_associations=None, incorrect_business_associations=None,
         missing_important_disclosures=None, qualification_preservation=None,
         source_citation_correctness=None, new_issues_introduced=None,
         geometry_demonstrably_helped=None, new_call_completed=False,
         note='Before assessments are retained machine audit findings; all human decisions remain blank.'))
    save(output / 'validation.json', dict(status='PASS', source_regions=5, source_word_spans=109,
         assigned_word_spans=101, unresolved_word_spans=8, fragments_checked=len(fragments),
         conflicting_assignments=0, source_offsets_and_text='PASS', schema_example='PASS',
         historical_geometry_unchanged=True, request_identity_distinct=True,
         preserved_files=len(before)-len(changed), intentionally_changed_files=changed,
         recorded_attempts=6, new_inference_requests=0, new_human_decisions=0))
    return plan


if __name__ == '__main__':
    with deny_network():
        result = prepare()
    print(json.dumps({k: result[k] for k in ('status', 'request_id', 'input_tokens_upper_bound',
                                          'output_limit', 'cost_reservation_usd')}, indent=2))
