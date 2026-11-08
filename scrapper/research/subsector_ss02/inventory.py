"""Inventory existing records and freeze documented business evidence for all companies."""
import csv
import json
from pathlib import Path
from ..subsector_ss01.importer import readonly, MASTER, digest
from .prepare_inputs import OUT


def write_csv(path,rows):
    if not rows:return
    with Path(path).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def main():
    with readonly(MASTER) as c:
        c.row_factory=__import__('sqlite3').Row
        companies=[dict(r) for r in c.execute('SELECT * FROM companies ORDER BY company_id')]
        about=c.execute('''WITH ranked AS (
            SELECT f.company_id,t.company_text_observation_id,t.parse_id,t.display_text,t.available_at_utc,
                t.source_published_at_utc,b.content_sha256,b.local_path,f.final_url,
                row_number() OVER(PARTITION BY f.company_id ORDER BY t.available_at_utc DESC,t.company_text_observation_id DESC) rn
            FROM company_text_observations t JOIN parse_artifacts p USING(parse_id)
            JOIN response_blobs b USING(blob_id) JOIN fetch_observations f ON f.blob_id=p.blob_id
            WHERE t.text_type='about' AND f.company_id IS NOT NULL)
            SELECT * FROM ranked WHERE rn=1 ORDER BY company_id''').fetchall()
        sources={}
        websites={}
        for r in c.execute("SELECT parse_id,source_url FROM company_profile_links WHERE link_type='about_source' ORDER BY company_profile_link_id"):
            sources.setdefault(r['parse_id'],[]).append(r['source_url'])
        for r in c.execute("SELECT parse_id,source_url FROM company_profile_links WHERE link_type='website' ORDER BY company_profile_link_id"):
            websites.setdefault(r['parse_id'],[]).append(r['source_url'])
        profiles=[]
        for r in about:
            d=dict(r);d.pop('rn')
            d['primary_citation_urls']=list(dict.fromkeys(sources.get(r['parse_id'],[])))
            d['company_website_urls']=list(dict.fromkeys(websites.get(r['parse_id'],[])))
            d['evidence_authority']='retained Screener business description; cited primary links not automatically verified'
            profiles.append(d)
        inventory={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                   ['companies','source_identifiers','security_identifiers','exchange_securities','exchange_company_registry','screener_collection_scope','screener_scope_aliases']}
        inventory.update(canonical_scope='Every existing company_id; no new stock universe is constructed',
            business_profiles=len(profiles),unique_exchange_isins=c.execute('SELECT count(DISTINCT current_isin) FROM exchange_securities').fetchone()[0],
            archived_master_path=str(MASTER),raw_exchange_rows_are_not_unique_companies=True,
            stock_lists=['OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA/stocks.csv',
                         'OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA/screener_focus_import.xlsx',
                         'OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA/screener-in-2026-08-03.csv',
                         'artifacts/b2-2026-10-01/Q1 2027 FOCUS LIST.txt',
                         '/Users/mohamedfadhi/Downloads/2026 q4.csv',
                         '/Users/mohamedfadhi/Downloads/2026_Q2_Results_Focus_List_100_Stocks.csv'])
        scopes=[dict(r) for r in c.execute('SELECT company_path,company_id,eligibility,observed_at_utc FROM screener_collection_scope ORDER BY company_path')]
        registry=[dict(r) for r in c.execute('SELECT registry_company_id,isin,canonical_name_raw,company_id,first_observed_date,last_observed_date FROM exchange_company_registry ORDER BY registry_company_id')]
        inventory['scope_eligibility_counts']={r[0]:r[1] for r in c.execute('SELECT eligibility,count(*) FROM screener_collection_scope GROUP BY 1')}
    write_csv(OUT/'existing-company-inventory.csv',companies)
    write_csv(OUT/'existing-collection-scope-inventory.csv',scopes)
    write_csv(OUT/'existing-exchange-registry-inventory.csv',registry)
    (OUT/'evidence/existing-business-profiles.json').write_text(json.dumps(profiles,indent=2,ensure_ascii=False)+'\n')
    inventory['business_evidence_logical_sha256']=digest(profiles)
    (OUT/'existing-universe-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    registry=json.loads((OUT/'evidence/official-identity-registry.json').read_text())['rows']
    write_csv(OUT/'existing-exchange-security-inventory.csv',[dict(r,canonical_name_matches=json.dumps(r['canonical_name_matches'])) for r in registry])
    print(json.dumps(inventory))


if __name__=='__main__':main()
