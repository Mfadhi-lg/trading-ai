"""Bounded public primary evidence retrieval, separate from pilot market history."""
import csv
import io
import json
import subprocess
import zipfile
from .storage import Archive,OUT,dump,sha,atomic
from .calendar import ANNUAL,SPECIAL,ADDENDA,BASE,build

def get(url):
    result=subprocess.run(['/usr/bin/curl','--disable','--http1.1','--silent','--proto','=https',
        '--max-time','20','--max-redirs','0','--write-out','\n%{http_code}',url],capture_output=True)
    raw,status=result.stdout.rsplit(b'\n',1)
    return raw,dict(url=url,http_status=int(status),transport_exit=result.returncode,sha256=sha(raw))

def main():
    a=Archive();receipts=[]
    # Provider security metadata is an overlay on canonical IDs, never a new master.
    for exchange in ['BSE']:
        url=f'https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz'
        raw,receipt=get(url)
        if receipt['http_status']==200 and not receipt['transport_exit']:
            import gzip
            records=json.loads(gzip.decompress(raw))
            path=OUT/f'public-{exchange.lower()}-instruments.json.gz'
            atomic(path,raw);receipt.update(records=len(records),retained_path=str(path))
        receipts.append(receipt)
    url='https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261030_F_0000.csv.zip'
    raw,receipt=get(url)
    if receipt['http_status']==200 and zipfile.is_zipfile(io.BytesIO(raw)):
        a.guard();p=a.root/'reference'/'nse-bhavcopy-20261030.zip';p.parent.mkdir(exist_ok=True)
        atomic(p,raw);receipt['passport_path']=str(p)
    receipts.append(receipt)
    refs=set(ANNUAL.values())|{r[1] for r in SPECIAL.values()}|set(ADDENDA.values())
    for ref in sorted(refs):
        path=OUT/'evidence'/(ref+'.pdf')
        if path.exists(): continue
        raw,receipt=get(BASE+ref+'.pdf')
        if receipt['http_status']==200 and raw.startswith(b'%PDF'):
            path.parent.mkdir(exist_ok=True);atomic(path,raw);receipt['retained_path']=str(path)
        receipts.append(receipt)
    build()
    dump(OUT/'primary-reference-receipts.json',receipts)
    print(json.dumps(dict(receipts=len(receipts),success=sum(r['http_status']==200 for r in receipts))),flush=True)

if __name__=='__main__': main()
