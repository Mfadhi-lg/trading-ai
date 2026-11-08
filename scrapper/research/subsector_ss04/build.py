"""Evidence-conservative revision of existing research classifications; offline replay."""
import collections,csv,hashlib,json,sqlite3,zipfile
from pathlib import Path
from ..subsector_ss03 import build as ss03
R=ss03.ROOT; O=R/'artifacts/subsector-ss04'; DB=O/'research.sqlite3'
J=lambda x:json.dumps(x,sort_keys=True,ensure_ascii=False,default=lambda x:{"blob_sha256":hashlib.sha256(x).hexdigest()})
# Reviewed narrow official descriptors. Compound/broad descriptors deliberately excluded.
OFFICIAL={'IT Enabled Services':'BPM','Shipping':'MARITIME_SHIPPING','Financial Products Distributor':'SECURITIES_DISTRIBUTION','Internet & Catalogue Retail':'E_RETAIL','Waste Management':None,'Printing & Publication':None,'Trading - Auto Components':None,'Trading - Minerals':None}
# Literal activities in retained descriptions support proposals only, never verification.
PHRASES={'WHEEL_RIMS':['road wheels','alloy casting wheels'], 'FORMULATIONS':['differentiated formulations'], 'POWER_CABLES':['insulated wires & cables'], 'AIR_CARGO':['transporting cargo on an airport-to-airport'], 'TECHNICAL_TEXTILES':['engineered fabrics'], 'PET_RECYCLING':['pet waste recycling','recycled polyester staple fibre'], 'TRANSFORMER_BUSHINGS':['transformer bushing'], 'WATCHES':['manufacturing and trading of watches']}
TUTOR={'TYRE':'TYRES','TRANSFORMERS':'TRANSFORMERS','PLYWOOD MFG':'PLYWOOD','RATINGS':'CREDIT_RATING','EMS':'EMS'}
NEW={'TRANSFORMER_BUSHINGS':'High voltage and high current transformer bushings','TECHNICAL_TEXTILES':'Engineered woven coated and laminated technical textiles','WATCHES':'Watch manufacturing and distribution'}
# Exact current issuer statements inspected during SS04; undated pages are retrospective only.
RESEARCH={4294:('TRANSFORMER_BUSHINGS',[], 'https://www.yashhv.com/about/company-overview','Independent manufacturer of HV and EHV condenser graded transformer bushings.'),756:('PET_RECYCLING',['SYNTHETIC_YARN'],'https://ganeshaecosphere.com/about-us/','Recycles discarded PET bottles into polyester staple fibre and spun yarn.')}
def csvout(name,rows,fields=None):
 rows=list(rows);fields=fields or list(rows[0])
 with (O/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fields);w.writeheader();w.writerows(rows)
def fingerprint(c):
 return {t:hashlib.sha256(J(c.execute('select * from "'+t+'" order by rowid').fetchall()).encode()).hexdigest() for t, in c.execute("select name from sqlite_master where type='table' and name not like 'ss04_%' order by name")}
def rollback(c):
 for t in ['ss04_run','ss04_q2','ss04_exposures','ss04_decisions','ss04_taxonomy']:c.execute('drop table if exists '+t)
 c.commit()
