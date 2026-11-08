"""SS-02 scope-corrected full existing-universe classification and basket preparation."""
import argparse
import collections
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3

from ..subsector_ss01 import importer as base
from . import taxonomy, policy
from .build import OUT,EVIDENCE,MIGRATION,before_hash,identity_index,identity_overlays,monthly_metadata,resolve_symbol
from .inventory import write_csv

# Direct company-source research is retained separately from automatic proposals.
CURATED={
 'KROSS':['AUTO_AXLES','AUTO_FORGINGS'],'JAYNECOIND':['STEEL_MAKING'],
 'SKYGOLD':['JEWELLERY_MANUFACTURING'],'INDORAMA':['SYNTHETIC_YARN'],
 'NAUKRI':['INTERNET_CLASSIFIEDS'],'KRN':['HEAT_EXCHANGERS'],'AEROFLEX':['FLOW_PRODUCTS'],
 'ZENTEC':['DEFENCE_SIMULATION'],'PPLPHARMA':['PHARMA_CDMO','FORMULATIONS'],
 'KABRAEXTRU':['EXTRUSION_MACHINERY'],'ONWARDTEC':['ENGINEERING_RD'],
 'RAMRAT':['WINDING_WIRES'],'LMW':['TEXTILE_MACHINERY','PRECISION_ENGINEERING'],
 'FSL':['BPM'],
 'IXIGO':['TRAVEL_OTA'],'KFINTECH':['REGISTRARS'],'MTARTECH':['PRECISION_ENGINEERING'],
 'VSSL':['STEEL_MAKING'],'SASKEN':['ENGINEERING_RD'],'GREENPLY':['PLYWOOD'],
 'COFORGE':['IT_SERVICES'],'NILKAMAL':['FURNITURE','PLASTIC_MOULDINGS'],
 'SOLARA':['API'],'TIPSMUSIC':['MUSIC_RIGHTS'],'E2E':['GPU_CLOUD'],
 'KPRMILL':['COTTON_YARN','KNIT_GARMENTS'],'AETHER':['SPECIALTY_INTERMEDIATES'],
 'MAHLOG':['LOGISTICS_3PL'],'GRWRHITECH':['POLYESTER_FILMS'],
 'GOKULAGRO':['EDIBLE_OILS','CASTOR_DERIVATIVES'],'PSB':['PUBLIC_BANKS'],'MILKYMIST':['DAIRY'],
 'BBL':['TRANSFORMERS','INDUSTRIAL_MOTORS'],
 'TARIL':['TRANSFORMERS'],'LUMAXIND':['AUTO_LIGHTING'],
 'ORIENTELEC':['FANS','HOME_APPLIANCES','LIGHTING'],'JSWENERGY':['THERMAL_POWER','RENEWABLE_POWER'],
 'ANTELOPUS':['OIL_GAS_EP'],'APARINDS':['CONDUCTORS','POWER_CABLES','SPECIALTY_OILS'],
}


