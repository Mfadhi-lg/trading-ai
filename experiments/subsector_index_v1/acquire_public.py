"""Public official corporate-action cross-checks. No auth, cookies or retries.

This never retrieves candles or accesses Upstox credentials. Stop the NSE host
on its first failure; never turn an empty/inaccessible page into 'no actions'.
"""
from datetime import datetime,timezone
from pathlib import Path
import json
import subprocess
from experiments.earnings_reaction_v1.er03 import write_json,digest

OUT=Path(__file__).resolve().parents[2]/'artifacts/subsector-index-v1'
SYMBOLS=('CEATLTD','MRF','TOLINS','SYRMA','CENTUM','PGEL','TARIL','VOLTAMP','SHILCTECH')

def main():
    dest=OUT/'evidence/public';dest.mkdir(parents=True,exist_ok=True)
    requests=[dict(symbol=s,url=f'https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={s}&from_date=01-12-2025&to_date=30-10-2026') for s in SYMBOLS]
    write_json(OUT/'public-corporate-actions-manifest.json',dict(authenticated=False,requests=requests,automatic_retries=0,
        restrictions='Public official interface; no auth/cookie/waf bypass. Stop host on first failure.'))
    ledger=OUT/'public-corporate-actions-ledger.json'
    if ledger.exists():raise SystemExit('Exclusive one-shot public ledger exists; no retry')
    state=dict(started_at=datetime.now(timezone.utc).isoformat(),requests=[],complete=False)
    with ledger.open('x') as f:json.dump(state,f,indent=2)
    for r in requests:
        record=dict(r,attempted_at=datetime.now(timezone.utc).isoformat());state['requests'].append(record);write_json(ledger,state)
        result=subprocess.run(['/usr/bin/curl','--disable','--silent','--show-error','--proto','=https','--max-redirs','0','--retry','0','--max-time','15','--write-out','\n%{http_code}',r['url']],capture_output=True)
        if result.returncode:
            record.update(transport_failure=True,returncode=result.returncode);break
        raw,status=result.stdout.rsplit(b'\n',1);record['http_status']=int(status)
        try:
            data=json.loads(raw)
            if int(status)!=200 or not isinstance(data,list) or any(x.get('symbol')!=r['symbol'] for x in data):raise ValueError('Invalid response')
        except (ValueError,TypeError):record['failure']='HTTP_OR_SCHEMA;NO_RETRY';break
        path=dest/(r['symbol']+'-corporate-actions.json');path.write_bytes(raw)
        record.update(retained_path=str(path.relative_to(OUT.parents[1])),sha256=digest(path),rows=len(data),received_at=datetime.now(timezone.utc).isoformat())
        print(r['symbol'],len(data),'actions',flush=True)
    else:state['complete']=True
    state['finished_at']=datetime.now(timezone.utc).isoformat();write_json(ledger,state)
    print(json.dumps(state))

if __name__=='__main__':main()