def apply(c,p,major):
 ss03.migrate(c); baseline=fingerprint(c)
 c.executescript((Path(__file__).parent/'006_classification.sql').read_text())
 signature=hashlib.sha256((J(p)+J(sorted(major))+Path(__file__).read_text()+(Path(__file__).parent/'006_classification.sql').read_text()).encode()).hexdigest()
 if c.execute('select count(*) from ss04_decisions').fetchone()[0]:
  if c.execute('select signature from ss04_run').fetchall()!=[(signature,)]:raise ValueError('Frozen revision input/code mismatch; explicit isolated rollback/reapply required')
  return baseline
 c.execute('insert into ss04_run values (?)',(signature,))
 tutor=collections.defaultdict(list)
 for cid,quarter,label,ca,row,ref,source in c.execute('''select coalesce(i.company_id,r.company_id),s.quarter,r.label_raw,a.canonical_id,r.row_id,r.row_ref,s.source_path from ss_rows r join ss_sources s using(source_id) left join ss02_identity_overlay i using(row_id) join ss_subsectors t on t.label_raw=r.label_raw join ss03_alias a using(subsector_id) where s.quarter in ('FY2026-Q4','FY2027-Q1')'''):
  if cid:tutor[cid].append(dict(quarter=quarter,original_label=label,canonical_tutor_id=ca,row_id=row,locator=ref,source=source))
 taxonomy={x['code']:x for x in p['taxonomy']};taxonomy.update({k:dict(code=k,label=v,definition=v,origin='SS04 documented product activity') for k,v in NEW.items()})
 decisions=[]
 for m in sorted(p['mappings'],key=lambda x:(0 if tutor[x['company_id']] else 1 if x['company_id'] in {q['company_id'] for q in p['q2']} else 2,x['company_id'])):
  cid=m['company_id'];code=m['primary_code'];old=m['status'];exp={e['code']:dict(e) for e in m['exposures']}
  status='VERIFIED_RESEARCH' if old.startswith('verified_') else 'PROVISIONAL' if code else 'MULTI_ACTIVITY_REVIEW' if exp else 'UNRESOLVED'
  reason={'VERIFIED_RESEARCH':'Retained independently supported SS02 assignment; not newly verified by SS04.','PROVISIONAL':'Retained business-description proposal; underlying primary citation needs inspection.','MULTI_ACTIVITY_REVIEW':'Multiple documented activities; revenue dominance and primary activity not established.','UNRESOLVED':'Retained description and official industry do not establish a granular product/service assignment.'}[status]
  evidence=[dict(kind='retained_business_description',**m['evidence']),* [dict(kind='official_BSE',**x) for x in m['official_business_evidence']]]
  official={OFFICIAL.get(x['basic_industry']) for x in m['official_business_evidence']};official.discard(None)
  if status!='VERIFIED_RESEARCH' and len(official)==1:
   code=next(iter(official));status='VERIFIED_RESEARCH';reason='Reviewed explicit narrow BSE business descriptor; current classification, historical availability unknown.'
  for k,phrases in PHRASES.items():
   if any(s in m['evidence']['display_text'].casefold() for s in phrases):
    exp[k]=dict(code=k,role='unranked_activity',evidence={'basis':'SS04 literal retained activity','phrases':phrases})
  if status=='UNRESOLVED' and exp:
   code=next(iter(exp)) if len(exp)==1 else None;status='PROVISIONAL' if code else 'MULTI_ACTIVITY_REVIEW';reason='New literal activity recognition in retained description; primary issuer evidence still required.'
  # A product part is not a finished product: Wheels India's wind components do not establish turbine manufacture.
  if cid==405:
   code=None;status='MULTI_ACTIVITY_REVIEW';exp.pop('WIND_TURBINES',None);exp['WHEEL_RIMS']=dict(code='WHEEL_RIMS',evidence={'basis':'retained description: manufacture of road wheels'});reason='Removed unsupported turbine-manufacturing proposal: description specifies wind turbine parts. Wheel and industrial activities require primary segment review.'
  labels={ss03.ALIASES.get(x['original_label'],x['original_label']) for x in tutor[cid]}
  if status in ('UNRESOLVED','PROVISIONAL') and len(labels)==1 and next(iter(labels)) in TUTOR:
   code=TUTOR[next(iter(labels))];status='VERIFIED_TUTOR';reason='Exact product-specific original tutor reference; verifies tutor label only, not independently researched business dominance.'
  if cid in RESEARCH:
   code,secondary,url,statement=RESEARCH[cid];status='VERIFIED_RESEARCH';reason='Issuer business overview supports product-specific activity; undated current page, not historical eligibility.'
   exp={k:dict(code=k,evidence={'basis':'SS04 inspected issuer overview','source_url':url}) for k in [code]+secondary}
   evidence.append(dict(kind='SS04_issuer',source_url=url,statement=statement,publication_date=None,retrieved_date='2026-11-02',historical_availability='unknown; retrospective support'))
  if code in {'PROPERTY_DEVELOPMENT','ELECTRICITY_GENERATION','ENGINEERED_WOOD_PANELS','HOME_APPLIANCES'}:
   parent=code
   children={'PROPERTY_DEVELOPMENT':{'RESIDENTIAL_REALTY','COMMERCIAL_REALTY'},'ELECTRICITY_GENERATION':{'THERMAL_POWER','RENEWABLE_POWER','HYDRO_POWER','BIOMASS_POWER'},'ENGINEERED_WOOD_PANELS':{'PLYWOOD','LAMINATES','MDF'},'HOME_APPLIANCES':{'FANS','AIR_CONDITIONING','WATER_PURIFICATION'}}[parent]
   specific=set(exp)&children;exp.pop(parent,None)
   code=next(iter(specific)) if len(specific)==1 else None
   status='PROVISIONAL' if code else 'MULTI_ACTIVITY_REVIEW' if exp else 'UNRESOLVED'
   reason='Retained official parent '+parent+' is insufficiently specific for SS04; '+('description supports narrower activity pending issuer verification.' if specific else 'product or operating mix requires issuer segment evidence.')
  if code and code not in exp:exp[code]=dict(code=code,evidence={'basis':reason})
  evidence.extend(dict(kind='retained_assignment_evidence',code=e['code'],**e.get('evidence',{})) for e in exp.values())
  if status in ('UNRESOLVED','MULTI_ACTIVITY_REVIEW','PROVISIONAL'):
   descriptors=sorted({x['basic_industry'] for x in m['official_business_evidence']})
   reason+=' Company '+m['company_name']+'; official descriptors: '+(', '.join(descriptors) or 'none retained')+'; evidenced activity candidates: '+(', '.join(sorted(exp)) or 'none recognized')+'.'

  for k,e in exp.items():e['role']='primary' if k==code else 'secondary' if code else 'unranked_activity'
  d=dict(company_id=cid,company_name=m['company_name'],major_scope=int(cid in major),original_sector='|'.join(sorted({x.get('broader_sector') or '' for x in m['official_business_evidence']})),primary_code=code,status=status,reason=reason,prior_status=old,tutor_evidence=tutor[cid],evidence=evidence,exposures=list(exp.values()),known_at='2026-11-02',valid_from=None,historical_availability='Unknown; current evidence must not be backdated',tutor_history_conflict=len(labels)>1)
  decisions.append(d)
 used={e['code'] for d in decisions for e in d['exposures']}
 for k in sorted(used):c.execute('insert into ss04_taxonomy values (?,?,?)',(k,taxonomy[k]['label'],J(taxonomy[k])))
 for d in decisions:
  c.execute('insert into ss04_decisions values (?,?,?,?,?)',(d['company_id'],d['major_scope'],d['primary_code'],d['status'],J(d)))
  for e in d['exposures']:c.execute('insert into ss04_exposures values (?,?,?,?)',(d['company_id'],e['code'],e['role'],J(e['evidence'])))
 for q in p['q2']:c.execute('insert into ss04_q2 values (?,?,?)',(q['entry_id'],q['company_id'],J(q)))
 c.commit();assert fingerprint(c)==baseline
 return baseline