def classify(profile,curated=None,official=None):
    """Never turn a broad label into a completed business subsector."""
    hits=taxonomy.activities(profile['display_text'])
    evidence={h['code']:dict(basis='literal_documented_business_activity',matches=h['matches']) for h in hits}
    origin='documented_business_research_proposal'
    if curated:
        # Independently reviewed primary-company material supersedes regex proposals only.
        evidence={code:dict(basis='primary_company_source_research',source=curated['source']) for code in curated['codes']}
        origin='independent_primary_company_research'
    codes=sorted(evidence)
    primary=codes[0] if len(codes)==1 else None
    if not curated and len(codes)>1:
        explicit=[]
        text=profile['display_text'].casefold()
        for hit in hits:
            for match in hit['matches']:
                prefix=text[max(0,match['start']-90):match['start']]
                if re.search(r'(primarily|predominantly|principal business|mainly)[^.;]{0,85}$',prefix):
                    explicit.append(hit['code'])
        if len(set(explicit))==1:primary=explicit[0]
    status=('verified_primary_company_research' if curated else 'evidence_supported_proposal') if primary else 'diversified_or_multi_activity_review' if codes else 'insufficient_granular_business_evidence'
    official_decision=None
    if official:
        codes_from_official={taxonomy.OFFICIAL_BUSINESS.get(r['basic_industry']) for r in official}
        codes_from_official.discard(None)
        if len(codes_from_official)==1:
            published_code=next(iter(codes_from_official))
            specific_children={'PROPERTY_DEVELOPMENT':{'RESIDENTIAL_REALTY','COMMERCIAL_REALTY'},
                               'PRIVATE_BANKS':{'SMALL_FINANCE_BANKS'},'HOME_APPLIANCES':{'FANS'},
                               'ENGINEERED_WOOD_PANELS':{'PLYWOOD','LAMINATES','MDF'},
                               'APPAREL_PRODUCTS':{'KNIT_GARMENTS'},
                               'ELECTRICITY_GENERATION':{'THERMAL_POWER','RENEWABLE_POWER','HYDRO_POWER','BIOMASS_POWER'}}
            if primary and primary in specific_children.get(published_code,set()):
                official_decision='official_parent_confirms_proposed_specific_activity'
            elif curated and primary and primary!=published_code:
                status='primary_source_classification_conflict';primary=None
                evidence[published_code]=dict(basis='official_BSE_basic_industry',records=official)
                codes=sorted(evidence);official_decision='conflicting_primary_sources_require_review'
            else:
                primary=published_code
                evidence[primary]=dict(basis='official_BSE_basic_industry',records=official)
                codes=sorted(evidence);status='verified_official_granular_business';origin='official_BSE_basic_industry'
                official_decision='explicit_fine_business_descriptor'
        elif len(codes_from_official)>1:
            status='primary_source_classification_conflict';primary=None
            official_decision='competing_official_business_descriptors'
    return dict(primary_code=primary,status=status,origin=origin,official_business_evidence=official or [],official_decision=official_decision,
                exposures=[dict(code=code,role='primary' if code==primary else 'secondary' if primary else 'unranked_activity',evidence=evidence[code]) for code in codes])


def eligibility(record,code,cutoff):
    reasons=[]
    if not record:return ['historical_company_metadata_missing']
    if taxonomy.HISTORICAL_INDUSTRY.get(record['basic_industry'])!=code:
        reasons.append('no_dated_granular_business_proof_for_this_subsector')
    if datetime.fromisoformat(record['available_at'])>datetime.fromisoformat(cutoff):
        reasons.append('publisher_version_after_cutoff')
    if record['observation_date']>cutoff[:10]:reasons.append('observation_after_cutoff')
    if not record['historical_index_mcap_crore'] or record['historical_index_mcap_crore']<=0:
        reasons.append('missing_historical_capitalisation')
    impact=record['historical_impact_cost_percent']
    if impact is None or impact>policy.MAX_IMPACT_COST_PERCENT:reasons.append('missing_or_insufficient_historical_liquidity')
    if not record['quote_evidence']:reasons.append('historical_price_quote_not_observed')
    if record['symbol'].startswith('DUMMY'):reasons.append('non_tradable_placeholder')
    return reasons


def select(records):
    # Positive ranking inputs only. Never access returns, beta, today's cap or future performance.
    return sorted(records,key=lambda x:(-x['historical_index_mcap_crore'],x['company_id']))[:policy.TOP_N]


def evidence_manifest():
    return {str(p.relative_to(base.ROOT)):base.digest(p.read_bytes()) for p in sorted(EVIDENCE.rglob('*')) if p.is_file()}


def official_business(c):
    result=collections.defaultdict(list)
    code_ids=collections.defaultdict(set)
    for cid,code in c.execute("SELECT company_id,identifier_value FROM security_identifiers WHERE identifier_type='BSE'"):
        code_ids[str(code)].add(cid)
    directory=EVIDENCE/'bse-business-metadata'
    for path in sorted(directory.glob('*.json')):
        if path.name.endswith('.error.json'):continue
        doc=json.loads(path.read_text());r=doc['response'];code=str(r['SecurityCode'])
        if len(code_ids.get(code,set()))!=1:continue
        basic=' '.join((r.get('ISubGroup') or r.get('Industry') or '').split())
        result[next(iter(code_ids[code]))].append(dict(source_file=str(path.relative_to(base.ROOT)),
            source_url=doc['url'],retrieved_at=doc['retrieved_at'],source_sha256=base.digest(path.read_bytes()),
            security_code=code,isin=r.get('ISIN'),basic_industry=basic,
            broader_sector=r.get('Sector'),industry=r.get('IGroup'),historical_availability=None))
    return result


