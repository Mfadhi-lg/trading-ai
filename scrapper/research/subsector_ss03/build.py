"""SS-03 conservative overlay and review exports; never writes to source database."""
import csv, hashlib, json, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'artifacts/subsector-ss01/v1/research.sqlite3'
OUT=ROOT/'artifacts/subsector-ss03'
ALIASES={'AUTO ANCILLIARY':'AUTO ANCILLARY','FERTILIZERS':'FERTILIZER','TEXTILES':'TEXTILE'}
BROAD={'AUTO','CHEMICALS','COMMODITY','CONSTRUCTION','CONSUMER DURABLES','DATA CENTER','DEFENCE','FINANCIALS','FMCG','FMCG OTHER','HEALTHCARE','INDUSTRIAL PRODUCTS','INFRA','IT','METALS','MINING','OTHERS','PHARMA','POWER','RAILWAY','WIRES','ALCOHOL','OIL EXPLORATION','POLYMENR','EMS','SHIP BUILDING'}
CUT={'FY2026-Q4':'2026-03-31','FY2027-Q1':'2026-06-30'}
def cid(s): return 'TUTOR-'+hashlib.sha256(s.encode()).hexdigest()[:16]
def migrate(c):
 c.executescript((Path(__file__).parent/'004_canonical.sql').read_text())
 for sid,label in c.execute('select subsector_id,label_raw from ss_subsectors order by label_raw').fetchall():
  canonical=ALIASES.get(label,label); status='AMBIGUOUS_REVIEW' if canonical in BROAD else 'TUTOR_ORIGINAL'
  c.execute('insert or ignore into ss03_canonical values (?,?,?)',(cid(canonical),canonical,status))
  c.execute('insert or ignore into ss03_alias values (?,?,?,?)',(sid,cid(canonical),'verified spelling/inflection equivalence' if label in ALIASES else 'original tutor label', 'APPROVED'))
 c.commit()
def rollback(c):
 c.executescript('DROP VIEW IF EXISTS ss03_historical_memberships; DROP TABLE IF EXISTS ss03_alias; DROP TABLE IF EXISTS ss03_canonical;')
def export(name, columns, rows):
 with (OUT/name).open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(columns);w.writerows(rows)
