"""Owner-approved ER03 daily acquisition; existing Upstox parser and secure supplier.
No production changes, scope expansion, automatic retries or credential artifacts.
"""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import subprocess
import time
from .er03 import ROOT, OUT, digest, write_json
from .upstox_candles import parse_response, url_for

APPROVED_SHA256='7bf3ca6b61fb97fe02a3f33dde9330c79432ebfe810e5533b8ab02f46652014e'

@dataclass(frozen=True)
class DailyRequest:
    instrument_key: str
    unit: str
    interval: int
    from_date: date
    to_date: date


def execute():
    path=OUT/'acquisition-manifest.json'
    if digest(path)!=APPROVED_SHA256:
        raise PermissionError('approved manifest digest mismatch')
    plan=json.loads(path.read_text())
    metadata=ROOT/plan['metadata_path']
    if digest(metadata)!=plan['metadata_sha256']:
        raise ValueError('instrument metadata digest mismatch')
    master=json.loads(gzip.decompress(metadata.read_bytes()))
    records=[]; requests=[]
    for r in plan['requests']:
        matches=[m for m in master if m.get('instrument_key')==r['instrument_key']]
        if len(matches)!=1 or matches[0].get('trading_symbol') not in (r['symbol'],'NIFTY MIDSML 400'):
            raise ValueError('unique matching instrument identity required')
        if (r['unit'],r['interval'])!=('days',1):
            raise ValueError('daily only')
        q=DailyRequest(r['instrument_key'],'days',1,date.fromisoformat(r['from_date']),date.fromisoformat(r['to_date']))
        if url_for(q)!=r['url']:raise ValueError('URL mismatch')
        requests.append(q);records.append(matches[0])
    if len(requests)!=12:raise ValueError('exact twelve requests required')
    state=dict(scope_sha256=APPROVED_SHA256,owner_approval_reference='User approved ER03 bounded 12-request manifest in chat, 2026-11-03',
        started_at=datetime.now(timezone.utc).isoformat(),requests=[],complete=False,failure=None,
        automatic_retries=0,pacing='serial >=1s',token_in_artifacts=False)
    ledger=OUT/'retrieval-execution.json'
    with ledger.open('x') as f:json.dump(state,f,indent=2)
    write_json(OUT/'instrument-identity.json',dict(metadata_sha256=digest(metadata),records=records))
    provider=OUT/'provider';provider.mkdir(exist_ok=True)
    def save():write_json(ledger,state)
    try:
        from .execute_ta02d import secure_supplier
        token=secure_supplier()
        if not isinstance(token,str) or len(token)<20 or any(c in token for c in '\r\n"\\'):
            raise ValueError('invalid credential handoff')
        for n,q in enumerate(requests,1):
            if n>1:time.sleep(1)
            record=dict(number=n,instrument_key=q.instrument_key,unit=q.unit,interval=q.interval,
                from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),attempted_at=datetime.now(timezone.utc).isoformat())
            state['requests'].append(record);save()
            config='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
            result=subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent','--proto','=https',
                '--max-redirs','0','--retry','0','--max-time','30','--request','GET','--write-out','\n%{http_code}',url_for(q)],
                input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            if result.returncode:raise OSError('transport failure')
            raw,status=result.stdout.rsplit(b'\n',1);status=int(status)
            record.update(http_status=status,received_at=datetime.now(timezone.utc).isoformat(),response_sha256=hashlib.sha256(raw).hexdigest());save()
            if token.encode() in raw:raise ValueError('unsafe provider echo')
            dest=provider/f'{n}-{record["response_sha256"]}.json'
            batch=parse_response(raw,q,http_status=status,retrieved_at=datetime.fromisoformat(record['received_at']),
                raw_response_ref=str(dest.relative_to(ROOT)),instrument_metadata_ref=str((OUT/'instrument-identity.json').relative_to(ROOT)),instrument_metadata_verified=True)
            if not batch.candles:raise ValueError('empty provider interval')
            permitted={'calendar_unresolved:gaps_and_session_completeness_unknown','provider_descending_order:normalize_explicitly_before_analysis'}
            if any(i not in permitted for i in batch.issues):raise ValueError('unexpected provider quality issue')
            with dest.open('xb') as f:f.write(raw)
            write_json(dest.with_suffix('.validation.json'),asdict(batch))
            record.update(retained_path=str(dest.relative_to(ROOT)),rows=len(batch.candles),issues=batch.issues);save()
            print(json.dumps(dict(number=n,rows=len(batch.candles),http_status=status)),flush=True)
        state['complete']=True
    except Exception as error:
        state['failure']=dict(kind=type(error).__name__,detail='STOPPED_NO_RETRY; exception text and error bodies not retained')
    state['finished_at']=datetime.now(timezone.utc).isoformat();save()
    if not state['complete']:raise SystemExit('Acquisition stopped; inspect sanitized ledger; no retry')

if __name__=='__main__':execute()