def make_plan():
    frozen=json.loads((OUT/'freeze.json').read_text())['frozen_at_utc']
    profiles=json.loads((EVIDENCE/'existing-business-profiles.json').read_text())
    research=json.loads((EVIDENCE/'researched_classifications.json').read_text())
    with base.readonly(base.TARGET) as c:
        baseline=before_hash(c);index,registry,_=identity_index(c)
        overlays,anchors=identity_overlays(c,index);by_row={o['row_id']:o for o in overlays}
        companies=dict(c.execute('SELECT company_id,current_legal_name FROM companies'))
        official=official_business(c)
        if {p['company_id'] for p in profiles}!=set(companies):raise ValueError('Full existing company universe must be covered')
        curated_by_id={}
        for symbol,codes in CURATED.items():
            cid,proof,ids=resolve_symbol(symbol,index)
            if cid is not None and symbol in research:
                curated_by_id[cid]=dict(codes=codes,source=research[symbol])
        tutor=collections.defaultdict(list)
        for rid,label,quarter in c.execute('''SELECT r.row_id,r.label_raw,s.quarter FROM ss_rows r JOIN ss_sources s USING(source_id)
                WHERE s.quarter IN ('FY2026-Q4','FY2027-Q1')'''):
            cid=by_row[rid]['company_id']
            if cid is not None:tutor[cid].append(dict(row_id=rid,label_raw=label,quarter=quarter))
        mappings=[]
        for profile in profiles:
            cid=profile['company_id'];result=classify(profile,curated_by_id.get(cid),official.get(cid))
            result.update(company_id=cid,company_name=companies[cid],known_at=frozen,valid_from=None,
                          tutor_evidence=tutor[cid],evidence=profile,evidence_sha256=base.digest(profile))
            mappings.append(result)
        by_company={m['company_id']:m for m in mappings}
        grouped_securities=collections.defaultdict(list)
        for r in registry:grouped_securities[r['isin']].append(r)
        security_mapping=[]
        for isin,records in sorted(grouped_securities.items()):
            ids=set()
            for r in records:ids.update(r.get('canonical_identity_candidates',r['canonical_name_matches']))
            cid=next(iter(ids)) if len(ids)==1 else None
            mapped=by_company.get(cid)
            security_mapping.append(dict(isin=isin,company_id=cid,primary_code=mapped['primary_code'] if mapped else None,
                state=mapped['status'] if mapped else 'ambiguous_canonical_link' if ids else 'no_canonical_company_business_profile',
                securities=records))
        q2=[]
        for eid,rid in c.execute('SELECT entry_id,min(row_id) FROM ss_focus_entry_rows GROUP BY entry_id ORDER BY entry_id'):
            cid=by_row[rid]['company_id'];mapped=by_company.get(cid)
            q2.append(dict(entry_id=eid,source_row_id=rid,company_id=cid,primary_code=mapped['primary_code'] if mapped else None,
                           state=mapped['status'] if mapped else by_row[rid]['state'],identity_evidence=by_row[rid]))
        monthly=monthly_metadata()
        hist={}
        for quarter,rows in monthly.items():
            hist[quarter]={}
            for record in rows:
                cid,proof,ids=resolve_symbol(record['symbol'],index,'NSE')
                if cid is not None:
                    hist[quarter][cid]=dict(record,company_id=cid,identity_evidence=proof)
        candidate_sets=collections.defaultdict(set)
        for m in mappings:
            for exposure in m['exposures']:candidate_sets[exposure['code']].add(m['company_id'])
        for records in hist.values():
            for cid,record in records.items():
                code=taxonomy.HISTORICAL_INDUSTRY.get(record['basic_industry'])
                if code:candidate_sets[code].add(cid)
        baskets=[]
        policy_hash=base.digest([Path(policy.__file__).read_text(),Path(taxonomy.__file__).read_text()])
        for code,cids in sorted(candidate_sets.items()):
            for quarter,cutoff in policy.CUTOFFS.items():
                reviews=[];eligible=[]
                for cid in sorted(cids):
                    metadata=hist[quarter].get(cid)
                    reasons=eligibility(metadata,code,cutoff)
                    reviews.append(dict(company_id=cid,status='eligible_on_retained_historical_metadata' if not reasons else 'requires_historical_evidence',reasons=reasons))
                    if not reasons:eligible.append(metadata)
                chosen=select(eligible);total=sum(r['historical_index_mcap_crore'] for r in chosen)
                members=[dict(company_id=r['company_id'],rank=n,equal_weight=1/len(chosen),historical_cap_weight=r['historical_index_mcap_crore']/total,
                    selection_evidence=r) for n,r in enumerate(chosen,1)]
                baskets.append(dict(basket_id=base.digest([code,quarter,policy_hash]),code=code,quarter=quarter,cutoff_at=cutoff,
                    frozen_at=frozen,status='FROZEN_METADATA_QUALIFIED_RECONSTRUCTION' if members else 'CANDIDATE_ONLY_HISTORICAL_EVIDENCE_MISSING',
                    chart_ready=0,policy_sha256=policy_hash,members=members,candidate_review=reviews,
                    caveat='Retrospective freeze; historical metadata coverage is restricted. Full daily price-feed coverage remains unverified.'))
        cross=collections.defaultdict(set)
        for m in mappings:
            for source in m['tutor_evidence']:
                for exposure in m['exposures']:cross[(source['label_raw'],exposure['code'])].add(m['company_id'])
    return dict(version='SS-02-full-universe-v1',baseline_sha256=baseline,frozen_at=frozen,
        taxonomy=taxonomy.definitions(),mappings=mappings,identity_overlays=overlays,q2=q2,baskets=baskets,security_mapping=security_mapping,
        tutor_crosswalk=[dict(label_raw=k[0],code=k[1],company_count=len(v),basis='observed_business_exposure_not_label_equivalence') for k,v in sorted(cross.items())],
        verified_sequence_anchors=anchors,unmatched_owner_symbol_entries=['NSE:ATHERENERG'],
        evidence_manifest=evidence_manifest(),code_sha256=base.digest(Path(__file__).read_bytes()),
        migration_sha256=base.digest(MIGRATION.read_bytes()))


