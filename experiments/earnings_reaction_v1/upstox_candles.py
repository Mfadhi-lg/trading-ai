"""Bounded Upstox V3 adapter. Default CLI prints a plan and never reads credentials.

Live use is a library call with a caller-owned secure token supplier. It requires
an explicit scope authorization record and independently checked provider metadata.
No credentials, headers, provider error bodies or exception text enter artifacts.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .candles import CandleBatch, CandleRequest, validate_rows
from .events import aware

AETHER = 'NSE_EQ|INE0BWX01014'
NIFTY = 'NSE_INDEX|Nifty 50'
SCOPE = 'TA-02B:AETHER-daily-20250701-20260814:AETHER-5m-20260730-20260803:NIFTY-daily-20250701-20260814'
REQUESTS = (
    CandleRequest(AETHER, 'days', 1, date(2025, 7, 1), date(2026, 8, 14)),
    CandleRequest(AETHER, 'minutes', 5, date(2026, 7, 30), date(2026, 8, 3)),
    CandleRequest(NIFTY, 'days', 1, date(2025, 7, 1), date(2026, 8, 14)),
)


def url_for(request):
    return ('https://api.upstox.com/v3/historical-candle/'
            f'{quote(request.instrument_key, safe="")}/{request.unit}/{request.interval}/'
            f'{request.to_date.isoformat()}/{request.from_date.isoformat()}')


class ProviderError(ValueError):
    def __init__(self, kind, raw, status):
        self.kind, self.response_sha256, self.http_status = kind, hashlib.sha256(raw).hexdigest(), status
        super().__init__(f'{kind}; HTTP {status}; sha256={self.response_sha256}')


def parse_response(raw: bytes, request, *, http_status=200, retrieved_at,
                   raw_response_ref='', instrument_metadata_ref=None,
                   instrument_metadata_verified=False, calendar=None,
                   calendar_verified=False, synthetic=False):
    digest = hashlib.sha256(raw).hexdigest()
    if http_status != 200:
        kind = {401: 'unauthorized', 403: 'forbidden_or_entitlement_denied',
                429: 'rate_limited_stop_no_retry'}.get(http_status, 'provider_http_error')
        raise ProviderError(kind, raw, http_status)
    try:
        def reject_constant(value):
            raise ValueError('non-finite JSON constant')
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate JSON field')
                result[key] = value
            return result
        payload = json.loads(raw, parse_float=Decimal, parse_constant=reject_constant,
                             object_pairs_hook=unique_object)
        if not isinstance(payload, dict) or payload.get('status') != 'success':
            raise ValueError('provider did not report success')
        rows = payload['data']['candles']
        if not isinstance(rows, list):
            raise ValueError('candles array required')
        candles, issues, missing = validate_rows(rows, request, retrieved_at=retrieved_at,
                                                calendar=calendar, calendar_verified=calendar_verified)
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError):
        raise ProviderError('invalid_provider_payload', raw, http_status) from None
    if not instrument_metadata_verified or not instrument_metadata_ref:
        issues += ('provider_instrument_metadata_unverified',)
    return CandleBatch(request, candles, issues, missing, digest,
                       aware(retrieved_at).astimezone(timezone.utc), http_status, raw_response_ref,
                       instrument_metadata_ref, calendar.evidence_ref if calendar else None, synthetic)


def verify_instrument_records(records):
    """Verify current provider keys only. Never certifies July mapping/tradability."""
    equity = [r for r in records if r.get('instrument_key') == AETHER]
    index = [r for r in records if r.get('instrument_key') == NIFTY]
    if len(equity) != 1 or len(index) != 1:
        raise ValueError('unique AETHER and Nifty records required')
    e, n = equity[0], index[0]
    if not (e.get('isin') == 'INE0BWX01014' and e.get('trading_symbol') == 'AETHER'
            and e.get('segment') == 'NSE_EQ' and e.get('instrument_type') == 'EQ'
            and e.get('exchange') == 'NSE'):
        raise ValueError('AETHER provider identity mismatch')
    if not (n.get('segment') == 'NSE_INDEX' and n.get('exchange') == 'NSE'
            and n.get('instrument_type') == 'INDEX'):
        raise ValueError('Nifty provider identity mismatch')
    return True


def request_plan():
    return {
        'scope': SCOPE, 'status': 'NOT_AUTHORIZED_NOT_EXECUTED',
        'owner_authorization_required': ['exact_three_requests', 'historical_market_data_entitlement_check',
                                         'raw_response_retention_under_ta02b', 'existing_secure_token_supplier'],
        'entitlement': 'UNTESTED; first bounded request is the entitlement probe; stop on any error',
        'instrument_preflight': 'verify hashed public NSE instrument JSON records before authenticated requests',
        'requests': [dict(asdict(r), url=url_for(r)) for r in REQUESTS],
        'pacing': 'serial; >=1 second apart; no automatic retries; stop on 401/403/429 or transport error',
        'warmup': 'July 1, 2025 through August 14, 2026; count verified sessions after retrieval; no indicator sufficiency claim',
        'event_classification': 'EXPLORATORY EVENT', 'event_status': 'EVENT_DATE_PROVISIONAL',
        'backtest_admissible': False, 'corporate_action_adjustment_status': 'UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',
    }


def retrieve_bounded(*, authorization, token_supplier, instrument_records, metadata_ref,
                     output_dir, opener=urlopen):
    """Only call after explicit owner approval. No retired-experiment credentials.

    authorization: scope, owner_approval_ref, allow_raw_retention=True,
    allow_entitlement_probe=True. This records approval; it cannot create it.
    token_supplier must integrate the owner's existing secure credential mechanism.
    The first candle GET tests entitlement without an extra account/API request.
    Returned batches stay provisional until calendar and quality checks are supplied.
    """
    import time
    if (authorization.get('scope') != SCOPE or not authorization.get('owner_approval_ref')
            or authorization.get('allow_raw_retention') is not True
            or authorization.get('allow_entitlement_probe') is not True):
        raise PermissionError('explicit owner authorization for exact TA-02B scope required')
    verify_instrument_records(instrument_records)
    if not metadata_ref:
        raise ValueError('hashed provider metadata reference required')
    root = Path('artifacts/earnings-reaction-v1/ta02b').resolve()
    output = Path(output_dir).resolve()
    if not output.is_relative_to(root):
        raise ValueError('evidence must stay under TA-02B')
    output.mkdir(parents=True, exist_ok=True)
    results = []
    token = token_supplier()  # Never read before scope and identity preflight.
    if not isinstance(token, str) or not token.strip() or any(c in token for c in '\r\n'):
        raise ValueError('invalid secure token handoff')
    for number, request in enumerate(REQUESTS):
        if number:
            time.sleep(1)
        req = Request(url_for(request), headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
        try:
            with opener(req, timeout=30) as response:
                raw, status = response.read(), response.status
        except HTTPError as error:
            raise ProviderError('request_stopped', error.read(), error.code) from None
        except (URLError, OSError):
            raise RuntimeError('transport_failure; no retry; no provider coverage claim') from None
        received = datetime.now(timezone.utc)
        raw_path = output / f'{number+1}-{hashlib.sha256(raw).hexdigest()}.json'
        batch = parse_response(raw, request, http_status=status, retrieved_at=received,
                               raw_response_ref=str(raw_path.relative_to(root)),
                               instrument_metadata_ref=metadata_ref, instrument_metadata_verified=True)
        # Only validated successful candle bodies are retained, never error bodies/headers.
        with raw_path.open('xb') as stream:
            stream.write(raw)
        with raw_path.with_suffix('.validation.json').open('x') as stream:
            json.dump(asdict(batch), stream, default=str, indent=2)
            stream.write('\n')
        results.append(batch)
        if not batch.candles:
            break
    return tuple(results)


if __name__ == '__main__':
    print(json.dumps(request_plan(), default=str, indent=2))
