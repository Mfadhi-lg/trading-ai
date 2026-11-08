"""Exact owner-approved SS-INDEX-02 request execution, reusing ER03 mechanisms."""
from datetime import date, datetime, timezone
import gzip, hashlib, json, subprocess, time, sys
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import parse_response, url_for
from experiments.earnings_reaction_v1.er03 import ROOT, digest, write_json
from .prepare import OUT

APPROVED='1006e76d75c6292678d24b62903e26a247222d7f01db13ead76ad3eb07a37262'

def main():
    manifest=OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json'
    if digest(manifest)!=APPROVED:raise PermissionError('Manifest differs from owner-approved eight requests')
    plan=json.loads(manifest.read_text());metadata=ROOT/plan['metadata_path']
    if digest(metadata)!=plan['metadata_sha256']:raise ValueError('Metadata hash mismatch')
    master=json.loads(gzip.decompress(metadata.read_bytes()));requests=[]
    for r in plan['requests']:
        matches=[m for m in master if m.get('instrument_key')==r['instrument_key']]
        if len(matches)!=1 or matches[0]!=r['identity']:raise ValueError('Instrument identity mismatch')
        if r['listing_date']>r['from_date'] or (r['unit'],r['interval'])!=('days',1):raise ValueError('Ineligible request')
        q=DailyRequest(r['instrument_key'],'days',1,date.fromisoformat(r['from_date']),date.fromisoformat(r['to_date']))
        if url_for(q)!=r['url']:raise ValueError('URL mismatch')
        requests.append((r,q))
    if len(requests)!=8:raise ValueError('Exactly eight requests required')
    state=dict(scope_sha256=APPROVED,owner_approval_reference='Explicit approval in SS-INDEX-02 chat on 2026-11-04',
        automatic_retries=0,requests=[],complete=False,failure=None)
    ledger=OUT/'retrieval-execution.json'
    if '--sandbox-recovery' in sys.argv:
        prior=json.loads(ledger.read_text())
        if prior['complete'] or prior['failure']['kind']!='OSError' or len(prior['requests'])!=1 or 'http_status' in prior['requests'][0]:
            raise PermissionError('Only explicit recovery of first sandbox transport failure is allowed')
        ledger.rename(OUT/'sandbox-transport-failure.json')
        state['sandbox_recovery']='Explicit permission escalation; original transport attempt retained; no provider-response retries'
    with ledger.open('x') as f:json.dump(state,f,indent=2)
    provider=OUT/'evidence/provider';provider.mkdir(parents=True,exist_ok=True)
    try:
        from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
        token=secure_supplier()
        if not isinstance(token,str) or len(token)<20 or any(c in token for c in '\r\n"\\'):raise ValueError('Invalid credential handoff')
        for n,(r,q) in enumerate(requests,1):
            if n>1:time.sleep(1)
            record=dict(number=n,symbol=r['symbol'],instrument_key=q.instrument_key,unit='days',interval=1,
                from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),attempted_at=datetime.now(timezone.utc).isoformat())
            state['requests'].append(record);write_json(ledger,state)
            config='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
            result=subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent','--proto','=https','--max-redirs','0',
                '--retry','0','--max-time','30','--request','GET','--write-out','\n%{http_code}',url_for(q)],
                input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            if result.returncode:raise OSError('Transport failure')
            raw,status=result.stdout.rsplit(b'\n',1);status=int(status)
            if token.encode() in raw:raise ValueError('Unsafe provider echo')
            sha=hashlib.sha256(raw).hexdigest();dest=provider/f'{n}-{sha}.json'
            record.update(http_status=status,response_sha256=sha,received_at=datetime.now(timezone.utc).isoformat())
            batch=parse_response(raw,q,http_status=status,retrieved_at=datetime.fromisoformat(record['received_at']),
                raw_response_ref=str(dest.relative_to(ROOT)),instrument_metadata_ref=plan['metadata_path'],instrument_metadata_verified=True)
            permitted={'calendar_unresolved:gaps_and_session_completeness_unknown','provider_descending_order:normalize_explicitly_before_analysis'}
            if not batch.candles or any(i not in permitted for i in batch.issues):raise ValueError('Unexpected candle quality issue')
            with dest.open('xb') as f:f.write(raw)
            record.update(retained_path=str(dest.relative_to(ROOT)),rows=len(batch.candles),issues=batch.issues);write_json(ledger,state)
            print(json.dumps(dict(number=n,symbol=r['symbol'],rows=len(batch.candles),http_status=status)),flush=True)
        state['complete']=True
    except Exception as error:
        state['failure']=dict(kind=type(error).__name__,detail='STOPPED_NO_RETRY; exception text and error bodies not exported')
    write_json(ledger,state)
    if not state['complete']:raise SystemExit('Acquisition stopped; inspect sanitized ledger; no automatic retry')

if __name__=='__main__':main()
