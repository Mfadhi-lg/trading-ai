"""Exact TA-03R4 scope extension of the existing Upstox parser/secure supplier.

Default mode writes a public request plan; never accesses credentials.
Execution is forbidden until the owner approves this exact manifest's digest.
No old TA-02B allowlist, authorization or retained evidence is modified.
"""
from dataclasses import asdict, dataclass
from datetime import date,datetime,timezone
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import subprocess
import time

from .candles import CandleRequest
from .upstox_candles import parse_response,url_for

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/earnings-reaction-v1/ta03r4'
TIPSMUSIC='NSE_EQ|INE716B01029'
BENCHMARK='NSE_INDEX|NIFTY MIDSML 400'
METADATA=ROOT/'artifacts/earnings-reaction-v1/ta02b/public-nse-instruments.json.gz'
METADATA_SHA256='77cda8e87e1e52055c5485103468a8849acf4dcb89dcfd1a49d0670bd68828d8'
CHUNKS=(('2026-08-17','2026-09-13'),('2026-09-14','2026-10-11'),('2026-10-12','2026-10-30'))
STOCK_CHUNKS=(('2026-07-21','2026-08-17'),('2026-08-18','2026-09-14'),
              ('2026-09-15','2026-10-12'),('2026-10-13','2026-10-30'))
SPECS=((BENCHMARK,'days',1,'2026-02-02','2026-10-30'),
       (TIPSMUSIC,'days',1,'2025-04-01','2026-10-30'))+tuple(
    (BENCHMARK,'minutes',5,start,end) for start,end in CHUNKS)+tuple(
    (TIPSMUSIC,'minutes',1,start,end) for start,end in STOCK_CHUNKS)


@dataclass(frozen=True)
class ResearchRequest(CandleRequest):
    def __post_init__(self):
        identity=(self.instrument_key,self.unit,self.interval,str(self.from_date),str(self.to_date))
        if type(self.interval) is not int or identity not in SPECS:
            raise ValueError('outside exact TA-03R4 nine-request scope')
        if self.unit=='minutes' and (self.to_date-self.from_date).days > 27:
            raise ValueError('intraday chunk exceeds 28 inclusive calendar days')


REQUESTS=tuple(ResearchRequest(k,u,i,date.fromisoformat(a),date.fromisoformat(b)) for k,u,i,a,b in SPECS)


def verified_metadata():
    raw=METADATA.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=METADATA_SHA256:
        raise ValueError('public instrument master changed')
    records=[r for r in json.loads(gzip.decompress(raw)) if r.get('instrument_key') in (TIPSMUSIC,BENCHMARK)]
    stock=[r for r in records if r.get('instrument_key')==TIPSMUSIC]
    bench=[r for r in records if r.get('instrument_key')==BENCHMARK]
    if len(stock)!=1 or len(bench)!=1:
        raise ValueError('unique provider records required')
    if (stock[0].get('trading_symbol'),stock[0].get('isin'),stock[0].get('segment')) != ('TIPSMUSIC','INE716B01029','NSE_EQ'):
        raise ValueError('TIPSMUSIC identity mismatch')
    if (bench[0].get('trading_symbol'),bench[0].get('segment')) != ('NIFTY MIDSML 400','NSE_INDEX'):
        raise ValueError('benchmark identity mismatch')
    return records


def manifest():
    verified_metadata()
    return dict(scope='TA-03R4:TIPSMUSIC-NIFTYMIDSML400:OCT30:9-GETS-v2',
        status='NOT_AUTHORIZED_NOT_EXECUTED',request_count=len(REQUESTS),
        requests=[dict(number=n,**asdict(q),url=url_for(q)) for n,q in enumerate(REQUESTS,1)],
        retention_location='artifacts/earnings-reaction-v1/ta03r4/provider/',
        daily_comparison_window='2026-05-04 through 2026-10-30; includes dated Aug18 cursor reference',
        intraday_comparison_window='2026-10-01 through 2026-10-30; earlier intraday data is warmup and Aug18 daily RVOL-B reference support',
        rationale=dict(stock_daily='252 prior daily observations for May-onward research records; 21/50/55 and EMA/ATR seed history',
            benchmark_daily='55 prior observations for May-onward RS; no unnecessary 252-day benchmark lookback',
            stock_one_minute='July21-Aug17 supplies 20 preceding sessions for the dated Aug18 daily RVOL-B observation; finer session profiles and October display warmup',
            benchmark_five_minute='aligned closes for display-timeframe RS55; no benchmark volume-profile calculation'),
        metadata_path=str(METADATA.relative_to(ROOT)),metadata_sha256=METADATA_SHA256,
        historical_identity='current retained provider records verified; dated symbol/ISIN evidence recorded separately; interval coverage unprobed',
        limitations=['calendar/corporate actions and returned coverage must be checked before acceptance',
            'intraday comparison starts October; earlier screenshot intraday references are not supplied',
            'recursive seed parity not guaranteed by finite warmup',
            'no source-equivalence claim for alternatives',
            'benchmark failure stops the sequence; no substituted benchmark or fallback interval'],
        authorization_required=True,read_only=True,automatic_retries=0,
        entitlement_probe='first planned benchmark daily GET only; no additional account endpoint',
        stop_on='any HTTP/schema/transport error, empty response or unexpected instrument metadata',
        provider_limits_reference='https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/')


