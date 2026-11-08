"""Bounded, cached official BSE business metadata for EXISTING canonical companies.

No new universe, trading action, price candles, credentials or production access.
Stops on sustained refusal. At most four public requests per second, four in flight.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import threading
import time
import urllib.request
import certifi

from ..subsector_ss01.importer import readonly,TARGET
from .prepare_inputs import OUT


def main():
    dest=OUT/'evidence/bse-business-metadata';dest.mkdir(exist_ok=True)
    with readonly(TARGET) as c:
        rows=c.execute("SELECT company_id,identifier_value FROM security_identifiers WHERE identifier_type='BSE' ORDER BY company_id").fetchall()
    tasks=sorted({str(code) for cid,code in rows if str(code).isdigit()})
    context=ssl.create_default_context(cafile=certifi.where())
    lock=threading.Lock();next_request=[0.0];refusals=[0];stop=threading.Event()
    def fetch(code):
        path=dest/(code+'.json')
        if path.exists():return 'cached'
        if stop.is_set():return 'stopped_after_refusal'
        with lock:
            delay=max(0,next_request[0]-time.monotonic());next_request[0]=max(time.monotonic(),next_request[0])+0.25
        if delay:time.sleep(delay)
        if stop.is_set():return 'stopped_after_refusal'
        url='https://api.bseindia.com/BseIndiaAPI/api/ComHeadernew/w?scripcode='+code
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.bseindia.com/','Origin':'https://www.bseindia.com','Accept':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=25,context=context) as response:
                data=response.read();parsed=json.loads(data)
            if str(parsed.get('SecurityCode'))!=code:raise ValueError('BSE response identity mismatch')
            result=dict(url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),raw_response_sha256=hashlib.sha256(data).hexdigest(),response=parsed)
            path.write_text(json.dumps(result,ensure_ascii=False)+'\n')
            with lock:refusals[0]=0
            return 'fetched'
        except Exception as exc:
            with lock:
                refusals[0]+=1
                if refusals[0]>=8:stop.set()
            (dest/(code+'.error.json')).write_text(json.dumps(dict(url=url,error_type=type(exc).__name__,error=str(exc)))+'\n')
            return 'error'
    counts={};completed=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(fetch,code) for code in tasks]):
            result=future.result();counts[result]=counts.get(result,0)+1;completed+=1
            if completed%100==0:print(json.dumps(dict(completed=completed,total=len(tasks),states=counts)),flush=True)
    (OUT/'business-metadata-fetch-report.json').write_text(json.dumps(dict(total_existing_bse_identifiers=len(tasks),states=counts),indent=2)+'\n')
    print(json.dumps(dict(complete=True,total=len(tasks),states=counts)),flush=True)


if __name__=='__main__':main()
