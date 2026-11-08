"""Freeze local official exchange identities and metadata, never acquisition writes."""
import json
from pathlib import Path
import re
import sqlite3

from ..subsector_ss01.importer import digest, readonly, TARGET, MASTER

OUT=Path(__file__).resolve().parents[3]/'artifacts/subsector-ss02'


def key(name):
    text=re.sub(r'\s+(limited|ltd)\.?\s*$','',name,flags=re.I)
    return ''.join(c for c in text.casefold().replace('&','and') if c.isalnum())


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    backup=OUT/'ss01-v2-before.sqlite3'
    with readonly(TARGET) as c:
        names={}
        for cid,name in c.execute('SELECT company_id,current_legal_name FROM companies'):
            names.setdefault(key(name),[]).append(cid)
        if not backup.exists():
            dest=sqlite3.connect(backup);c.backup(dest);dest.close()
    with readonly(MASTER) as c:
        securities=[]
        for row in c.execute('''SELECT s.exchange_security_id,s.exchange,s.segment,s.nse_symbol,s.bse_scrip_code,
           s.current_name_raw,s.current_isin,s.verification_status,v.version_id,v.listing_date,
           v.valid_from_date,v.valid_to_date,v.attributes_json,r.company_id
           FROM exchange_securities s JOIN exchange_security_versions v ON v.version_id=s.current_version_id
           LEFT JOIN exchange_company_registry r ON r.isin=s.current_isin
           ORDER BY s.exchange_security_id'''):
            sid,exchange,segment,symbol,bse,name,isin,verified,vid,listed,start,end,attrs,registry_cid=row
            symbol=symbol if exchange=='NSE' else json.loads(attrs).get('security_id') or bse
            securities.append(dict(security_id=sid,exchange=exchange,segment=segment,symbol=symbol,bse_code=bse,
                name=name,isin=isin,verification=verified,version_id=vid,listing_date=listed,
                version_valid_from=start,version_valid_to=end,canonical_name_matches=names.get(key(name),[]),
                canonical_identity_candidates=sorted(set(names.get(key(name),[]))|({registry_cid} if registry_cid is not None else set())),
                existing_registry_company_id=registry_cid))
    data=dict(archived_master_path=str(MASTER),authority='official exchange master snapshot; identity only, not historical basket ranking',
              rows=securities)
    (OUT/'evidence/official-identity-registry.json').write_text(json.dumps(data,indent=2)+'\n')
    (OUT/'evidence/official-identity-registry.sha256').write_text(digest(data)+'\n')
    print(json.dumps(dict(exchange_security_rows=len(securities),backup=str(backup))))


if __name__=='__main__':
    main()