def canonical_bytes(plan):
    return (json.dumps(plan,default=str,indent=2,sort_keys=True)+'\n').encode()


def write_plan():
    OUT.mkdir(parents=True,exist_ok=True)
    payload=canonical_bytes(manifest())
    (OUT/'retrieval-plan.json').write_bytes(payload)
    digest=hashlib.sha256(payload).hexdigest()
    (OUT/'retrieval-plan.sha256').write_text(digest+'  retrieval-plan.json\n')
    return digest


def execute(*,approved_sha256,approval_reference,token_supplier=None,transport=None):
    """Run once only after genuine owner approval; test hooks never imply approval."""
    expected=canonical_bytes(manifest())
    if not approval_reference or approved_sha256!=hashlib.sha256(expected).hexdigest():
        raise PermissionError('exact owner-approved manifest digest required')
    if not (OUT/'retrieval-plan.json').is_file() or (OUT/'retrieval-plan.json').read_bytes()!=expected:
        raise PermissionError('reviewable retained request manifest required')
    # Scope/identity guards precede any secret access or network operation.
    provider=OUT/'provider'
    provider.mkdir(exist_ok=True)
    ledger=OUT/'retrieval-execution.json'
    state=dict(scope_sha256=approved_sha256,owner_approval_reference=approval_reference,
        started_at=datetime.now(timezone.utc).isoformat(),requests=[],complete=False,failure=None)
    with ledger.open('x') as stream:
        json.dump(state,stream,indent=2)
    def save():
        ledger.write_text(json.dumps(state,indent=2)+'\n')
    try:
        if token_supplier is None:
            from .execute_ta02d import secure_supplier
            token_supplier=secure_supplier
        token=token_supplier()
        if not isinstance(token,str) or len(token)<20 or any(ch in token for ch in '\r\n"\\'):
            raise ValueError('invalid credential handoff')
        for number,q in enumerate(REQUESTS,1):
            if number>1:
                time.sleep(1)
            record=dict(number=number,url=url_for(q),attempted_at=datetime.now(timezone.utc).isoformat())
            state['requests'].append(record)
            save()
            if transport is None:
                config='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
                result=subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent',
                    '--proto','=https','--max-redirs','0','--retry','0','--max-time','30',
                    '--request','GET','--write-out','\n%{http_code}',url_for(q)],
                    input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
                if result.returncode:
                    raise OSError('transport failure')
                raw,status=result.stdout.rsplit(b'\n',1)
                status=int(status)
            else:
                raw,status=transport(q,token)
            record.update(http_status=status,received_at=datetime.now(timezone.utc).isoformat(),
                          response_sha256=hashlib.sha256(raw).hexdigest())
            save()
            if token.encode() in raw:
                raise ValueError('unsafe provider echo; body not retained')
            raw_path=provider/f'{number}-{record["response_sha256"]}.json'
            batch=parse_response(raw,q,http_status=status,retrieved_at=datetime.fromisoformat(record['received_at']),
                raw_response_ref=str(raw_path.relative_to(ROOT)),instrument_metadata_ref='ta03r4/instrument-identity.json',
                instrument_metadata_verified=True)
            if not batch.candles:
                raise ValueError('empty historical interval; no fallback authorized')
            expected_issues={'calendar_unresolved:gaps_and_session_completeness_unknown',
                             'provider_descending_order:normalize_explicitly_before_analysis'}
            if any(issue not in expected_issues for issue in batch.issues):
                raise ValueError('provider quality issue; stop without silently normalizing defects')
            with raw_path.open('xb') as stream:
                stream.write(raw)
            with raw_path.with_suffix('.validation.json').open('x') as stream:
                json.dump(asdict(batch),stream,default=str,indent=2)
            record.update(retained_path=str(raw_path.relative_to(ROOT)),rows=len(batch.candles))
            save()
        state['complete']=True
    except Exception as error:
        state['failure']=dict(kind=type(error).__name__,detail='STOPPED_NO_RETRY;NO_EXCEPTION_TEXT_RETAINED')
    state['finished_at']=datetime.now(timezone.utc).isoformat()
    save()
    return state['complete']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--approved-sha256')
    parser.add_argument('--approval-reference')
    args=parser.parse_args()
    if not args.execute:
        print('NOT_AUTHORIZED_NOT_EXECUTED; nine GETs; manifest SHA256:',write_plan())
    else:
        if not execute(approved_sha256=args.approved_sha256,approval_reference=args.approval_reference):
            raise SystemExit('Bounded retrieval stopped; inspect sanitized ledger; no retry')


if __name__=='__main__':
    main()
