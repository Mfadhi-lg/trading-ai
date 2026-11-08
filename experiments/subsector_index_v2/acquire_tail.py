"""Separately approved one-request Nifty50 tail; existing secure supplier/parser."""
from datetime import date,datetime,timezone
import hashlib,json,subprocess
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import parse_response,url_for
from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
from experiments.earnings_reaction_v1.er03 import ROOT,digest,write_json
from .prepare import OUT

def main():
    path=OUT/'REMAINING_ACQUISITION_MANIFEST.json'
    if digest(path)!='dc07c957443e2a6f526e3dbfc0fbf185d2fcc9094d351be9540b9cc64fb8788e':raise PermissionError('Exact separate approval required')
    r=json.loads(path.read_text())['requests'][0]
    q=DailyRequest(r['instrument_key'],'days',1,date.fromisoformat(r['from_date']),date.fromisoformat(r['to_date']))
    if url_for(q)!=r['url'] or q.instrument_key!='NSE_INDEX|Nifty 50':raise ValueError('Identity mismatch')
    state=dict(manifest_sha256=digest(path),owner_approval_reference='Separate explicit SS-INDEX-02 Nifty50 tail approval in chat',complete=False,automatic_retries=0,request=r)
    ledger=OUT/'nifty-tail-execution.json'
    with ledger.open('x') as f:json.dump(state,f,indent=2)
    try:
        token=secure_supplier()
        if any(c in token for c in '\r\n"\\'):raise ValueError('Invalid credential handoff')
        config='header = "Accept: application/json"\nheader = "Authorization: Bearer '+token+'"\n'
        result=subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent','--proto','=https','--max-redirs','0','--retry','0','--max-time','30','--request','GET','--write-out','\n%{http_code}',url_for(q)],input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode:raise OSError('Transport failure')
        raw,status=result.stdout.rsplit(b'\n',1);status=int(status)
        if token.encode() in raw:raise ValueError('Unsafe provider echo')
        sha=hashlib.sha256(raw).hexdigest();dest=OUT/'evidence/provider'/('nifty50-'+sha+'.json')
        batch=parse_response(raw,q,http_status=status,retrieved_at=datetime.now(timezone.utc),raw_response_ref=str(dest.relative_to(ROOT)),instrument_metadata_ref='ER03 retained Nifty50 identity',instrument_metadata_verified=True)
        allowed={'calendar_unresolved:gaps_and_session_completeness_unknown','provider_descending_order:normalize_explicitly_before_analysis'}
        if not batch.candles or any(i not in allowed for i in batch.issues):raise ValueError('Quality check failed')
        with dest.open('xb') as f:f.write(raw)
        state.update(complete=True,http_status=status,rows=len(batch.candles),retained_path=str(dest.relative_to(ROOT)),response_sha256=sha)
    except Exception as error:state['failure']=dict(kind=type(error).__name__,detail='Stopped; no retry; error body and credential not exported')
    write_json(ledger,state)
    if not state['complete']:raise SystemExit('Tail acquisition stopped; no automatic retry')
    print(json.dumps(dict(rows=state['rows'],http_status=state['http_status'])))

if __name__=='__main__':main()
