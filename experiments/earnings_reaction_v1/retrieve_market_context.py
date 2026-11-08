"""Bounded public-domain acquisition. No credentials, cookies, redirects or retries.

Current-vintage FRED CSVs are retrospective evidence only. Dated H10 releases
provide unrevised historical vintages; their noon observation and weekly release
times must remain distinct. Re-running reuses byte-hashed retained responses.
"""
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import json
import subprocess
import time
from .er03 import digest, write_json
from .er04 import OUT as ER04

OUT = ER04/'market-context'

def plan():
    rows=[]
    for series in ('DCOILBRENTEU','DCOILWTICO','DEXINUS'):
        rows.append(dict(id=series, url=f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=2026-02-01&coed=2026-10-30',file=series+'.csv'))
    d=date(2026,2,23)
    while d<=date(2026,11,2):
        # Federal holidays on Monday; actual following-business-day release.
        actual=d+timedelta(days=1) if d in {date(2026,5,25),date(2026,9,7),date(2026,10,12)} else d
        rows.append(dict(id='H10-'+actual.isoformat(),url=f'https://www.federalreserve.gov/releases/h10/{actual:%Y%m%d}/',file=f'H10-{actual}.html'))
        d+=timedelta(days=7)
    rows += [dict(id='INDIA_VIX',url='https://www.nseindia.com/api/historical/vixhistory?from=01-02-2026&to=30-10-2026',file='INDIA_VIX.json'),
             dict(id='NIFTY50_GAP',url='https://www.nseindia.com/api/historical/indicesHistory?indexType=NIFTY%2050&from=15-08-2026&to=30-10-2026',file='NIFTY50_GAP.json')]
    return rows

def main():
    raw=OUT/'raw'; raw.mkdir(parents=True,exist_ok=True)
    p=plan()
    write_json(OUT/'public-acquisition-manifest.json',dict(requests=p,authentication=False,automatic_retries=0,
        terms='EIA and Federal Reserve public-domain statistics, citation requested. No proprietary US index histories requested.',
        source_notes=['https://fred.stlouisfed.org/series/DCOILBRENTEU','https://fred.stlouisfed.org/series/DEXINUS','https://www.federalreserve.gov/releases/h10/about.htm']))
    ledger=OUT/'public-acquisition-ledger.json'
    state=json.loads(ledger.read_text()) if ledger.exists() else dict(requests=[])
    indexed={r['id']:r for r in state['requests']}
    for r in p:
        dest=raw/r['file']
        if r['id'] in indexed:
            old=indexed[r['id']]
            if old.get('success'):
                if digest(dest)!=old['sha256']:raise ValueError('Retained bytes changed')
            if not old.get('transport_failure') or '--resume-sandbox-transport' not in __import__('sys').argv:
                continue
            record=dict(r,attempted_at=datetime.now(timezone.utc).isoformat(),success=False,sandbox_transport_resumed=True)
        else:
            record=dict(r,attempted_at=datetime.now(timezone.utc).isoformat(),success=False)
        state['requests'].append(record)
        write_json(ledger,state)
        result=subprocess.run(['/usr/bin/curl','--disable','--silent','--show-error','--proto','=https','--max-redirs','0','--retry','0','--max-time','30','--write-out','\n%{http_code}',r['url']],capture_output=True)
        if result.returncode:
            record.update(transport_failure=True);write_json(ledger,state)
            raise SystemExit('Transport failed; no retry. Request sandbox escalation if applicable.')
        body,status=result.stdout.rsplit(b'\n',1)
        record.update(http_status=int(status),received_at=datetime.now(timezone.utc).isoformat())
        if int(status)==200 and (b'observation_date,' in body or b'DATE,' in body or b'Foreign Exchange Rates' in body or (r['id'] in ('INDIA_VIX','NIFTY50_GAP') and body.lstrip().startswith(b'{'))):
            dest.write_bytes(body)
            record.update(success=True,sha256=digest(dest),bytes=len(body))
        else:
            record['missing_reason']='HTTP_OR_SCHEMA_FAILURE;NO_BYPASS_NO_RETRY'
        write_json(ledger,state)
        print(json.dumps({k:record[k] for k in ('id','http_status','success')}),flush=True)
        time.sleep(.25)

if __name__=='__main__':main()
