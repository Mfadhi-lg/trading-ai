"""Independent SS-02 coverage/export checks against the preserved SS-01 database."""
import collections
import csv
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import zipfile
from ..subsector_ss01.importer import TARGET,digest
from .prepare_inputs import OUT
from .inventory import write_csv


def rows(c,sql):return [dict(r) for r in c.execute(sql)]


def main():
    c=sqlite3.connect(f'file:{TARGET}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    old=sqlite3.connect(f"file:{OUT/'ss01-v2-before.sqlite3'}?mode=ro",uri=True)
    preserved={}
    for (t,) in old.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        expected=old.execute(f'SELECT * FROM "{t}" ORDER BY 1,2').fetchall()
        actual=[tuple(r) for r in c.execute(f'SELECT * FROM "{t}" ORDER BY 1,2')]
        assert actual==expected,t
        preserved[t]=len(expected)
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('SELECT count(*) FROM companies').fetchone()[0]==c.execute('SELECT count(*) FROM ss02_company_mapping').fetchone()[0]
    assert c.execute('SELECT count(*) FROM ss02_identity_overlay').fetchone()[0]==c.execute('SELECT count(*) FROM ss_rows').fetchone()[0]
    profiles={r['company_id']:r for r in json.loads((OUT/'evidence/existing-business-profiles.json').read_text())}
    for r in c.execute('SELECT * FROM ss02_business_evidence'):
        assert r['description_raw']==profiles[r['company_id']]['display_text']
        assert r['evidence_sha256']==digest(profiles[r['company_id']])
    for r in c.execute('SELECT * FROM ss02_company_mapping'):
        exposures=c.execute('SELECT code,role FROM ss02_business_exposures WHERE company_id=?',(r['company_id'],)).fetchall()
        primaries=[e['code'] for e in exposures if e['role']=='primary']
        assert primaries==([r['primary_code']] if r['primary_code'] else [])
        if r['status']=='insufficient_granular_business_evidence':assert r['primary_code'] is None
    # Independently roundtrip each selected member to the original ZIP row.
    member_rows=rows(c,'''SELECT m.*,b.code,b.quarter,b.cutoff_at FROM ss02_basket_members m JOIN ss02_baskets b USING(basket_id)''')
    for r in member_rows:
        e=json.loads(r['selection_evidence_json'])
        with zipfile.ZipFile(OUT/'evidence'/e['source_file']) as z:
            original=list(csv.DictReader(z.read(e['member']).decode('utf-8-sig').splitlines()[2:]))
            source=next(x for x in original if x['Security Symbol']==e['symbol'])
        assert float(source['Index Market Capitalisation (Rs. Crores)'])==e['historical_index_mcap_crore']
        assert float(source['Avg. Impact Cost (%)'])==e['historical_impact_cost_percent']
        assert e['observation_date']<=r['cutoff_at'][:10]
        assert datetime.fromisoformat(e['available_at'])<=datetime.fromisoformat(r['cutoff_at'])
        assert e['quote_evidence'] is not None
    assert c.execute('SELECT count(*) FROM ss02_baskets WHERE chart_ready!=0').fetchone()[0]==0
    taxonomy_rows=rows(c,'SELECT code,label,origin,is_granular FROM ss02_taxonomy ORDER BY code')
    write_csv(OUT/'granular-taxonomy.csv',taxonomy_rows)
    write_csv(OUT/'original-tutor-labels.csv',rows(c,'SELECT * FROM ss_subsectors ORDER BY label_raw'))
    write_csv(OUT/'tutor-to-granular-crosswalk.csv',rows(c,'SELECT * FROM ss02_tutor_crosswalk ORDER BY label_raw,code'))
    company_rows=rows(c,'SELECT * FROM ss02_company_subsectors ORDER BY company_id')
    write_csv(OUT/'company-subsector-mappings.csv',company_rows)
    write_csv(OUT/'unresolved-company-review.csv',[r for r in company_rows if r['primary_code'] is None])
    write_csv(OUT/'diversified-company-review.csv',[r for r in company_rows if 'diversified' in r['status'] or 'conflict' in r['status']])
    write_csv(OUT/'proposed-classifications-review.csv',[r for r in company_rows if r['status']=='evidence_supported_proposal'])
    write_csv(OUT/'subsector-constituents.csv',rows(c,'''SELECT e.company_id,c.current_legal_name,e.code,t.label,e.role,e.evidence_json
       FROM ss02_business_exposures e JOIN companies c USING(company_id) JOIN ss02_taxonomy t USING(code) ORDER BY e.code,e.company_id'''))
    write_csv(OUT/'existing-security-subsector-mappings.csv',rows(c,'SELECT * FROM ss02_security_mapping ORDER BY isin'))
    write_csv(OUT/'historical-basket-manifests.csv',rows(c,'SELECT * FROM ss02_baskets ORDER BY code,quarter'))
    write_csv(OUT/'historical-basket-members.csv',member_rows)
    write_csv(OUT/'historical-basket-candidate-review.csv',rows(c,'SELECT * FROM ss02_basket_candidate_review ORDER BY basket_id,company_id'))
    q2=rows(c,'''SELECT q.*,e.display_stock_raw FROM ss02_q2_mapping q JOIN ss_focus_entries e USING(entry_id) ORDER BY e.display_stock_raw''')
    write_csv(OUT/'q2-subsector-reconciliation.csv',q2)
    write_csv(OUT/'q2-unresolved-identities.csv',[r for r in q2 if r['company_id'] is None])
    discrepancy=rows(c,"SELECT * FROM ss02_identity_overlay WHERE evidence_json LIKE '%source_name_discrepancy_requires_review%'")
    write_csv(OUT/'q2-source-name-discrepancy-review.csv',discrepancy)
    quarter=rows(c,'''SELECT s.quarter,count(*) original_rows,count(DISTINCT i.company_id) resolved_unique_companies,
        sum(i.company_id IS NOT NULL) resolved_rows FROM ss_rows r JOIN ss_sources s USING(source_id)
        JOIN ss02_identity_overlay i USING(row_id) GROUP BY s.quarter ORDER BY s.quarter''')
    states=dict(c.execute('SELECT status,count(*) FROM ss02_company_mapping GROUP BY status').fetchall())
    scopes=list(csv.DictReader((OUT/'existing-collection-scope-inventory.csv').open()))
    major_ids={int(r['company_id']) for r in scopes if r['company_id'] and r['eligibility']=='eligible'}
    major_states=collections.Counter(r['status'] for r in company_rows if r['company_id'] in major_ids)
    counts={t:c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    current_sources=c.execute("SELECT decision_json FROM ss02_company_mapping").fetchall()
    official_coverage=sum(bool(json.loads(r[0]).get('official_business_evidence')) for r in current_sources)
    result=dict(canonical_companies=len(company_rows),company_mapping_states=states,
        granular_primary_mappings=sum(r['primary_code'] is not None for r in company_rows),
        verified_primary_mappings=sum(r['status'].startswith('verified_') for r in company_rows),
        researched_proposals=states.get('evidence_supported_proposal',0),official_business_source_coverage=official_coverage,
        original_tutor_labels=c.execute('SELECT count(*) FROM ss_subsectors').fetchone()[0],
        granular_taxonomy_proposals=len(taxonomy_rows),granular_codes_with_company_exposures=c.execute('SELECT count(DISTINCT code) FROM ss02_business_exposures').fetchone()[0],
        original_major_scope_unique_canonical_companies=len(major_ids),major_scope_mapping_states=dict(major_states),
        quarters=quarter,q2_entries=len(q2),q2_canonical_matches=sum(r['company_id'] is not None for r in q2),
        q2_primary_granular_mappings=sum(r['primary_code'] is not None for r in q2),q2_unresolved_identities=[r['display_stock_raw'] for r in q2 if r['company_id'] is None],
        current_exchange_isins=counts['ss02_security_mapping'],
        security_isins_requiring_canonical_link=c.execute('SELECT count(*) FROM ss02_security_mapping WHERE company_id IS NULL').fetchone()[0],
        source_name_discrepancy_reviews=len(discrepancy),
        historical_basket_manifests=counts['ss02_baskets'],metadata_qualified_baskets=c.execute("SELECT count(*) FROM ss02_baskets WHERE status='FROZEN_METADATA_QUALIFIED_RECONSTRUCTION'").fetchone()[0],
        historical_constituent_rows=len(member_rows),table_counts=counts,ss01_tables_preserved=preserved,
        independent_validation='passed',full_universe_granular_assignment_complete=False,
        incomplete_reason='Broad or insufficient business evidence and genuine diversified/source conflicts remain explicitly unresolved; proposals require review.')
    (OUT/'coverage-and-integrity.json').write_text(json.dumps(result,indent=2)+'\n')
    write_csv(OUT/'final-table-counts.csv',[dict(table=k,rows=v) for k,v in counts.items()])
    old.close();c.close()
    print(json.dumps({k:v for k,v in result.items() if k not in ('table_counts','ss01_tables_preserved')}))


if __name__=='__main__':main()