def run():
 O.mkdir(exist_ok=True,parents=True)
 p=json.loads((R/'artifacts/subsector-ss02/full-universe-plan.json').read_text())
 scope=list(csv.DictReader((R/'artifacts/subsector-ss02/existing-collection-scope-inventory.csv').open()))
 major={int(r['company_id']) for r in scope if r['eligibility']=='eligible' and r['company_id']};assert len(major)==1887
 if not DB.exists():
  src=sqlite3.connect(f'file:{ss03.SOURCE}?mode=ro',uri=True);dst=sqlite3.connect(DB);src.backup(dst);src.close();dst.close()
 c=sqlite3.connect(DB);c.execute('pragma foreign_keys=on');baseline=apply(c,p,major)
 ds=[json.loads(r[0]) for r in c.execute('select decision_json from ss04_decisions order by company_id')];byid={d['company_id']:d for d in ds}
 fields=['company_id','company_name','major_scope','original_sector','primary_code','status','reason','prior_status','known_at','valid_from','historical_availability','tutor_history_conflict']
 flat=lambda d:{k:d[k] for k in fields}
 csvout('MAJOR_STOCK_UNIVERSE.csv',[flat(d) for d in ds if d['major_scope']],fields)
 csvout('COMPANY_SUBSECTOR_FINAL.csv',[dict(**flat(d),exposures_json=J(d['exposures']),original_tutor_evidence_json=J(d['tutor_evidence'])) for d in ds])
 csvout('CANONICAL_SUBSECTORS.csv',[dict(canonical_id=k,label=l,definition_json=v,origin='granular_business') for k,l,v in c.execute('select * from ss04_taxonomy order by code')]+[dict(canonical_id=k,label=l,definition_json=J({'status':s,'granularity':'tutor reference; not automatically granular'}),origin='tutor_reference') for k,l,s in c.execute('select * from ss03_canonical order by canonical_id')])
 csvout('CLASSIFICATION_EVIDENCE.csv',[dict(company_id=d['company_id'],status=d['status'],primary_code=d['primary_code'],evidence_kind=e.get('kind','TUTOR_ORIGINAL'),source_url=e.get('source_url',e.get('final_url',e.get('source',''))),source_date=e.get('publication_date',e.get('source_published_at_utc')),evidence_locator=e.get('source_file',e.get('locator',e.get('local_path',''))),evidence_json=J(e),historical_availability=d['historical_availability']) for d in ds for e in d['evidence']+d['tutor_evidence']])
 csvout('DIVERSIFIED_COMPANIES.csv',[dict(**flat(d),exposures_json=J(d['exposures'])) for d in ds if len(d['exposures'])>1 or d['status']=='MULTI_ACTIVITY_REVIEW'])
 csvout('UNRESOLVED_COMPANIES.csv',[dict(record_type='company',**flat(d)) for d in ds if d['status'] in ('UNRESOLVED','MULTI_ACTIVITY_REVIEW','PROVISIONAL')]+[dict(record_type='Q2_identity',company_id='',company_name=r['stock_raw'],major_scope='',original_sector='',primary_code='',status='UNRESOLVED',reason='Original identity remains ambiguous or security has no existing canonical company; see preserved SS02 identity evidence.',prior_status='IDENTITY_UNRESOLVED',known_at='2026-11-02',valid_from='',historical_availability='unknown',tutor_history_conflict=False) for r in csv.DictReader((R/'artifacts/subsector-ss03/Q2_SUBSECTOR_RECONCILIATION.csv').open()) if not r['company_id']])
 coverage=[]
 populations={q:{cid for cid, in c.execute('select distinct company_id from ss03_historical_memberships where quarter=?',(q,))} for q in ss03.CUT}
 populations['FY2027-Q2']={q['company_id'] for q in p['q2'] if q['company_id']};populations['MAJOR']=major
 for pop,ids in populations.items():
  subset=[byid[i] for i in ids];counts=collections.Counter(d['status'] for d in subset)
  coverage.append(dict(population=pop,total_distinct_canonical_companies=len(ids),**{k:counts[k] for k in ['VERIFIED_TUTOR','VERIFIED_RESEARCH','PROVISIONAL','MULTI_ACTIVITY_REVIEW','UNRESOLVED']},distinct_granular_subsectors=len({d['primary_code'] for d in subset if d['primary_code']}),verified_granular_subsectors=len({d['primary_code'] for d in subset if d['status'].startswith('VERIFIED')}),unlinked_source_entries=5 if pop=='FY2027-Q2' else c.execute('select count(*) from ss_rows r join ss_sources s using(source_id) left join ss02_identity_overlay i using(row_id) where s.quarter=? and coalesce(i.company_id,r.company_id) is null',(pop,)).fetchone()[0]))
 csvout('Q4_Q1_Q2_COVERAGE.csv',coverage)
 peers=[]
 for code,label in c.execute('select code,label from ss04_taxonomy order by code'):
  subset=[d for d in ds if d['major_scope'] and d['primary_code']==code];verified=[d for d in subset if d['status'].startswith('VERIFIED')]
  if subset:peers.append(dict(code=code,label=label,major_primary_companies=len(subset),verified_primary_companies=len(verified),verified_company_ids=J([d['company_id'] for d in verified]),candidate_benchmark=len(verified)>=3,criterion='At least 3 verified primary major-universe companies; business comparability requires further review',historical_basket_ready=False))
 csvout('SUBSECTOR_PEER_COVERAGE.csv',peers)
 q2=list(csv.DictReader((R/'artifacts/subsector-ss03/Q2_SUBSECTOR_RECONCILIATION.csv').open()))
 for r in q2:
  d=byid.get(int(r['company_id'])) if r['company_id'] else None
  r.update(ss04_primary_code=d['primary_code'] if d else '',ss04_status=d['status'] if d else 'UNRESOLVED',ss04_reason=d['reason'] if d else 'No unambiguous existing canonical identity; preserve source and candidate evidence.')
 csvout('Q2_RECONCILIATION.csv',q2)
 # Retain source rows even when a cohort identity cannot be resolved.
 csvout('HISTORICAL_SOURCE_REVIEW.csv',[dict(quarter=q,row_id=rid,row_ref=ref,stock_raw=symbol,label_raw=label,company_id=cid,status=byid[cid]['status'] if cid else 'UNRESOLVED',reason=byid[cid]['reason'] if cid else 'No unambiguous canonical identity in retained overlay; manual source-context evidence required.') for q,rid,ref,symbol,label,cid in c.execute("select s.quarter,r.row_id,r.row_ref,r.symbol_raw,r.label_raw,coalesce(i.company_id,r.company_id) from ss_rows r join ss_sources s using(source_id) left join ss02_identity_overlay i using(row_id) where s.quarter in ('FY2026-Q4','FY2027-Q1') order by s.quarter,r.row_id")])
 stats=dict(companies=len(ds),major_companies=len(major),baseline_broader=dict(collections.Counter(m['status'] for m in p['mappings'])),baseline_major=dict(collections.Counter(m['status'] for m in p['mappings'] if m['company_id'] in major)),coverage=coverage,new_verified_research=sum(d['status']=='VERIFIED_RESEARCH' and not d['prior_status'].startswith('verified_') for d in ds if d['major_scope']),candidate_subsectors=sum(r['candidate_benchmark'] for r in peers),q2_entries=len(q2),q2_original_assessments=c.execute('select count(*) from ss_mentor_assessments').fetchone()[0],q2_reference_counts=dict(collections.Counter(r['tutor_reference_status'] for r in q2)),integrity=c.execute('pragma integrity_check').fetchone()[0],foreign_keys=c.execute('pragma foreign_key_check').fetchall(),original_table_fingerprints=baseline)
 (O/'validation.json').write_text(J(stats)+'\n');c.close();print(J({k:v for k,v in stats.items() if k!='original_table_fingerprints'}))
if __name__=='__main__':run()
