"""Bounded OpenAI-compatible client; persistent reservations survive failures/crashes.

Use one ledger for the entire pilot. No retries. Unknown billing retains the full
reservation. Locking prevents concurrent processes from bypassing spending limits.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import ssl
import time
import urllib.request


def stable(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(x):
    return hashlib.sha256(x).hexdigest()


def decode_content(content):
    """Allow one outer JSON code fence; never repair truncated/invalid JSON."""
    match=re.fullmatch(r'\s*```(?:json)?[ \t]*\n(.*)\n```\s*',content,re.S)
    return json.loads(match[1] if match else content)


def recover_envelopes(root):
    root=Path(root)
    with ledger_lock(root):
        path=root/'inference_usage.json';ledger=json.loads(path.read_text())
        recovered=[]
        for attempt in ledger['requests']:
            if attempt.get('error_type')!='JSONDecodeError':continue
            raw=json.loads((root/'responses'/(attempt['cache_key']+'.json')).read_text())
            if raw['choices'][0]['finish_reason']!='stop':continue
            try:result=decode_content(raw['choices'][0]['message']['content'])
            except (ValueError,TypeError):continue
            if not isinstance(result,dict) or not isinstance(result.get('evidence'),list):continue
            save(root/'cache'/(attempt['cache_key']+'.json'),result)
            attempt.update(original_failure_status=attempt['status'],status='completed_after_offline_envelope_recovery',
                recovery_method='Removed one outer JSON code fence; raw response unchanged; no further inference')
            recovered.append(attempt['cache_key'])
        save(path,ledger)
        return recovered


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    tmp.replace(path)


def http(url, *, key=None, payload=None, timeout=30):
    import certifi  # already installed in the repository runtime
    headers = {'Content-Type': 'application/json'}
    if key: headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url, headers=headers,
        data=stable(payload).encode() if payload is not None else None)
    def expired(*_): raise TimeoutError('Total request deadline exceeded')
    previous=signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                context=ssl.create_default_context(cafile=certifi.where())) as response:
            return json.load(response)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)


def verify_catalog(url, base_url, model, output):
    """Free OpenRouter endpoint catalog request; never sends a key or PDF."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    if u.scheme != 'https' or u.netloc != 'openrouter.ai' or base_url != 'https://openrouter.ai/api/v1':
        raise ValueError('Automatic discovery supports the inspected OpenRouter catalog only')
    data = http(url)['data']
    if data['id'] != model: raise ValueError('Exact catalog model mismatch')
    modalities = data['architecture']['input_modalities']
    endpoints = [e for e in data['endpoints'] if e.get('status') == 0]
    if not endpoints or 'text' not in modalities: raise ValueError('No available text endpoint')
    # Conservative highest advertised rates, also enforced via provider.max_price.
    pricing = {k: max(float(e['pricing'][k]) for e in endpoints) for k in ('prompt', 'completion')}
    result = dict(base_url=base_url, model=model, modalities=modalities, pricing_per_token=pricing,
        verified_at=datetime.now(timezone.utc).isoformat(), catalog_url=url,
        catalog_sha256=digest(stable(data).encode()), catalog=data,
        pricing_basis='maximum advertised active endpoint rate; provider price caps enforced')
    save(output, result)
    return {k: result[k] for k in ('model', 'modalities', 'pricing_per_token', 'verified_at')}


@contextmanager
def ledger_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


