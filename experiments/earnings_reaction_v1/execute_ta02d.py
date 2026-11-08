"""One-shot TA-02D execution harness; reuses the existing candle adapter.

Credentials pass to system curl through stdin only. No redirects or retries.
Run only under the owner's TA-02D authorization; an exclusive ledger prevents reruns.
"""
import gzip
import hashlib
import io
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError

from .upstox_candles import (
    REQUESTS, SCOPE, ProviderError, parse_response, retrieve_bounded,
    url_for, verify_instrument_records,
)

ROOT = Path('artifacts/earnings-reaction-v1/ta02b')
LEDGER = ROOT / 'ta02d-execution.json'


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, check=False)


def secure_supplier():
    # Repeat custody checks immediately before accessing credential bytes.
    if git('ls-files', '--', '.env').stdout.strip():
        raise ValueError('STOP_ENV_TRACKED')
    if git('check-ignore', '-q', '.env').returncode != 0:
        raise ValueError('STOP_ENV_NOT_IGNORED')
    history = git('log', '--all', '--format=%H', '--', '.env')
    if history.returncode or history.stdout.strip():
        raise ValueError('STOP_ENV_HISTORY_UNSAFE_OR_UNCHECKABLE')
    found = {}
    for line in Path('.env').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().removeprefix('export ').strip()
        if key not in ('UPSTOX_ANALYTICS_TOKEN', 'UPSTOX_ACCESS_TOKEN'):
            continue
        if key in found:
            raise ValueError('STOP_DUPLICATE_CREDENTIAL_KEY')
        value = value.strip()
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('STOP_MALFORMED_CREDENTIAL_QUOTING')
            value = value[1:-1]
        if not re.fullmatch(r'[A-Za-z0-9._~+/=-]+', value):
            raise ValueError('STOP_MALFORMED_CREDENTIAL')
        if len(value) < 20:
            raise ValueError('STOP_MALFORMED_CREDENTIAL')
        found[key] = value
    if len(found) != 1:
        raise ValueError('STOP_MISSING_OR_AMBIGUOUS_CREDENTIAL')
    return next(iter(found.values()))


