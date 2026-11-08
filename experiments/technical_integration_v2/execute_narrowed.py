"""One-shot, owner-approved reduced TECH-06 acquisition. Never rerun a ledger.

Use the established secure supplier and V3 URL builder. The old candle parser
assumes INR/NSE sessions and cannot establish global-instrument semantics.
"""
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import re
import subprocess
import time

from .prepare_narrowed import ROOT, OUT, NUMBERS, sha
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import url_for

APPROVED = 'ad779a7b5b26ea5bd0e3854ef7bdf04a8be6ea06d493eef17b9768117b737686'
APPROVAL = 'Owner message: Approved. Execute the reduced 16-request Upstox manifest.'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, obj):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(obj, indent=2) + '\n')
    temporary.replace(path)


def strict_payload(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('nonfinite_json')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)


def classify_error(status, payload):
    """Explicit key rejection is local; entitlement and transient errors are not."""
    errors = payload.get('errors', []) if isinstance(payload, dict) else []
    codes = [str(e.get('errorCode', e.get('error_code', ''))) for e in errors if isinstance(e, dict)]
    if status in (401, 403, 429):
        return 'GLOBAL_STOP_AUTH_ENTITLEMENT_OR_RATE_LIMIT', codes
    if status == 400 and codes and set(codes) == {'UDAPI100011'}:
        return 'SERIES_STOP_PROVIDER_INVALID_INSTRUMENT_KEY', codes
    messages = ' '.join(str(e.get('message', '')) for e in errors if isinstance(e, dict)).lower()
    if status == 400 and re.search(r'(unsupported|not supported).*(interval|timeframe)|(interval|timeframe).*(unsupported|not supported)', messages):
        return 'INTERVAL_STOP_EXPLICITLY_UNSUPPORTED', codes
    if status == 400 and re.search(r'(instrument|global).*(not supported|unsupported)|(not supported|unsupported).*(instrument|global)', messages):
        return 'SERIES_STOP_EXPLICITLY_UNSUPPORTED', codes
    return 'GLOBAL_STOP_UNEXPECTED_HTTP_OR_SCHEMA', codes


