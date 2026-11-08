"""Read frozen Focus membership; never infer new membership from old quarters."""
import hashlib
import json
from pathlib import Path


def membership(path, period):
    path=Path(path)
    if not path.is_file():
        return {'company_ids':[],'state':'registry unavailable','period':period}
    payload=path.read_bytes();data=json.loads(payload)
    year,month=map(int,period[:7].split('-'))
    fy=year+(month!=3);q={6:1,9:2,12:3,3:4}.get(month)
    expected=f'FY{fy}-Q{q}'
    ids=sorted({r['company_id'] for r in data.get('rows',[])
        if r.get('quarter')==expected and r.get('label')==1
        and type(r.get('company_id')) is int and r['company_id']>0
        and r.get('identity_resolution_state')=='RESOLVED'})
    return {'company_ids':ids,'state':'resolved membership' if ids else 'no resolved membership for this quarter',
            'period':period,'registry_quarter':expected,'registry_sha256':hashlib.sha256(payload).hexdigest()}
