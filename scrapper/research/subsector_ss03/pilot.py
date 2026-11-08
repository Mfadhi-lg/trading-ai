"""Five-category research pilot. Data are retrospective reviews, not executable backtests."""
import csv,io,json,sqlite3,zipfile
from pathlib import Path
from . import build
R=build.ROOT; OUT=build.OUT/'pilot-baskets'; DB=Path('/tmp/subsector-ss03b-isolated.sqlite3')
# Dated documents concern business operations; publication dates are only populated when explicitly evidenced.
CANDIDATES={
'EMS':[
(645,'Electronic contract manufacturer across consumer product families; diversification across product lines','https://dixoninfo.com/?Itemid=492&id=71&option=com_content&view=article','', 'Company overview is current; March 2025 proposal alone is not proof of operating exposure'),
(487,'Electronic design and manufacturing services','https://syrmasgs.com/new-milestone-syrma-sgs-sets-up-the-largest-electronics-manufacturing-facility-in-pune/','2024-11-19','Dated manufacturer announcement'),
(860,'Integrated electronic manufacturing and box-build; global operations','https://www.avalontec.com/investors/','','Company FY2025 report listed; filing/publication date not independently established'),
(1629,'Electronic design and manufacturing services; diverse end-markets','https://www.kaynestechnology.co.in/doc/Stock-Exchange-Disclosures/Annual%20Report%20FY2023-24.pdf','2024-08-17','2024 annual report with dated notice')],
'TRANSFORMERS':[
(610,'Power and industrial transformers','https://www.voltamptransformers.com/userfile/pdf/1751635237_ANNUAL_REPORT_2024_25.pdf','','FY2025 annual report; publication date not independently established'),
(110,'Power transformer manufacturing','https://www.transformerindia.com/wp-content/uploads/2024/05/30thAGMReport01052024Final.pdf','2024-05-01','Dated company annual report notice'),
(1731,'Grid equipment including power transformers; diversified beyond transformers','https://www.hitachienergy.com/us/en/news-and-events/press-releases/2025/06/hitachi-energy-india-to-deliver-765-kv-transformers-to-power-grid-corporation-of-india-limited-to-advance-country-s-grid-expansion','2025-06-26','Dated company transformer order')],
'TYRE':[
(68,'Tyre manufacturing for different vehicle segments','https://www.ceat.com/investors/shareholder-information/stock-exchange-announcements.html','2025-07-29','Dated FY2025 annual report submission; report describes tyre operations'),
(1091,'Tyre manufacturing in India and Europe','https://corporate.apollotyres.com/content/dam/orbit/apollo-corporate/investors/announcements/2025-26/Submission%20of%20AGM%20Notice%20and%20Annual%20Report%20FY25%20-%20July%208%2C%202025.pdf','2025-07-08','Dated annual report submission'),
(1601,'Tyre manufacturing with multiple vehicle categories','https://international.jktyre.com/international/investor','2024-08-02','2024 AGM statement describes tyre plant and operating business')],
'FERTILIZER':[
(578,'Urea production plus trading other agricultural inputs','https://www.chambalfertilisers.com/pdf/Annual-Report-for-the-Financial-Year-2024-2025.pdf','','FY2025 annual report; publication date not independently established'),
(269,'Phosphatic fertilisers plus crop protection and other activities','https://www.bseindia.com/xml-data/corpfiling/AttachHis/f73f363e-f614-4498-a96f-357a57041685.pdf','2025-07-02','Dated exchange filing FY2025 annual report'),
(437,'Phosphatic and complex fertilisers; also urea exposure','https://www.paradeepphosphates.com/uploads/content/annual-report-2024-25.pdf','','FY2025 annual report; publication date not independently established')],
'SHIP BUILDING':[
(458,'Naval shipbuilding with engineering activities','https://grse.in/press-releases-2025-26-quarter2/','2025-09-19','Dated AGM account confirms shipbuilding and engineering'),
(583,'Naval ships and submarines; ship repair','https://mazagondock.in/English/who-we-are','','Current issuer overview; historical publication date unknown'),
(1254,'Shipbuilding and material ship-repair business','https://cochinshipyard.in/uploads/investor/d65f33bc1b65c3ea1f3f36d95b27b293.pdf','','FY2025 report separates shipbuilding and ship repair; publication date not established')]
}
# All tutor-only members are evaluated separately; no old assignment is changed.
DECISIONS={132:('EXCLUDE','Cloud/GPU services, not electronics contract manufacturing'),836:('EXCLUDE','ICT distribution and after-sales, not electronics contract manufacturing'),551:('EXCLUDE','Refurbishment of IT devices, not demonstrated contract electronics manufacturing'),725:('EXCLUDE','Kitchen appliance brand/manufacturer; contract EMS exposure not established'),1814:('EXCLUDE','Marine electrical automation and services; contract EMS exposure not established'),743:('EXCLUDE','Shipping and offshore services, not shipbuilding'),2111:('EXCLUDE','Subsea and vessel operations, not shipbuilding')}
REVIEW_NOTES={1240:'Defence and mission-critical electronics design and manufacturing; diversified and not a pure contract-EMS peer; historical customer mix to review',1695:'Diversified appliance manufacturing and EMS; direct exposure is credible, but own-product and segment mix need dated review',4343:'PCB design and assembly manufacturing; focused EMS exposure plausible, but historic listing and manufacturing scope need verification',129:'Power transformer manufacturer; listing before each formation date and trading history require verification',533:'Fertilisers and industrial/mining chemicals; material diversification and segment mix require dated review',892:'Fertilisers and industrial chemicals; fertilizer exposure is plausible but segment mix needs review',1609:'Fertiliser and polymers business; verify the historical listed entity, separation and eligibility at each cutoff'}
def historical(symbol,quarter):
 tag='feb' if quarter=='FY2026-Q4' else 'apr'; p=R/f'artifacts/subsector-ss02/evidence/nifty-mcwb-{tag}2026.zip'
 found=[]
 with zipfile.ZipFile(p) as z:
  for name in sorted(z.namelist()):
   rows=list(csv.reader(io.StringIO(z.read(name).decode('utf-8-sig'))))
   for j,row in enumerate(rows[3:],4):
    if len(row)>11 and row[1].strip()==symbol:
     found.append({'archive':str(p.relative_to(R)),'member':name,'row':j,'symbol':row[1],'name':row[2],'basic_industry':row[3],'index_mcap_crore':row[5],'impact_cost_pct':row[11],'availability':'2026-03-06T17:15:00+00:00' if tag=='feb' else '2026-05-11T05:45:14+00:00'})
 return found

