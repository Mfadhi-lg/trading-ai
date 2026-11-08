"""Exact-manifest-gated research acquisition using ER03's existing transport/parser.

Run only after explicit owner authorization; a matching CLI hash is a safeguard,
not authorization by itself. No retries, substitutions or scope expansion.
"""
from dataclasses import asdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import subprocess
import time
from .er03 import ROOT, digest, write_json
from .er03d import OUT
from .retrieve_er03 import DailyRequest
from .upstox_candles import parse_response, url_for

def execute(authorized_sha256):
    manifest=OUT/'acquisition-manifest.json'
    if digest(manifest)!=authorized_sha256:raise PermissionError('Exact approved manifest hash required')
    plan=json.loads(manifest.read_text())
    metadata=ROOT/plan['metadata_path']
    if digest(metadata)!=plan['metadata_sha256']:raise ValueError('Metadata hash mismatch')
    master=json.loads(gzip.decompress(metadata.read_bytes()))
    requests=[];identities=[]
    for r in plan['requests']:
        found=[m for m in master if m.get('instrument_key')==r['instrument_key'] and m.get('trading_symbol')==r['symbol']]
        if len(found)!=1:raise ValueError('Unique provider instrument identity required')
        if (r['unit'],r['interval'])!=('days',1):raise ValueError('Daily only')
        q=DailyRequest(r['instrument_key'],'days',1,date.fromisoformat(r['from_date']),date.fromisoformat(r['to_date']))
        if url_for(q)!=r['url']:raise ValueError('URL mismatch')
        requests.append(q);identities.append(found[0])
    if len(requests)!=11:raise ValueError('Exact eleven requests required')
    state=dict(scope_sha256=authorized_sha256,owner_approval_reference='Explicit owner approval required in current chat',
               started_at=datetime.now(timezone.utc).isoformat(),requests=[],complete=False,failure=None,automatic_retries=0,token_in_artifacts=False)
    ledger=OUT/'retrieval-execution.json'
    with ledger.open('x') as f:json.dump(state,f,indent=2)
    identity=OUT/'instrument-identity.json';write_json(identity,dict(metadata_sha256=digest(metadata),records=identities))
    provider=OUT/'provider';provider.mkdir(exist_ok=True)
    try:
        from .execute_ta02d import secure_supplier
        token=secure_supplier()
        if not isinstance(token,str) or len(token)<20 or any(c in token for c in '\r\n"\\'):raise ValueError('Invalid credential handoff')
        for n,q in enumerate(requests,1):
            if n>1:time.sleep(1)
            record=dict(number=n,instrument_key=q.instrument_key,unit=q.unit,interval=q.interval,from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),attempted_at=datetime.now(timezone.utc).isoformat())
            state['requests'].append(record);write_json(ledger,state)
            config='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
            result=subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent','--proto','=https','--max-redirs','0','--retry','0','--max-time','30','--request','GET','--write-out','\n%{http_code}',url_for(q)],input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            if result.returncode:raise OSError('Transport failure')
            raw,status=result.stdout.rsplit(b'\n',1);status=int(status)
            record.update(http_status=status,received_at=datetime.now(timezone.utc).isoformat(),response_sha256=hashlib.sha256(raw).hexdigest());write_json(ledger,state)
            if token.encode() in raw:raise ValueError('Unsafe provider echo')
            dest=provider/f'{n}-{record["response_sha256"]}.json'
            batch=parse_response(raw,q,http_status=status,retrieved_at=datetime.fromisoformat(record['received_at']),raw_response_ref=str(dest.relative_to(ROOT)),instrument_metadata_ref=str(identity.relative_to(ROOT)),instrument_metadata_verified=True)
            permitted={'calendar_unresolved:gaps_and_session_completeness_unknown','provider_descending_order:normalize_explicitly_before_analysis'}
            if not batch.candles or any(i not in permitted for i in batch.issues):raise ValueError('Unexpected provider quality issue')
            with dest.open('xb') as f:f.write(raw)
            write_json(dest.with_suffix('.validation.json'),asdict(batch))
            record.update(retained_path=str(dest.relative_to(ROOT)),rows=len(batch.candles),issues=batch.issues);write_json(ledger,state)
            print(json.dumps(dict(number=n,rows=len(batch.candles),http_status=status)),flush=True)
        state['complete']=True
    except Exception as error:
        state['failure']=dict(kind=type(error).__name__,detail='STOPPED_NO_RETRY; exception text and error bodies not retained')
    state['finished_at']=datetime.now(timezone.utc).isoformat();write_json(ledger,state)
    if not state['complete']:raise SystemExit('Acquisition stopped; inspect sanitized ledger; no retry')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--authorize-manifest-sha256',required=True)
    execute(p.parse_args().authorize_manifest_sha256)