def apply_to(c,plan):
    if before_hash(c)!=plan['baseline_sha256']:raise ValueError('SS-01 baseline changed')
    c.executescript(MIGRATION.read_text())
    old=c.execute("SELECT sha256 FROM ss02_migrations WHERE version='003'").fetchone()
    if old and old[0]!=plan['migration_sha256']:raise ValueError('SS-02 migration checksum changed')
    with c:
        c.execute('INSERT OR IGNORE INTO ss02_migrations VALUES(?,?)',('003',plan['migration_sha256']))
        run_id='SS-02-full-universe-v1'
        existing=c.execute('SELECT plan_sha256 FROM ss02_runs WHERE run_id=?',(run_id,)).fetchone()
        if existing and existing[0]!=base.digest(plan):raise ValueError('Existing frozen run cannot be silently replaced')
        for t in plan['taxonomy']:
            c.execute('INSERT OR IGNORE INTO ss02_taxonomy VALUES(?,?,1,?,?)',(t['code'],t['label'],t['origin'],base.encoded(t)))
        for m in plan['mappings']:
            p=m['evidence']
            c.execute('INSERT OR IGNORE INTO ss02_business_evidence VALUES(?,?,?,?,?)',(m['company_id'],m['evidence_sha256'],p['display_text'],p['available_at_utc'],base.encoded(p)))
            c.execute('INSERT OR IGNORE INTO ss02_company_mapping VALUES(?,?,?,?,?,NULL,?,?)',
                (m['company_id'],m['primary_code'],m['status'],m['origin'],m['known_at'],base.encoded(m['tutor_evidence']),base.encoded({k:v for k,v in m.items() if k!='evidence'})))
            for e in m['exposures']:
                c.execute('INSERT OR IGNORE INTO ss02_business_exposures VALUES(?,?,?,?)',(m['company_id'],e['code'],e['role'],base.encoded(e['evidence'])))
        for o in plan['identity_overlays']:
            c.execute('INSERT OR IGNORE INTO ss02_identity_overlay VALUES(?,?,?,?)',(o['row_id'],o['company_id'],o['state'],base.encoded(o['evidence'])))
        for q in plan['q2']:
            c.execute('INSERT OR IGNORE INTO ss02_q2_mapping VALUES(?,?,?,?,?)',(q['entry_id'],q['company_id'],q['primary_code'],q['state'],base.encoded(q)))
        for s in plan['security_mapping']:
            c.execute('INSERT OR IGNORE INTO ss02_security_mapping VALUES(?,?,?,?,?)',(s['isin'],s['company_id'],s['primary_code'],s['state'],base.encoded(s)))
        for x in plan['tutor_crosswalk']:
            c.execute('INSERT OR IGNORE INTO ss02_tutor_crosswalk VALUES(?,?,?,?)',(x['label_raw'],x['code'],x['company_count'],x['basis']))
        for b in plan['baskets']:
            c.execute('INSERT OR IGNORE INTO ss02_baskets VALUES(?,?,?,?,?,?,0,?)',(b['basket_id'],b['code'],b['quarter'],b['cutoff_at'],b['frozen_at'],b['status'],b['policy_sha256']))
            for m in b['members']:
                c.execute('INSERT OR IGNORE INTO ss02_basket_members VALUES(?,?,?,?,?,?)',(b['basket_id'],m['company_id'],m['rank'],m['equal_weight'],m['historical_cap_weight'],base.encoded(m['selection_evidence'])))
            for r in b['candidate_review']:
                c.execute('INSERT OR IGNORE INTO ss02_basket_candidate_review VALUES(?,?,?,?)',(b['basket_id'],r['company_id'],r['status'],base.encoded(r)))
        c.execute('INSERT OR IGNORE INTO ss02_runs VALUES(?,?,?,?)',(run_id,base.digest(plan),plan['baseline_sha256'],plan['frozen_at']))
    if before_hash(c)!=plan['baseline_sha256']:raise AssertionError('Historical source or assessment table changed')


