"""Parse/hash the two saved company responses; no DB, network or ingestion."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,'/app')
from scrapper.sources.screener.company import parse_company_page
from scrapper.core.collection_scope import parsed_cap


after=json.loads(Path('/verification/after.json').read_text())
result=[]
for fetch in after['new_fetches']:
    if fetch['resource_kind']!='company_page':continue
    raw=Path(fetch['local_path']).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=fetch['content_sha256']:raise RuntimeError('Saved company response changed')
    parsed=parse_company_page(raw,fetch['fetched_at_utc'])
    quarters=sorted({f.period_end for f in parsed.financials if f.section_code=='quarters' and f.period_end})
    result.append({'company_id':fetch['company_id'],'fetch_id':fetch['fetch_id'],
        'requested_url':fetch['requested_url'],'final_url':fetch['final_url'],
        'nse_symbol':parsed.nse_symbol,'bse_code':parsed.bse_code,'source_company_id':parsed.source_company_id,
        'reporting_scope':parsed.reporting_scope,'market_cap_crore':str(parsed_cap(parsed)),
        'quarter_periods':quarters,'september_financial_rows':sum(f.section_code=='quarters' and f.period_end=='2026-09-30' for f in parsed.financials),
        'saved_response_sha256':digest,'source_hash_verified':True,'raw_bytes':len(raw),
        'financial_persistence':False,'source_requests':0})
print(json.dumps({'companies':result,'mode':'offline_parse_saved_source','source_requests':0}),flush=True)
