"""Execute only TECH-05's five explicitly approved historical requests."""
from datetime import date,datetime,timezone
import gzip,hashlib,json,subprocess,time
from .prepare import ROOT,OUT
from experiments.earnings_reaction_v1.er03 import digest,write_json
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import url_for,parse_response

APPROVED='ebd126d358655366584f51c0c760bd11d8d5b8c18df2bae1cf74bc788236f9a1'
def main():
    path=OUT/'ACQUISITION_PROPOSAL.json'
    if digest(path)!=APPROVED:raise PermissionError('Exact approved scope mismatch')
    plan=json.loads(path.read_text());master=[]
    for p,h in plan['metadata'].items():
        if digest(ROOT/p)!=h:raise ValueError('Metadata changed')
        master+=json.loads(gzip.decompress((ROOT/p).read_bytes()))
    if len(plan['requests'])!=5:raise PermissionError('Exactly five requests required')
    for r in plan['requests']:
        matches=[m for m in master if m['instrument_key']==r['instrument_key']]
        if len(matches)!=1 or matches[0]!=r['identity']:raise ValueError('Identity mismatch')
    ledger=OUT/'retrieval-execution.json';state=dict(scope_sha256=APPROVED,owner_approval='Explicit TECH-05 five-request approval in chat',requests=[],complete=False,automatic_retries=0)
    with ledger.open('x') as f:json.dump(state,f,indent=2)
    dest=OUT/'raw';dest.mkdir(exist_ok=True)
    try:
        from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
        token=secure_supplier()
        if any(c in token for c in '\r\n"\\'):raise ValueError('Unsafe credential handoff')
        for i,r in enumerate(plan['requests']):
            if i:time.sleep(1)
            q=DailyRequest(r['instrument_key'],'days',1,date.fromisoformat(r['from_date']),date.fromisoformat(r['to_date']))
            if url_for(q)!=r['url']:raise ValueError('URL differs')
            rec=dict(number=i+1,series=r['series'],instrument_key=q.instrument_key,url=url_for(q),attempted_at=datetime.now(timezone.utc).isoformat());state['requests'].append(rec);write_json(ledger,state)
            cfg='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
            result=subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent','--proto','=https','--max-redirs','0','--retry','0','--max-time','30','--request','GET','--write-out','\n%{http_code}',url_for(q)],input=cfg.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            if result.returncode:raise OSError('Transport failed')
            raw,status=result.stdout.rsplit(b'\n',1);status=int(status)
            if token.encode() in raw:raise ValueError('Unsafe echo')
            sha=hashlib.sha256(raw).hexdigest();target=dest/(r['series']+'-'+sha+'.json');now=datetime.now(timezone.utc)
            rec.update(http_status=status,response_sha256=sha,received_at=now.isoformat());write_json(ledger,state)
            batch=parse_response(raw,q,http_status=status,retrieved_at=now,raw_response_ref=str(target.relative_to(ROOT)),instrument_metadata_ref='ACQUISITION_PROPOSAL.json:identity',instrument_metadata_verified=True)
            allowed={'calendar_unresolved:gaps_and_session_completeness_unknown','provider_descending_order:normalize_explicitly_before_analysis'}
            if not batch.candles or any(v not in allowed for v in batch.issues):raise ValueError('Candle quality failed')
            with target.open('xb') as f:f.write(raw)
            rec.update(retained_path=str(target.relative_to(ROOT)),rows=len(batch.candles),issues=batch.issues);write_json(ledger,state)
            print(json.dumps(dict(series=r['series'],rows=len(batch.candles),http_status=status)),flush=True)
        state['complete']=True
    except Exception as e:state['failure']=dict(kind=type(e).__name__,detail='STOP_NO_RETRY; no sensitive exception or error body exported')
    write_json(ledger,state)
    if not state['complete']:raise SystemExit('Acquisition stopped; no automatic retry')

if __name__=='__main__':main()