def main():
    # Verify source identity, retained metadata and exact request plan before secrets.
    checks = []
    for line in (ROOT / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split(None, 1)
        digest = hashlib.sha256(Path(name).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError('STOP_SOURCE_OR_EVIDENCE_IDENTITY_CHANGED')
        checks.append(dict(path=name, sha256=digest))
    metadata = json.loads((ROOT / 'public-instrument-metadata.json').read_bytes())
    master = (ROOT / metadata['source_ref']).read_bytes()
    assert hashlib.sha256(master).hexdigest() == metadata['source_response_sha256']
    records = [r for r in json.loads(gzip.decompress(master))
               if r.get('instrument_key') in {q.instrument_key for q in REQUESTS}]
    assert sorted(records, key=lambda r: r['instrument_key']) == sorted(
        metadata['records'], key=lambda r: r['instrument_key'])
    verify_instrument_records(records)
    plan = json.loads((ROOT / 'request-plan.json').read_bytes())
    assert plan['scope'] == SCOPE
    assert [r['url'] for r in plan['requests']] == [url_for(q) for q in REQUESTS]
    assert [(q.instrument_key, q.unit, q.interval, str(q.from_date), str(q.to_date)) for q in REQUESTS] == [
        ('NSE_EQ|INE0BWX01014', 'days', 1, '2025-07-01', '2026-08-14'),
        ('NSE_EQ|INE0BWX01014', 'minutes', 5, '2026-07-30', '2026-08-03'),
        ('NSE_INDEX|Nifty 50', 'days', 1, '2025-07-01', '2026-08-14')]
    state = dict(started_at_utc=datetime.now(timezone.utc).isoformat(),
                 authorization_ref='Owner TASK TA-02D and subsequent continue instruction, 2026-10-31',
                 scope=SCOPE, preflight_hashes=checks,
                 env_git_ignored=True, env_untracked=True, env_reachable_history_absent=True,
                 transport='system curl; HTTPS only; TLS verification; no redirects; no retries; stdin credential',
                 requests=[], failure=None, datasets=[])
    # Exclusive creation prohibits a second execution under this authorization.
    with LEDGER.open('x') as f:
        json.dump(state, f, indent=2)

    def save():
        LEDGER.write_text(json.dumps(state, indent=2) + '\n')

    def opener(req, timeout):
        n = len(state['requests'])
        if n >= 3 or req.full_url != url_for(REQUESTS[n]) or req.get_method() != 'GET':
            raise ValueError('STOP_REQUEST_SCOPE_MISMATCH')
        record = dict(number=n+1, url=req.full_url, http_status=None,
                      attempted_at_utc=datetime.now(timezone.utc).isoformat())
        state['requests'].append(record)
        save()
        auth = req.get_header('Authorization')
        # Curl default configuration is disabled; no token in argv, files or env.
        config = 'header = "Accept: application/json"\nheader = "' + 'Authorization: ' + auth + '"\n'
        result = subprocess.run([
            '/usr/bin/curl', '--disable', '--config', '-', '--silent',
            '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '0',
            '--retry', '0', '--max-time', str(timeout), '--request', 'GET',
            '--write-out', '\n%{http_code}', req.full_url,
        ], input=config.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            record['transport_exit_code'] = result.returncode
            save()
            raise OSError('transport_failure_no_retry')
        raw, status = result.stdout.rsplit(b'\n', 1)
        status = int(status)
        record['http_status'] = status
        record['received_at_utc'] = datetime.now(timezone.utc).isoformat()
        record['response_sha256'] = hashlib.sha256(raw).hexdigest()
        save()
        # Refuse retention if the provider unexpectedly echoes the credential.
        if auth.removeprefix('Bearer ').encode() in raw:
            raise ValueError('STOP_UNSAFE_PROVIDER_ECHO_NO_BODY_RETAINED')
        if status != 200:
            raise HTTPError(req.full_url, status, 'provider_request_stopped', {}, io.BytesIO(raw))
        response = io.BytesIO(raw)
        response.status = status
        return response

    try:
        retrieve_bounded(
            authorization=dict(scope=SCOPE, owner_approval_ref=state['authorization_ref'],
                               allow_raw_retention=True, allow_entitlement_probe=True),
            token_supplier=secure_supplier, instrument_records=records,
            metadata_ref='public-instrument-metadata.json',
            output_dir=ROOT / 'ta02d-retrieved', opener=opener)
    except ProviderError as e:
        state['failure'] = dict(kind=e.kind, http_status=e.http_status,
                                response_sha256=e.response_sha256)
    except Exception as e:
        # Exception text and tracebacks may contain private transport details.
        state['failure'] = dict(kind=type(e).__name__, detail='stopped_no_retry')
    save()

    # Reparse the exact retained bytes offline, including earlier successes if stopped.
    for i, q in enumerate(REQUESTS, 1):
        paths = list((ROOT / 'ta02d-retrieved').glob(f'{i}-*.json'))
        paths = [p for p in paths if not p.name.endswith('.validation.json')]
        if not paths:
            continue
        assert len(paths) == 1
        p = paths[0]
        raw = p.read_bytes()
        receipt = state['requests'][i-1]
        b = parse_response(raw, q, retrieved_at=datetime.fromisoformat(receipt['received_at_utc']),
                           raw_response_ref=str(p.relative_to(ROOT)),
                           instrument_metadata_ref='public-instrument-metadata.json',
                           instrument_metadata_verified=True)
        assert b.response_sha256 == receipt['response_sha256']
        stamps = [c.start_ist for c in b.candles]
        counts = Counter(stamps)
        by_day = {}
        for c in b.candles:
            by_day.setdefault(str(c.start_ist.date()), []).append(c.start_ist)
        observed = []
        for day, starts in sorted(by_day.items()):
            ordered = sorted(set(starts))
            observed.append(dict(day=day, candles=len(starts), first=ordered[0].isoformat(),
                                 last=ordered[-1].isoformat(),
                                 within_observed_day_missing_5m_slots=(
                                     sum(max(0, int((z-a).total_seconds()/300)-1)
                                         for a,z in zip(ordered, ordered[1:])) if q.unit=='minutes' else None)))
        state['datasets'].append(dict(
            number=i, instrument_key=q.instrument_key, unit=q.unit, interval=q.interval,
            requested_from=str(q.from_date), requested_to=str(q.to_date), http_status=200,
            candles=len(stamps), earliest=min(stamps).isoformat() if stamps else None,
            latest=max(stamps).isoformat() if stamps else None,
            provider_first=stamps[0].isoformat() if stamps else None,
            provider_last=stamps[-1].isoformat() if stamps else None,
            schema_ohlcv_valid=True, malformed_candles=0,
            duplicate_excess=sum(v-1 for v in counts.values()),
            timezone_offsets_seconds=sorted({int(datetime.fromisoformat(c.provider_timestamp).utcoffset().total_seconds()) for c in b.candles}),
            outside_requested_dates=sum(not q.from_date<=s.date()<=q.to_date for s in stamps),
            observed_days=observed, issues=list(b.issues),
            calendar_verified=False, missing_sessions='UNKNOWN_WITHOUT_VERIFIED_CALENDAR',
            partial_or_unexpected_sessions='UNVERIFIED_WITHOUT_HISTORICAL_SESSION_EVIDENCE',
            adjustment_status=b.adjustment_status, candle_verified=b.candle_verified,
            backtest_admissible=False, raw_path=str(p), response_sha256=b.response_sha256,
            validation_path=str(p.with_suffix('.validation.json'))))
    state['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    state['success'] = len(state['datasets']) == 3 and all(d['candles'] for d in state['datasets']) and state['failure'] is None
    save()
    print(json.dumps(dict(success=state['success'], requests=state['requests'], failure=state['failure'],
                          datasets=[{k:v for k,v in d.items() if k!='observed_days'} for d in state['datasets']]), indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'execution_stopped':type(e).__name__, 'no_automatic_retry':True}))
