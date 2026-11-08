"""Bounded experiment CLI. Writes only to artifacts/phase5/general-ai-experiment.

python -m scrapper.research.general_ai_experiment.runner prepare
python -m scrapper.research.general_ai_experiment.runner catalog
python -m scrapper.research.general_ai_experiment.runner cloud --stage smoke
No imports of database clients, scheduler or feature-admission services.
"""
from __future__ import annotations
import argparse
import base64
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import resource
import signal
import ssl
import time
import urllib.error
import urllib.request

from .protocol import MODELS, JEV, LOCAL, SCHEMA, task_text, validate_output, classification_questions
from .verification import verify

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/phase5/general-ai-experiment'
BASE = 'https://openrouter.ai/api/v1'
MAX_OUTPUT = 8192
TRANSPORT_VERSION = 'structured-v2'
CEILING = Decimal('10')
SMOKE_PAGES = {'credit_rating': 2, 'presentation': 7, 'transcript': 4}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def api_key():
    # Read only this variable; never source shell code or print dotenv contents.
    if os.environ.get('OPENROUTER_API_KEY'):
        return os.environ['OPENROUTER_API_KEY']
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            match = re.fullmatch(r'\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.*?)\s*', line)
            if match:
                raw = match[1]
                if raw.startswith(('"', "'")) and raw.endswith(raw[0]):
                    return raw[1:-1]
                return raw.split(' #', 1)[0].strip()
    return ''