class Client:
    def __init__(self, root, verification):
        self.root = Path(root)
        self.verification = json.loads(Path(verification).read_text())
        self.base = os.environ.get('PILOT_API_ENDPOINT', '').rstrip('/')
        self.model = os.environ.get('PILOT_MODEL_ID', '')
        self.key = os.environ.get('PILOT_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
        self.timeout = float(os.environ.get('PILOT_REQUEST_TIMEOUT', '90'))
        self.input_limit = int(os.environ.get('PILOT_MAX_INPUT_TOKENS', '16000'))
        self.output_limit = int(os.environ.get('PILOT_MAX_OUTPUT_TOKENS', '2200'))
        self.budget = float(os.environ.get('PILOT_BUDGET_USD', '0'))
        self.max_calls = int(os.environ.get('PILOT_MAX_CALLS', '0'))
        if self.base != self.verification['base_url'] or self.model != self.verification['model']:
            raise ValueError('Endpoint/model not verified')
        age = datetime.now(timezone.utc) - datetime.fromisoformat(self.verification['verified_at'])
        if not 0 <= age.total_seconds() < 86400: raise ValueError('Refresh capability/pricing verification')
        if not all(math.isfinite(x) and x > 0 for x in (self.timeout, self.budget, self.input_limit, self.output_limit, self.max_calls)):
            raise ValueError('Explicit positive finite budget, call and token limits required')
        if not self.key: raise ValueError('API key missing; no request sent')
        self.pricing = self.verification['pricing_per_token']
        if not all(math.isfinite(self.pricing[k]) and self.pricing[k] >= 0 for k in ('prompt', 'completion')):
            raise ValueError('Unverified pricing')

    def call(self, packet, prompt_version, messages):
        if packet['task']=='visual' and 'image' not in self.verification['modalities']:
            raise ValueError('Image support not verified')
        config = dict(endpoint=self.base, model=self.model, max_tokens=self.output_limit,
                      prompt_version=prompt_version, temperature=0)
        cache_key = digest(stable(dict(packet=packet, messages=messages, config=config)).encode())
        # A byte per token plus fixed overhead deliberately overestimates native text.
        upper_input = len(stable(messages).encode()) + 1024
        if upper_input > self.input_limit: raise ValueError('Bounded section exceeds input budget; no truncation')
        reserve = upper_input * self.pricing['prompt'] + self.output_limit * self.pricing['completion']
        with ledger_lock(self.root):
            usage_path = self.root / 'inference_usage.json'
            ledger = json.loads(usage_path.read_text()) if usage_path.exists() else {'requests': []}
            cached = self.root / 'cache' / (cache_key + '.json')
            if cached.exists(): return json.loads(cached.read_text()), cache_key
            charged = sum(r['charged_or_reserved_usd'] for r in ledger['requests'])
            if len(ledger['requests']) >= self.max_calls or charged + reserve > self.budget:
                raise ValueError('Hard pilot budget/call limit reached')
            payload = dict(model=self.model, messages=messages, temperature=0, max_tokens=self.output_limit)
            if self.base == 'https://openrouter.ai/api/v1':
                payload['provider'] = {'max_price': {'prompt': self.pricing['prompt']*1e6,
                                                       'completion': self.pricing['completion']*1e6}}
                payload['reasoning'] = {'effort': 'none'}
            attempt = dict(cache_key=cache_key, packet=packet, configuration=config,
                started_at=datetime.now(timezone.utc).isoformat(), input_tokens_upper_bound=upper_input,
                charged_or_reserved_usd=reserve, status='reserved', input_tokens=None, output_tokens=None,
                latency_seconds=None, cost_usd=None)
            ledger['requests'].append(attempt)
            save(usage_path, ledger)  # Reserve before network, including crash ambiguity.
            save(self.root / 'requests' / (cache_key + '.json'), payload)
            started = time.monotonic()
            try:
                raw = http(self.base + '/chat/completions', key=self.key, payload=payload, timeout=self.timeout)
                save(self.root / 'responses' / (cache_key + '.json'), raw)
                usage = raw.get('usage', {})
                attempt.update(input_tokens=usage.get('prompt_tokens'), output_tokens=usage.get('completion_tokens'),
                               cost_usd=usage.get('cost'))
                finish = raw['choices'][0]['finish_reason']
                if finish != 'stop': raise ValueError('Incomplete model request: ' + str(finish))
                result = decode_content(raw['choices'][0]['message']['content'])
                if not isinstance(result.get('evidence'), list): raise ValueError('Invalid evidence envelope')
                attempt['status'] = 'completed'
                save(cached, result)
                return result, cache_key
            except (Exception, KeyboardInterrupt) as exc:
                # Never log exception payloads/headers that might expose credentials.
                attempt.update(status='failed_or_truncated', error_type=type(exc).__name__)
                raise RuntimeError('Inference failed; inspect retained response and ledger') from None
            finally:
                attempt['latency_seconds'] = time.monotonic() - started
                # Keep reservation even if a provider reports a smaller cost. This is
                # a conservative spending cap, not an assertion of actual billing.
                save(usage_path, ledger)