def run():
 OUT.mkdir(parents=True,exist_ok=True)
 src=sqlite3.connect(f'file:{build.SOURCE}?mode=ro',uri=True);c=sqlite3.connect(DB);src.backup(c);src.close();c.execute('pragma foreign_keys=on');build.migrate(c);c.executescript((Path(__file__).parent/'005_pilot.sql').read_text())
 major={int(r['company_id']) for r in csv.DictReader(open(R/'artifacts/subsector-ss02/existing-collection-scope-inventory.csv')) if r['company_id'] and r['eligibility']=='eligible'}
 reviews=[];baskets=[];members=[]
 for label,selected in CANDIDATES.items():
  ca=build.cid(label);selected={x[0]:x for x in selected}
  tutors={r[0] for r in c.execute('select distinct company_id from ss03_historical_memberships where canonical_id=?',(ca,))}
  for ident in sorted(tutors|selected.keys()):
   name=c.execute('select current_legal_name from companies where company_id=?',(ident,)).fetchone()[0]
   symbols=[x[0] for x in c.execute("select identifier_value from security_identifiers where company_id=? and identifier_type='NSE' and is_current=1",(ident,))]
   desc=c.execute('select description_raw from ss02_business_evidence where company_id=?',(ident,)).fetchone()[0]
   quarters=[x[0] for x in c.execute('select quarter from ss03_historical_memberships where company_id=? and canonical_id=? order by quarter',(ident,ca))]
   if ident in selected:
    _,reason,url,date,note=selected[ident]; decision='CANDIDATE';detail={'business':reason,'source_url':url,'source_date':date,'date_basis':note}
   elif ident in DECISIONS:
    decision,reason=DECISIONS[ident];detail={'business':reason,'source_url':'SS02 retained business profile','source_date':'','date_basis':'retrospective profile; exclusion requires company-specific historical confirmation'}
   else:
    decision='REVIEW';detail={'business':REVIEW_NOTES.get(ident,'Tutor grouped company; examine specific activity before inclusion'),'source_url':'SS02 retained business profile','source_date':'','date_basis':'retrospective only'}
   source_rows=c.execute("select r.row_id,r.row_ref,s.quarter,s.sha256,r.label_raw from ss_rows r join ss_sources s using(source_id) left join ss02_identity_overlay i using(row_id) join ss_subsectors t on t.label_raw=r.label_raw join ss03_alias a using(subsector_id) where coalesce(i.company_id,r.company_id)=? and a.canonical_id=? order by s.quarter,r.row_ref",(ident,ca)).fetchall()
   detail.update({'tutor_source_rows':source_rows,'name':name,'major_scope':ident in major,'scope_observed_at':'2026-09; retrospective candidate discovery only','current_nse_symbols':symbols,'tutor_quarters':quarters,'ss02_description':desc,'identity_warning':'Current identifier; historical continuity not established'})
   c.execute('insert or replace into ss03_pilot_review values (?,?,?,?)',(ca,ident,decision,json.dumps(detail,sort_keys=True)))
   reviews.append((label,ca,ident,name,decision,json.dumps(detail,ensure_ascii=False,sort_keys=True)))
  for q,cut in build.CUT.items():
   c.execute('insert or replace into ss03_pilot_basket values (?,?,?,?,0)',(ca,q,cut,'PROVISIONAL'))
   baskets.append((label,ca,q,cut,'PROVISIONAL',len(selected),0,'Dated business, historical listing, size/liquidity and complete price coverage not established for every candidate'))
   for ident,(_,reason,url,date,note) in sorted(selected.items()):
    assert ident in major
    c.execute('insert or replace into ss03_pilot_member values (?,?,?,?)',(ca,q,ident,'PROVISIONAL'))
    sym=c.execute("select identifier_value from security_identifiers where company_id=? and identifier_type='NSE' and is_current=1 order by identifier_value limit 1",(ident,)).fetchone(); sym=sym[0] if sym else ''
    m=historical(sym,q) if sym else []
    members.append((label,ca,q,cut,ident,c.execute('select current_legal_name from companies where company_id=?',(ident,)).fetchone()[0],sym,'PROVISIONAL',reason,url,date,note,json.dumps(m,sort_keys=True),'NOT_ESTABLISHED','NOT_ESTABLISHED',f'1/{len(selected)}','UNAVAILABLE: require historical capitalisation for every candidate'))
 c.commit()
 def write(file,head,rows):
  with (OUT/file).open('w',encoding='utf-8',newline='') as f:
   w=csv.writer(f);w.writerow(head);w.writerows(rows)
 write('PEER_REVIEW.csv',['label','canonical_id','company_id','company_name','decision','evidence_json'],reviews)
 write('CANDIDATE_BASKETS.csv',['label','canonical_id','quarter','cutoff','company_id','company_name','current_nse_symbol','eligibility','business_exposure','source_url','source_date','source_date_basis','historical_metadata_json','listing_continuity','full_price_coverage','candidate_equal_weight','historical_weight'],members)
 write('READINESS.csv',['label','canonical_id','quarter','cutoff','status','credible_candidate_count','chart_ready','reason'],baskets)
 print(json.dumps({'reviews':len(reviews),'members':len(members),'baskets':len(baskets),'integrity':c.execute('pragma integrity_check').fetchone()[0],'fk':c.execute('pragma foreign_key_check').fetchall(),'metadata_hits':sum(1 for x in members if x[12]!='[]')},indent=2));c.close()
if __name__=='__main__':run()
