"""Fetch only allowlisted public classification/capitalisation/liquidity metadata."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request
import ssl
from urllib.parse import urlparse

OUT=Path(__file__).resolve().parents[3]/'artifacts/subsector-ss02/evidence'
HOSTS={'www.amfiindia.com','portal.amfiindia.com','www.niftyindices.com','api.bseindia.com',
       'nsearchives.nseindia.com','archives.nseindia.com','www.nseindia.com'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('url');parser.add_argument('filename')
    args=parser.parse_args()
    if urlparse(args.url).hostname not in HOSTS or '/' in args.filename or '..' in args.filename:
        raise ValueError('Only named public metadata evidence on allowlisted official hosts')
    if any(word in args.url.lower() for word in ['bhavcopy','bhav','chart','historicalprice','securitywiseprice']):
        raise ValueError('Candle/price downloads are outside SS-02 scope')
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/args.filename
    if path.exists():
        print(json.dumps({'cached':str(path)}));return
    headers={'User-Agent':'Mozilla/5.0'}
    if urlparse(args.url).hostname=='api.bseindia.com':
        headers.update({'Referer':'https://www.bseindia.com/','Origin':'https://www.bseindia.com','Accept':'application/json'})
    request=urllib.request.Request(args.url,headers=headers)
    import certifi
    with urllib.request.urlopen(request,timeout=45,context=ssl.create_default_context(cafile=certifi.where())) as response:
        data=response.read()
        metadata=dict(url=args.url,final_url=response.url,headers=dict(response.headers),
                      retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
    path.write_bytes(data)
    path.with_suffix(path.suffix+'.metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(dict(path=str(path),bytes=len(data),sha256=metadata['sha256'])))


if __name__=='__main__':
    main()