def request(endpoint, *, payload=None, key='', return_raw=False):
    headers = {'Content-Type': 'application/json', 'X-OpenRouter-Title': 'Phase5 bounded research'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(BASE + endpoint, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    # macOS framework Python may lack a configured root bundle. Keep TLS
    # verification enabled using the repository environment's certifi bundle.
    try:
        import certifi
        tls = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        tls = ssl.create_default_context()
    # Socket timeouts reset when a provider sends keepalive bytes. Also bound
    # total wall time so a nominally non-streaming request cannot hang forever.
    def deadline(*_):
        raise TimeoutError('OpenRouter total request deadline exceeded')
    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(120)
    try:
        with urllib.request.urlopen(req, timeout=55, context=tls) as response:
            raw = response.read()
            return raw if return_raw else json.loads(raw)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def context_for(row):
    from scrapper.research.document_intelligence.schema import DocumentContext
    keys = {f.name for f in fields(DocumentContext)}
    return DocumentContext(**{k: v for k, v in row.items() if k in keys})


def freeze(directory):
    dump(directory / 'freeze.json', {'frozen_at': now(), 'files': {
        str(p.relative_to(directory)): sha(p) for p in sorted(directory.rglob('*'))
        if p.is_file() and p.name != 'freeze.json'}})


def check_freeze(directory):
    for name, digest in load(directory / 'freeze.json')['files'].items():
        if sha(directory / name) != digest:
            raise ValueError('Frozen benchmark file changed: ' + name)
    for row in load(directory / 'manifest.json')['documents']:
        if sha(ROOT / row['path']) != row['content_sha256']:
            raise ValueError('Original document changed')


def prepare(dataset='development'):
    from scrapper.research.document_intelligence.offline import extract_local, deny_network
    from scrapper.research.document_intelligence.extract import extract_intelligence
    from scrapper.research.document_intelligence.evidence import validate_evidence, canonical_hash
    from scrapper.research.document_intelligence.source_review import source_units
    import pdfplumber
    prepared = OUT / dataset
    if (prepared / 'freeze.json').exists():
        check_freeze(prepared)
        return {'status': 'existing_freeze_verified', 'path': str(prepared)}
    prepared.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    rights = load(OUT / 'sharing-review.json') if (OUT / 'sharing-review.json').exists() else {'policy': 'Public accessibility does not establish external processing rights.', 'documents': {}}
    manifest = load(ROOT / 'sample_files/manifest.json') if dataset == 'development' else [
        {'file': d['path'], 'sha256': d['content_sha256'], 'source_url': None, 'document_type': d['document_type']}
        for d in load(OUT / ('holdout-final-source-selection.json' if dataset == 'holdout-final' else 'holdout-candidate-selection-originals.json'))['selected'].values()]
    with deny_network():
        for item in manifest:
            original = (ROOT / 'sample_files' / item['file']) if dataset == 'development' else (ROOT / item['file'])
            if sha(original) != item['sha256']:
                raise ValueError('Sample original hash mismatch')
            before = time.perf_counter()
            body = original.read_bytes()
            extraction = extract_local(body, '.pdf', ocr=False)
            text = '\n'.join(p['selected_text'] for p in extraction['pages'])
            # Document families are classified by their content, not basenames.
            head = text[:16000]
            if re.search(r'(?:earnings|conference|concall|investor).*?(?:call|transcript)|transcript', head, re.I) and re.search(r'(?:Moderator|MR\.|Mr\.|Management:)', head):
                kind = 'transcript'
            elif re.search(r'(?:rating rationale|rating action|rated facilities|credit rating|ratings assigned)', head, re.I):
                kind = 'credit_rating'
            elif re.search(r'(?:investor|corporate|results)\s+presentation', head, re.I):
                kind = 'presentation'
            else:
                raise ValueError('Document type needs content review: ' + item['file'])
            expected = {'concall_transcripts': 'transcript', 'presentations': 'presentation', 'credit_ratings': 'credit_rating'}[Path(item['file']).parts[0]] if dataset == 'development' else item['document_type']
            if kind != expected:
                raise ValueError('Content differs from sample manifest family')
            digest = item['sha256']
            row = {'document_id': 'sha256:' + digest, 'content_sha256': digest,
                'document_type': kind, 'path': str(original.relative_to(ROOT)),
                'source_url': item['source_url'], 'pages': len(extraction['pages']),
                'publication_at': None, 'captured_at': None,
                'provenance': {'source': 'local_' + dataset + '_originals', 'historical_publication_evidence': 'unestablished'},
                'canonical_extraction_sha256': canonical_hash(extraction)}
            context = context_for(row)
            records, diagnostics = extract_intelligence(context, extraction)
            focused, focused_diagnostics = extract_intelligence(context, extraction, focus_disclosures=True)
            if not all(validate_evidence(r, extraction) for r in records + focused):
                raise ValueError('Baseline evidence mismatch')
            units = list(source_units(context, extraction, original))
            directory = prepared / digest
            dump(directory / 'canonical.json', extraction)
            dump(directory / 'source-units.json', units)
            dump(directory / 'baseline.json', {'records': [r.to_dict() for r in records], 'diagnostics': diagnostics,
                'focused_records': [r.to_dict() for r in focused], 'focused_diagnostics': focused_diagnostics})
            # Full native text on ALL pages. Render each page once, full frame, no cropping.
            # One selected page image per packet enables the smallest local vision candidate.
            packets, render_log = [], []
            full_text = [{'document_id': context.document_id, 'page': p['page_or_slide_number'],
                          'native_text': p['native_text']} for p in extraction['pages']]
            with pdfplumber.open(io.BytesIO(body)) as pdf:
                for index, page in enumerate(extraction['pages']):
                    image = pdf.pages[index].to_image(resolution=144).original.convert('RGB')
                    initial = list(image.size)
                    image.thumbnail((1280, 1280))
                    filename = f'page-{index+1}.png'
                    image.save(directory / filename)
                    selected = {'document_id': context.document_id, 'page': index + 1,
                        'native_text': page['native_text'], 'elements': page['elements'], 'layout': page['layout']}
                    packets.append({'document_id': context.document_id, 'selected_page': index + 1,
                        'original_pages_native_text': full_text, 'selected_page_structure': selected,
                        'image_file': filename, 'image_sha256': sha(directory / filename),
                        'preprocessing': {'dpi': 144, 'original_render_pixels': initial,
                            'sent_pixels': list(image.size), 'crop': None, 'images_per_request': 1,
                            'omitted_images_this_request': [i + 1 for i in range(len(pdf.pages)) if i != index],
                            'native_text_omitted': False}})
                    render_log.append({'page': index + 1, 'pixels': list(image.size)})
            dump(directory / 'packets.json', packets)
            row['preprocessing_seconds'] = time.perf_counter() - before
            row['baseline_records'] = len(records)
            row['focused_baseline_records'] = len(focused)
            row['renders'] = render_log
            notices = []
            for p in extraction['pages']:
                for match in re.finditer(r'.{0,100}(?:copyright|reproduc|permission|proprietary|all rights reserved|distribut).{0,250}', p['native_text'], re.I):
                    notices.append({'page': p['page_or_slide_number'], 'quote': match.group()})
            rights['documents'][digest] = {'external_allowed': False, 'review_status': 'pending',
                'basis': None, 'reviewer': None, 'source_notices': notices,
                'original_source': item['source_url']}
            rows.append(row)
            print(json.dumps({'prepared': item['file'], 'type': kind, 'pages': row['pages']}), flush=True)
    dump(prepared / 'manifest.json', {'role': 'development_not_unseen' if dataset == 'development' else 'holdout_pending_reference_freeze', 'documents': rows})
    (prepared / 'prompt.txt').write_text(task_text())
    dump(prepared / 'preprocessing.json', {'seconds': time.perf_counter() - started,
        'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'platform': platform.platform(), 'machine': platform.machine(),
        'openrouter_cost_usd': 0, 'local_energy_cost': None, 'no_database_access': True})
    dump(OUT / 'sharing-review.json', rights)
    freeze(prepared)
    return {'status': 'prepared', 'documents': len(rows), 'path': str(prepared)}


def catalog():
    raw = request('/models')
    stamp = now()
    dump(OUT / 'catalog.json', {'checked_at': stamp, 'response': raw})
    index = {m['id']: m for m in raw['data']}
    rows = []
    for identifier in (*MODELS, JEV):
        model = index.get(identifier)
        endpoints = None
        error = None
        if model:
            try:
                endpoints = request('/models/' + identifier + '/endpoints')
            except Exception as exc:
                error = type(exc).__name__  # No request headers or secrets in errors.
        rows.append({'requested_id': identifier, 'catalog_available': model is not None,
            'canonical_slug': model.get('canonical_slug') if model else None,
            'input_modalities': model.get('architecture', {}).get('input_modalities') if model else None,
            'pricing': model.get('pricing') if model else None,
            'context_length': model.get('context_length') if model else None,
            'native_file_advertised': 'file' in model.get('architecture', {}).get('input_modalities', []) if model else False,
            'endpoints': endpoints, 'endpoints_error': error,
            'execution_status': 'not_run', 'checked_at': stamp})
    rows.append({'requested_id': LOCAL, 'execution_status': 'not_run',
        'catalog_available': None, 'local_path': os.getenv('PHASE5_LOCAL_CHECKPOINT'),
        'note': 'No automatic checkpoint substitution or download; local-compatible path must be verified.'})
    dump(OUT / 'availability.json', rows)
    return [{'model': r['requested_id'], 'available': r['catalog_available']} for r in rows]


def budget_charge_bound(model, endpoint):
    """Reserve entire context at highest token prices, not a token heuristic.

    Unknown non-token charges or multimodal accounting fail closed. The shared
    ledger, exclusive lock and endpoint price filters enforce the experiment cap;
    no account-wide or API-key limit is required or changed.
    """
    prices = endpoint.get('pricing') or {}
    if not prices or 'prompt' not in prices or 'completion' not in prices:
        raise ValueError('provider_prices_unestablished')
    permitted = {'prompt', 'completion', 'input_cache_read', 'input_cache_write',
                 'input_cache_write_1h', 'internal_reasoning', 'image', 'audio',
                 'input_audio_cache', 'web_search', 'overrides', 'discount'}
    if any(k not in permitted and Decimal(str(v or 0)) != 0 for k, v in prices.items()):
        raise ValueError('unbounded_additional_provider_fee')
    # Image pricing semantics cannot be inferred just from the catalog field.
    # Require a reviewed endpoint-specific bound on image/PDF token billing.
    candidates = [prices, model.get('pricing', {})]
    candidates += [tier for p in list(candidates) for tier in p.get('overrides', [])]
    prompt = max(Decimal(str(p.get(k, 0))) for p in candidates
                 for k in ('prompt', 'image', 'input_cache_write', 'input_cache_write_1h'))
    completion = max(Decimal(str(p.get(k, 0))) for p in candidates for k in ('completion', 'internal_reasoning'))
    context = endpoint.get('context_length') or model.get('context_length')
    if not isinstance(context, int) or context <= 0:
        raise ValueError('provider_context_bound_missing')
    return Decimal(context) * prompt + Decimal(MAX_OUTPUT) * completion, prompt, completion


def budget_used(ledger):
    total = Decimal('0')
    for row in ledger['requests']:
        amount = Decimal(row['actual_usd'] if row['actual_usd'] is not None else row['reserved_usd'])
        if not amount.is_finite() or amount < 0:
            raise ValueError('Invalid cost in experiment ledger')
        total += amount
    return total


def can_reserve(ledger, maximum_charge):
    return maximum_charge.is_finite() and maximum_charge >= 0 and budget_used(ledger) + maximum_charge <= CEILING


def reasoning_settings(model):
    info = model.get('reasoning') or {}
    efforts = info.get('supported_efforts') or []
    if 'none' in efforts:
        return {'effort': 'none'}
    if 'minimal' in efforts:
        return {'effort': 'minimal'}
    if info.get('mandatory'):
        return {'effort': 'low'} if 'low' in efforts else None
    return {'enabled': False}


def transport_schema():
    # Equivalent JSON Schema with explicit enum types for providers that require
    # them. This changes no allowed values and adds no financial instructions.
    schema = json.loads(json.dumps(SCHEMA))
    for value in schema['properties']['events']['items']['properties'].values():
        if 'enum' in value and all(isinstance(x, str) for x in value['enum']):
            value['type'] = 'string'
    return schema


def cloud(stage, strategy, limit, model_filter=None, retry_failed_transport=False):
    holdout_freeze = load(OUT / 'holdout-evaluation-freeze.json') if stage == 'holdout' else None
    dataset = holdout_freeze.get('dataset', 'holdout') if holdout_freeze else 'development'
    if dataset not in {'development', 'holdout', 'holdout-final'}:
        raise ValueError('Unsupported frozen dataset')
    prepared = OUT / dataset
    check_freeze(prepared)
    if stage not in {'smoke', 'main', 'holdout', 'repeat1', 'repeat2', 'repeat3'}:
        raise ValueError('Holdout/repeat require explicit frozen inputs; development cannot become unseen')
    repeat_config = load(OUT / 'repeat-plan.json') if stage.startswith('repeat') else None
    if repeat_config and strategy != 'pages':
        raise ValueError('Frozen repeat plan covers source-page inputs only')
    if stage == 'holdout':
        for name, digest in load(OUT / 'holdout-evaluation-freeze.json')['files'].items():
            if sha(OUT / name) != digest:
                raise ValueError('Frozen holdout reference/configuration changed')
        final_config = load(OUT / 'holdout-final-config.json')
        if final_config['schema'] != transport_schema() or final_config['max_output_tokens'] != MAX_OUTPUT or final_config['financial_task'] != (prepared / 'prompt.txt').read_text():
            raise ValueError('Holdout task/transport differs from frozen final configuration')
    key = api_key()
    if not key:
        dump(OUT / 'cloud-blocker.json', {'at': now(), 'reason': 'OPENROUTER_API_KEY_missing', 'requests_sent': 0})
        return {'status': 'blocked', 'reason': 'OPENROUTER_API_KEY_missing'}
    catalog()  # Live capability/price refresh before executing.
    account = request('/key', key=key)['data']
    dump(OUT / 'key-cap-check.json', {k: account.get(k) for k in ('limit', 'limit_remaining', 'limit_reset', 'usage')})
    policy = load(OUT / 'sharing-review.json')
    capabilities = load(OUT / 'capability-review.json') if (OUT / 'capability-review.json').exists() else {}
    models = {m['id']: m for m in load(OUT / 'catalog.json')['response']['data']}
    availability = {r['requested_id']: r for r in load(OUT / 'availability.json')}
    manifest = load(prepared / 'manifest.json')['documents']
    allowed_rows = [r for r in manifest if policy['documents'].get(r['content_sha256'], {}).get('external_allowed') is True]
    if stage == 'smoke':
        # Three distinct source families, selected before any model output.
        manifest = [next(r for r in allowed_rows if r['document_type'] == kind) for kind in ('credit_rating', 'presentation', 'transcript')]
    ordered = sorted(MODELS, key=lambda m: (Decimal(models.get(m, {}).get('pricing', {}).get('prompt', '999')), m))
    if model_filter:
        if model_filter not in MODELS:
            raise ValueError('Exact requested candidate required')
        ordered = [model_filter]
    results_dir = OUT / 'runs' / (stage + '-' + strategy + '-' + TRANSPORT_VERSION)
    results_dir.mkdir(parents=True, exist_ok=True)
    sent = 0
    # Global experiment lock also used by local inference. Persistent reservations
    # survive crashes; they are never automatically released after a timeout.
    with (OUT / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger_path = OUT / 'spend-ledger.json'
        ledger = load(ledger_path) if ledger_path.exists() else {'ceiling_usd': '10', 'requests': []}
        for identifier in ordered:
            if repeat_config and identifier not in repeat_config['models']:
                continue
            if stage == 'holdout' and identifier not in final_config['models' if strategy == 'pages' else 'native_models']:
                continue
            model = models.get(identifier)
            if model is None:
                dump(results_dir / (identifier.replace('/', '__') + '-status.json'), {'status': 'unavailable_exact_id'})
                continue
            if stage == 'main':
                smoke_dir = OUT / 'runs' / ('smoke-' + strategy + '-' + TRANSPORT_VERSION)
                smoke = [load(p) for p in smoke_dir.glob('*.json')] if smoke_dir.exists() else []
                passed = {r.get('document_id') for r in smoke
                          if r.get('requested_model') == identifier and r.get('schema_valid') is True
                          and (r.get('response') or {}).get('choices', [{}])[0].get('finish_reason') == 'stop'}
                required = {next(r['document_id'] for r in allowed_rows if r['document_type'] == kind)
                            for kind in ('credit_rating', 'presentation', 'transcript')}
                if not required <= passed:
                    dump(results_dir / (identifier.replace('/', '__') + '-status.json'), {'status': 'blocked', 'reason': 'three_family_smoke_not_passed'})
                    continue
            endpoints = (availability[identifier].get('endpoints') or {}).get('data', {}).get('endpoints', [])
            review = capabilities.get(identifier, {})
            endpoint = next((e for e in endpoints if e.get('tag') == review.get('endpoint_tag')), None)
            if endpoint is None or not review.get('reviewer') or not review.get('billing_bound_basis'):
                dump(results_dir / (identifier.replace('/', '__') + '-status.json'), {'status': 'blocked', 'reason': 'endpoint_capability_and_billing_review_required'})
                continue
            if strategy == 'native' and not ('file' in model['architecture']['input_modalities'] and review.get('native_pdf_verified')):
                dump(results_dir / (identifier.replace('/', '__') + '-status.json'), {'status': 'unsupported_native_pdf'})
                continue
            if strategy == 'pages' and ('image' not in model['architecture']['input_modalities'] or review.get('max_images', 0) < 1):
                continue
            bound, prompt_price, completion_price = budget_charge_bound(model, endpoint)
            model_failed = False
            for row in manifest:
                digest = row['content_sha256']
                sharing = policy['documents'].get(digest, {})
                if sharing.get('external_allowed') is not True or not sharing.get('basis') or not sharing.get('reviewer'):
                    dump(results_dir / (identifier.replace('/', '__') + '-' + digest[:12] + '-status.json'), {'status': 'restricted_external_sharing_unestablished'})
                    continue
                directory = prepared / digest
                extraction = load(directory / 'canonical.json')
                units = load(directory / 'source-units.json')
                packets = load(directory / 'packets.json')
                if strategy == 'native':
                    packets = [None]
                elif stage == 'smoke':
                    packets = [p for p in packets if p['selected_page'] == SMOKE_PAGES[row['document_type']]]
                else:
                    plan = repeat_config if repeat_config else load(OUT / ('holdout-page-plan.json' if stage == 'holdout' else 'main-page-plan.json'))
                    if plan['source_manifest_sha256'] != sha(prepared / 'manifest.json'):
                        raise ValueError('Main page plan source identity mismatch')
                    pages = plan['document_pages'].get(row['document_id'], [])
                    if repeat_config and identifier == 'deepseek/deepseek-v4.1-flash' and row['document_type'] != 'credit_rating':
                        pages = []
                    packets = [p for p in packets if p['selected_page'] in pages]
                for packet in packets:
                    page_number = packet['selected_page'] if packet else 'full'
                    rid = identifier.replace('/', '__') + '-' + digest[:12] + '-' + str(page_number)
                    result_path = results_dir / (rid + '.json')
                    if retry_failed_transport and result_path.exists():
                        earlier = load(result_path)
                        if earlier.get('failure_type') == 'HTTPError' and earlier.get('http_status') in {400, 404}:
                            rid += '-transport-attempt1'
                            result_path = results_dir / (rid + '.json')
                    if result_path.exists() or any(r['request_id'] == str(result_path.relative_to(OUT)) for r in ledger['requests']):
                        continue  # Never retry an uncertain billed request implicitly.
                    if sent >= limit:
                        return {'status': 'request_limit_reached', 'sent': sent}
                    if not can_reserve(ledger, bound):
                        return {'status': 'budget_stop', 'sent': sent, 'next_max_charge_usd': str(bound)}
                    if bound > 0:
                        key_info = request('/key', key=key)['data']
                        current_remaining = key_info.get('limit_remaining')
                        if current_remaining is not None and Decimal(str(current_remaining)) < bound:
                            return {'status': 'key_budget_stop', 'sent': sent}
                    content = [{'type': 'text', 'text': 'Document identity: ' + row['document_id']}]
                    if packet:
                        content.append({'type': 'text', 'text': 'For this page-window request, propose events supported on selected_page only. Other original pages supply context, not additional target events.'})
                        content.append({'type': 'text', 'text': json.dumps({k: v for k, v in packet.items() if k not in {'image_file', 'image_sha256'}}, ensure_ascii=False)})
                        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode((directory / packet['image_file']).read_bytes()).decode()}})
                    else:
                        content.append({'type': 'file', 'file': {'filename': digest + '.pdf',
                            'file_data': 'data:application/pdf;base64,' + base64.b64encode((ROOT / row['path']).read_bytes()).decode()}})
                    payload = {'model': identifier, 'max_tokens': MAX_OUTPUT, 'stream': False,
                        'provider': {'only': [review['endpoint_tag']], 'allow_fallbacks': False,
                            'data_collection': 'deny', 'zdr': review.get('require_zdr', True), 'require_parameters': True,
                            'max_price': {'prompt': float(prompt_price * 1000000), 'completion': float(completion_price * 1000000)}},
                        'messages': [{'role': 'system', 'content': (prepared / 'prompt.txt').read_text()}, {'role': 'user', 'content': content}]}
                    endpoint_parameters = endpoint.get('supported_parameters') or []
                    if 'structured_outputs' in endpoint_parameters:
                        payload['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'financial_events', 'strict': True, 'schema': transport_schema()}}
                    elif 'response_format' in endpoint_parameters:
                        payload['response_format'] = {'type': 'json_object'}
                    if 'temperature' in endpoint_parameters:
                        payload['temperature'] = 0
                    if 'reasoning' in endpoint_parameters:
                        setting = reasoning_settings(model)
                        if setting is not None:
                            payload['reasoning'] = setting
                    if strategy == 'native':
                        payload['plugins'] = [{'id': 'file-parser', 'pdf': {'engine': 'native'}}]
                    entry = {'request_id': str(result_path.relative_to(OUT)), 'at': now(), 'reserved_usd': str(bound), 'actual_usd': None, 'status': 'in_flight'}
                    ledger['requests'].append(entry)
                    dump(ledger_path, ledger)
                    print(json.dumps({'request': rid, 'status': 'started', 'maximum_charge_usd': str(bound)}), flush=True)
                    before = time.perf_counter()
                    result = {'requested_model': identifier, 'strategy': strategy, 'document_id': row['document_id'],
                        'transport_version': TRANSPORT_VERSION, 'max_output_tokens': MAX_OUTPUT,
                        'explicit_transport_retry': retry_failed_transport and rid.endswith('-transport-attempt1'),
                        'dataset': dataset,
                        'source_text_encoding': 'literal_unicode_json',
                        'response_format_requested': payload.get('response_format'), 'reasoning_requested': payload.get('reasoning'),
                        'main_page_plan_sha256': sha(OUT / ('holdout-page-plan.json' if stage == 'holdout' else 'main-page-plan.json')) if stage in {'main','holdout'} and packet else None,
                        'runner_sha256': sha(Path(__file__)),
                        'page': page_number, 'input_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                        'prompt_sha256': sha(prepared / 'prompt.txt'), 'preprocessing': packet['preprocessing'] if packet else 'none_original_pdf',
                        'pdf_engine_requested': 'native' if not packet else None, 'capability_review': review}
                    try:
                        response = request('/chat/completions', payload=payload, key=key)
                        result['response'] = response
                        result['seconds'] = time.perf_counter() - before
                        cost = (response.get('usage') or {}).get('cost')
                        entry['actual_usd'] = str(cost) if cost is not None else None
                        entry['status'] = 'completed' if cost is not None else 'cost_unresolved'
                        raw = response['choices'][0]['message']['content']
                        try:
                            parsed = json.loads(raw)
                            result['schema_valid'] = validate_output(parsed)
                        except (TypeError, ValueError):
                            parsed = None
                            result['schema_valid'] = False
                        result['proposals'] = [verify(e, context_for(row), extraction, units) for e in parsed['events']] if result['schema_valid'] else []
                        result['abstained'] = result['schema_valid'] and not parsed['events']
                        result['semantic_accuracy'] = None
                        result['pdf_engine_actual'] = 'unestablished_requires_response_review' if not packet else None
                    except Exception as exc:
                        # Do not emit exception messages containing headers or payloads.
                        result.update(status='failed', failure_type=type(exc).__name__, http_status=getattr(exc, 'code', None), seconds=time.perf_counter() - before)
                        if isinstance(exc, urllib.error.HTTPError):
                            try:
                                error = json.loads(exc.read()).get('error', {})
                                result['error'] = {k: error.get(k) for k in ('code', 'message')}
                                metadata = error.get('metadata') or {}
                                safe = json.dumps({k: metadata.get(k) for k in ('provider_name', 'raw')})
                                result['provider_error_details'] = json.loads(safe.replace(key, '[REDACTED]'))
                            except (ValueError, OSError):
                                pass
                        entry['status'] = 'billing_uncertain_no_retry'
                    dump(result_path, result)
                    dump(ledger_path, ledger)
                    print(json.dumps({'request': rid, 'status': entry['status'], 'schema_valid': result.get('schema_valid'),
                        'proposals': len(result.get('proposals', [])), 'actual_usd': entry['actual_usd']}), flush=True)
                    sent += 1
                    if entry['status'] != 'completed':
                        # This model stops; its full reservation remains. Other
                        # candidates can run only if their own worst-case charge
                        # still fits, so uncertain billing cannot exceed budget.
                        model_failed = True
                        break
                    if Decimal(entry['actual_usd']) > bound:
                        return {'status': 'stop_charge_exceeded_reviewed_bound', 'sent': sent}
                if model_failed:
                    break
    return {'status': 'finished_feasible_requests', 'sent': sent}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'catalog', 'cloud'])
    parser.add_argument('--stage', choices=['smoke', 'main', 'holdout', 'repeat1', 'repeat2', 'repeat3'], default='smoke')
    parser.add_argument('--dataset', choices=['development', 'holdout', 'holdout-final'], default='development')
    parser.add_argument('--strategy', choices=['native', 'pages'], default='pages')
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--model', choices=MODELS)
    parser.add_argument('--retry-failed-transport', action='store_true', help='One explicit compatibility retry for preserved HTTP 400/404 failures; never retries rate limits or uncertain timeouts')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('Request limit must be 1..100')
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        result = prepare(args.dataset) if args.command == 'prepare' else catalog() if args.command == 'catalog' else cloud(args.stage, args.strategy, args.limit, args.model, args.retry_failed_transport)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'failure_type': type(exc).__name__, 'reason': str(exc) if isinstance(exc, ValueError) else 'See local prerequisites; secrets and transport details suppressed'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