def all_counts(c):
    return {t:c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")}


def summary(plan):
    return dict(total_existing_companies=len(plan['mappings']),mapping_states=dict(collections.Counter(m['status'] for m in plan['mappings'])),
        proposed_primary_mappings=sum(m['primary_code'] is not None for m in plan['mappings']),
        granular_taxonomy_definitions=len(plan['taxonomy']),existing_exchange_isins=len(plan['security_mapping']),
        exchange_isins_without_canonical_link=sum(s['company_id'] is None for s in plan['security_mapping']),
        q2_identity_states=dict(collections.Counter(q['identity_evidence']['state'] for q in plan['q2'])),
        baskets_with_historical_members=sum(bool(b['members']) for b in plan['baskets']),total_basket_manifests=len(plan['baskets']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['plan','apply']);args=parser.parse_args()
    plan=make_plan();path=OUT/'full-universe-plan.json'
    if args.mode=='plan':path.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    else:
        if not path.exists() or json.loads(path.read_text())!=plan:raise ValueError('Matching full-universe dry-run plan required')
        from ..subsector_ss01.q2 import safe_connect
        c=safe_connect()
        try:
            apply_to(c,plan);first=all_counts(c);apply_to(c,plan)
            assert first==all_counts(c)
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            (OUT/'validation.json').write_text(json.dumps(dict(table_counts=first,idempotency='passed',foreign_key_violations=0,integrity_check='ok',all_ss01_tables_unchanged=True,summary=summary(plan)),indent=2)+'\n')
        finally:c.close()
    print(json.dumps(summary(plan)))


if __name__=='__main__':main()