def run():
 OUT.mkdir(parents=True,exist_ok=True)
 # SQLite backup from read-only source gives a consistent isolated research copy in /tmp.
 src=sqlite3.connect(f'file:{SOURCE}?mode=ro',uri=True); db=Path('/tmp/subsector-ss03-isolated.sqlite3')
 c=sqlite3.connect(db);src.backup(c);src.close();c.execute('pragma foreign_keys=on');migrate(c)
 export('CANONICAL_SUBSECTORS.csv',['canonical_id','label','review_status','origin'],[(a,b,d,'TUTOR_ORIGINAL') for a,b,d in c.execute('select * from ss03_canonical order by label')])
 export('SUBSECTOR_ALIASES.csv',['original_subsector_id','original_label','canonical_id','canonical_label','reason','review_status'],c.execute('select s.subsector_id,s.label_raw,a.canonical_id,t.label,a.reason,a.review_status from ss_subsectors s join ss03_alias a using(subsector_id) join ss03_canonical t using(canonical_id) order by s.label_raw'))
 export('PRIORITY_SUBSECTORS.csv',['canonical_id','canonical_label','review_status','q4_companies','q1_companies'],c.execute("select t.canonical_id,t.label,t.review_status,count(distinct case when m.quarter='FY2026-Q4' then m.company_id end),count(distinct case when m.quarter='FY2027-Q1' then m.company_id end) from ss03_canonical t join ss03_historical_memberships m using(canonical_id) group by t.canonical_id order by t.label"))
 q=[]
 for entry,name,company,code,state in c.execute('select f.entry_id,f.display_stock_raw,m.company_id,m.primary_code,m.state from ss_focus_entries f join ss02_q2_mapping m using(entry_id) order by f.display_stock_raw').fetchall():
  rows=c.execute('select distinct t.label,a.canonical_id from ss_rows r join ss_sources s using(source_id) left join ss02_identity_overlay i using(row_id) join ss_subsectors x on x.label_raw=r.label_raw join ss03_alias a using(subsector_id) join ss03_canonical t using(canonical_id) where s.quarter in (?,?) and coalesce(i.company_id,r.company_id)=? order by t.label',('FY2026-Q4','FY2027-Q1',company)).fetchall() if company else []
  status='IDENTITY_UNRESOLVED' if not company else ('NO_TUTOR_HISTORY' if not rows else ('TUTOR_REFERENCE_UNAMBIGUOUS' if len(rows)==1 else 'TUTOR_CONFLICT_REVIEW'))
  assessments=c.execute('select m.row_id,m.original_row_number,m.q2_results_raw,m.rating_raw,m.guidance_raw,m.extraction_note_raw,m.decision_at,m.available_at from ss_mentor_assessments m join ss_focus_entry_rows e using(row_id) where e.entry_id=? order by m.original_row_number',(entry,)).fetchall()
  q.append((entry,name,company,status,'|'.join(x[1] for x in rows),'|'.join(x[0] for x in rows),code,state,json.dumps(assessments,ensure_ascii=False)))
 export('Q2_SUBSECTOR_RECONCILIATION.csv',['entry_id','stock_raw','company_id','tutor_reference_status','canonical_ids','canonical_labels','ss02_granular_code','ss02_research_status','original_assessments_json'],q)
 # Original memberships are retained as review candidates, never point-in-time eligibility.
 rows=c.execute('select m.quarter,m.company_id,co.current_legal_name,m.canonical_id,t.label from ss03_historical_memberships m join companies co using(company_id) join ss03_canonical t using(canonical_id) order by m.quarter,t.label,m.company_id').fetchall()
 # EMS independently researched peers are proposals, not proof of historical eligibility.
 candidates={(qtr,co,ca):(qtr,co,n,ca,l,'TUTOR_ORIGINAL_REFERENCE','UNKNOWN','CANDIDATE_ONLY') for qtr,co,n,ca,l in rows}
 for co,n,state in c.execute("select c.company_id,c.current_legal_name,m.status from ss02_company_mapping m join companies c using(company_id) where m.primary_code='EMS' order by c.company_id").fetchall():
  for qtr in CUT:
   key=(qtr,co,cid('EMS'))
   if key not in candidates:
    candidates[key]=(qtr,co,n,cid('EMS'),'EMS','SS02_RESEARCHED_'+state,'2026-11-02 research freeze','CANDIDATE_ONLY')
 records=[]
 for qtr,co,n,ca,l,origin,available,eligible in sorted(candidates.values()):
  source_rows=c.execute('select r.row_id,r.row_ref,s.sha256 from ss_rows r join ss_sources s using(source_id) left join ss02_identity_overlay i using(row_id) join ss_subsectors x on x.label_raw=r.label_raw join ss03_alias a using(subsector_id) where s.quarter=? and coalesce(i.company_id,r.company_id)=? and a.canonical_id=? order by r.row_ref',(qtr,co,ca)).fetchall()
  qualified=c.execute('select b.code,m.selection_evidence_json from ss02_baskets b join ss02_basket_members m using(basket_id) where b.quarter=? and m.company_id=? order by b.code',(qtr,co)).fetchall()
  records.append((qtr,CUT[qtr],co,n,ca,l,origin,available,eligible,json.dumps(source_rows),json.dumps(qualified),'Business relevance, source availability, listing continuity and full price coverage require review; SS02 metadata is evidence, not crosswalk equivalence'))
 cols=['quarter','cutoff','company_id','company_name','canonical_id','canonical_label','relationship','classification_available_at','eligibility','tutor_source_rows_json','ss02_qualified_metadata_json','reason']
 export('SUBSECTOR_CONSTITUENTS.csv',cols,records)
 export('HISTORICAL_BASKETS.csv',cols,records)
 readiness=[]
 for quarter in CUT:
  for ca,label,status in c.execute('select canonical_id,label,review_status from ss03_canonical order by label'):
   count=c.execute('select count(*) from ss03_historical_memberships where quarter=? and canonical_id=?',(quarter,ca)).fetchone()[0]
   readiness.append((quarter,CUT[quarter],ca,label,'CANDIDATE_ONLY' if count else 'BLOCKED',0,count,'Unknown source availability and no verified canonical peer basket' if count else 'No source membership for this quarter'))
 export('CHART_READINESS.csv',['quarter','cutoff','canonical_id','canonical_label','status','eligible_constituents','tutor_reference_candidates','reason'],readiness)
 print(json.dumps({'labels':c.execute('select count(*) from ss_subsectors').fetchone()[0],'canonical':c.execute('select count(*) from ss03_canonical').fetchone()[0],'priority':c.execute('select count(distinct canonical_id) from ss03_historical_memberships').fetchone()[0],'q2_status':{s:sum(1 for x in q if x[3]==s) for s in set(x[3] for x in q)},'candidate_memberships':len(rows),'integrity':c.execute('pragma integrity_check').fetchone()[0],'foreign_keys':c.execute('pragma foreign_key_check').fetchall()},indent=2))
 c.close()
if __name__=='__main__':run()