def execute():
    manifest = OUT / 'UPSTOX_REDUCED_MANIFEST.json'
    if sha(manifest) != APPROVED:
        raise PermissionError('approved manifest digest mismatch')
    plan = json.loads(manifest.read_text())
    original = ROOT / plan['original_manifest']
    if sha(original) != plan['original_manifest_sha256']:
        raise ValueError('original evidence changed')
    source = json.loads(original.read_text())
    if [r['number'] for r in plan['requests']] != NUMBERS:
        raise ValueError('request order mismatch')
    master = []
    for path, digest in plan['metadata'].items():
        if sha(ROOT / path) != digest:
            raise ValueError('metadata changed')
        master.extend(json.loads(gzip.decompress((ROOT / path).read_bytes())))
    for r in plan['requests']:
        if r != next(x for x in source['requests'] if x['number'] == r['number']):
            raise ValueError('original request changed')
        matches = [m for m in master if m.get('instrument_key') == r['instrument_key']]
        if matches != [r['identity']]:
            raise ValueError('instrument identity mismatch')
        q = DailyRequest(r['instrument_key'], r['unit'], r['interval'], date.fromisoformat(r['from_date']), date.fromisoformat(r['to_date']))
        if url_for(q) != r['url']:
            raise ValueError('url mismatch')
    baseline = json.loads((OUT / 'PRESERVATION_BASELINE.json').read_text())
    if not all(sha(ROOT / path) == digest for path, digest in baseline.items()):
        raise ValueError('preservation baseline changed before execution')
    ledger = OUT / 'EXECUTION_LEDGER.json'
    state = dict(manifest_sha256=APPROVED, owner_approval_reference=APPROVAL,
                 approved_request_count=16, automatic_retries=0,
                 started_at=now(), requests=[], complete=False, global_stop=None)
    with ledger.open('x') as stream:
        json.dump(state, stream, indent=2)
    save(OUT / 'OWNER_AUTHORIZATION.json', dict(manifest_sha256=APPROVED, approval=APPROVAL,
         recorded_at=now(), paid_upgrades_authorized=False, external_acquisition_authorized=False))
    raw_dir = OUT / 'raw'
    raw_dir.mkdir(exist_ok=True)
    stopped_keys, stopped_intervals = {}, {}
    try:
        from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
        token = secure_supplier()
        if not isinstance(token, str) or len(token) < 20 or any(c in token for c in '\r\n"\\'):
            raise ValueError('unsafe credential handoff')
        last_finished = None
        for r in plan['requests']:
            rec = dict(number=r['number'], series=r['series'], instrument_key=r['instrument_key'],
                       unit=r['unit'], interval=r['interval'], url=r['url'],
                       from_date=r['from_date'], to_date=r['to_date'], attempted=False)
            state['requests'].append(rec)
            reason = state['global_stop'] or stopped_keys.get(r['instrument_key']) or stopped_intervals.get((r['instrument_key'], r['unit']))
            if reason:
                rec.update(status='SKIPPED', reason=reason)
                save(ledger, state)
                continue
            if last_finished is not None:
                time.sleep(max(0, 1.05 - (time.monotonic() - last_finished)))
            rec.update(attempted=True, attempted_at=now(), status='ATTEMPT_STARTED')
            save(ledger, state)  # Durable before GET; a crashed attempt is never retried.
            config = 'header = "Accept: application/json"\nheader = "Authorization: Bearer ' + token + '"\n'
            result = subprocess.run([
                '/usr/bin/curl', '--disable', '--config', '-', '--silent', '--proto', '=https',
                '--max-redirs', '0', '--retry', '0', '--max-time', '30', '--request', 'GET',
                '--write-out', '\n%{http_code}', r['url']], input=config.encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            last_finished = time.monotonic()
            rec['received_at'] = now()
            if result.returncode:
                rec.update(status='TRANSPORT_FAILURE', curl_exit_code=result.returncode)
                state['global_stop'] = 'TRANSPORT_FAILURE_NO_RETRY'
                save(ledger, state)
                continue
            raw, status = result.stdout.rsplit(b'\n', 1)
            status = int(status)
            rec.update(http_status=status, response_sha256=hashlib.sha256(raw).hexdigest(), response_bytes=len(raw))
            if token.encode() in raw:
                rec['status'] = 'UNSAFE_PROVIDER_ECHO_NOT_RETAINED'
                state['global_stop'] = rec['status']
                save(ledger, state)
                continue
            try:
                payload = strict_payload(raw)
            except (ValueError, UnicodeError):
                payload = None
            if status != 200 or not isinstance(payload, dict) or payload.get('status') != 'success':
                classification, codes = classify_error(status, payload)
                rec.update(status=classification, provider_error_codes=codes)
                # Whitelisted semantic descriptions, never raw arbitrary provider messages.
                if classification.startswith('SERIES_STOP'):
                    stopped_keys[r['instrument_key']] = f"request {r['number']}: {classification}"
                    rec['provider_message_summary'] = 'Provider rejected the exact catalog instrument for this endpoint; no replacement key tried.'
                elif classification.startswith('INTERVAL_STOP'):
                    stopped_intervals[(r['instrument_key'], r['unit'])] = f"request {r['number']}: {classification}"
                    rec['provider_message_summary'] = 'Provider explicitly reports this interval unsupported.'
                else:
                    state['global_stop'] = f"request {r['number']}: {classification}"
            else:
                path = raw_dir / f"{r['number']:02d}-{rec['response_sha256']}.json"
                with path.open('xb') as stream:
                    stream.write(raw)
                rec['retained_path'] = str(path.relative_to(ROOT))
                data = payload.get('data')
                candles = data.get('candles') if isinstance(data, dict) else None
                if not isinstance(candles, list):
                    rec['status'] = 'GLOBAL_STOP_MALFORMED_CANDLE_SCHEMA'
                    state['global_stop'] = f"request {r['number']}: {rec['status']}"
                else:
                    rec.update(status='SUCCESS' if candles else 'EMPTY_WINDOW', returned_bars=len(candles))
            save(ledger, state)
            print(json.dumps({k: rec[k] for k in ('number', 'series', 'unit', 'status', 'http_status', 'returned_bars') if k in rec}), flush=True)
        state['complete'] = state['global_stop'] is None
    except Exception as error:
        state['global_stop'] = 'LOCAL_FAILURE_' + type(error).__name__ + ';NO_RETRY;SENSITIVE_DETAILS_OMITTED'
        # Preserve the full original-number ledger even after local interruption.
        recorded = {r['number'] for r in state['requests']}
        state['requests'].extend(dict(number=r['number'], series=r['series'], unit=r['unit'],
            instrument_key=r['instrument_key'], attempted=False, status='SKIPPED', reason=state['global_stop'])
            for r in plan['requests'] if r['number'] not in recorded)
    state.update(finished_at=now(), attempted_count=sum(r['attempted'] for r in state['requests']),
                 skipped_count=sum(not r['attempted'] for r in state['requests']))
    save(ledger, state)
    print(json.dumps({k: state[k] for k in ('complete', 'attempted_count', 'skipped_count', 'global_stop')}), flush=True)
    if state['global_stop']:
        raise SystemExit('Stopped without retry; inspect sanitized ledger.')


if __name__ == '__main__':
    execute()
