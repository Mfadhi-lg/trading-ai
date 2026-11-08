"""Prepare SS-02 overlays and frozen retrospective baskets from retained metadata."""
import argparse
import collections
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import io
import json
from pathlib import Path
import sqlite3
import zipfile

from openpyxl import load_workbook
from pypdf import PdfReader
from ..subsector_ss01 import importer as base
from .prepare_inputs import key
from . import policy

OUT=base.ROOT/'artifacts/subsector-ss02'
EVIDENCE=OUT/'evidence'
TXT=base.ROOT/'artifacts/earnings-reaction-v1/ta02b/inputs/683469f10f861daf833c663a5762cf5a3bf7879a10e8745eb9876195ca721ca6/Q2_2027_FOCUS_LIST.txt'
MIGRATION=Path(__file__).parent/'003_subsector_baskets.sql'


def before_hash(c):
    """Fingerprint every SS-01 table, including all historical mentor judgments."""
    return base.digest({t:[[base.digest(v) if isinstance(v,bytes) else v for v in row]
                          for row in c.execute(f'SELECT * FROM "{t}" ORDER BY 1,2')]
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'ss02_%' ORDER BY 1")})


def identity_index(c):
    index=collections.defaultdict(list)
    names=collections.defaultdict(set)
    for cid,name in c.execute('SELECT company_id,current_legal_name FROM companies'):
        names[key(name)].add(cid)
    for sid,cid,typ,value,*rest in c.execute('SELECT * FROM security_identifiers'):
        index[value].append(dict(company_id=cid,basis='existing_security_identifier',record_id=sid,exchange=typ))
    registry=json.loads((EVIDENCE/'official-identity-registry.json').read_text())['rows']
    for rec in registry:
        if rec['verification']!='verified':
            continue
        for cid in rec.get('canonical_identity_candidates',names.get(key(rec['name']),set())):
            index[rec['symbol']].append(dict(company_id=cid,basis='existing_exchange_registry_isin_link' if cid==rec.get('existing_registry_company_id') else 'official_exchange_exact_legal_name',
                exchange=rec['exchange'],security_id=rec['security_id'],version_id=rec['version_id'],isin=rec['isin'],name=rec['name']))
    amfi=[]
    for number,r in enumerate(load_workbook(EVIDENCE/'amfi-dec2025.xlsx',read_only=True,data_only=True).active.values,1):
        if number<=2 or not isinstance(r[0],(int,float)):
            continue
        for symbol in [r[3],r[5]]:
            if symbol and symbol!='-':
                for cid in names.get(key(r[1]),set()):
                    index[symbol].append(dict(company_id=cid,basis='AMFI_exact_legal_name_and_exchange_symbol',
                        source='amfi-dec2025.xlsx',row=number,isin=r[2],name=r[1]))
        amfi.append(dict(row=number,name=r[1],isin=r[2],bse_symbol=r[3],nse_symbol=r[5],average_total_mcap_crore=r[9]))
    return index,registry,amfi


def resolve_symbol(symbol,index,exchange=None):
    proof=[r for r in index.get(symbol,[]) if exchange is None or r.get('exchange',exchange)==exchange]
    ids=sorted({r['company_id'] for r in proof})
    return (ids[0] if len(ids)==1 else None),proof,ids


def identity_overlays(c,index):
    tokens=TXT.read_text().strip().split(',')
    if base.digest(TXT.read_bytes())!='683469f10f861daf833c663a5762cf5a3bf7879a10e8745eb9876195ca721ca6' or len(tokens)!=81:
        raise ValueError('Owner symbol snapshot changed')
    if tokens[40]!='NSE:ATHERENERG':
        raise ValueError('Explicit unmatched ATHERENERG insertion requires review')
    # Manual sequence reconciliation: CSV records 1..40 match TXT 1..40;
    # TXT token 41 is an extra security. CSV 41..80 match TXT 42..81.
    ordinals={rid:n for rid,n in c.execute('SELECT row_id,original_row_number FROM ss_mentor_assessments')}
    overlays=[];anchors=0
    for rid,raw,existing,name,quarter,ref in c.execute('''SELECT r.row_id,r.symbol_raw,r.company_id,r.company_name_raw,s.quarter,r.row_ref
        FROM ss_rows r JOIN ss_sources s USING(source_id) ORDER BY s.quarter,r.row_ref'''):
        proof=[];cid=existing;state='preserved_verified' if existing is not None else 'unresolved'
        if quarter=='FY2027-Q2' and ordinals[rid]<=80:
            n=ordinals[rid];pos=n if n<=40 else n+1
            venue,symbol=tokens[pos-1].split(':',1)
            linked,official,ids=resolve_symbol(symbol,index,venue)
            if existing is not None and linked is not None:
                if linked!=existing:
                    raise ValueError(f'Cross-source identity contradiction at CSV record {n}')
                anchors+=1
            if linked is not None:
                cid=linked;state='verified_cross_source' if existing is None else state
            elif existing is None and len(ids)>1:
                state='ambiguous'
            proof=dict(basis='owner_symbol_list_sequence_and_official_identity',txt_path=str(TXT),
                       txt_token_number=pos,txt_token=tokens[pos-1],csv_record=n,official=official,
                       warning='Identity corroboration only. Owner-declared Oct 30 snapshot is NOT an independently dated mentor decision.')
            if raw in ('Garware Technical','Vardhaman Special Tubes','Ather Industries'):
                proof['source_name_discrepancy_requires_review']=True
        elif existing is None:
            symbol=policy.REVIEWED_ALIASES.get(raw,raw)
            cid,official,ids=resolve_symbol(symbol,index)
            state='verified_reviewed_alias' if cid is not None and raw in policy.REVIEWED_ALIASES else 'verified_official_identity' if cid is not None else 'ambiguous' if len(ids)>1 else 'unresolved'
            proof=dict(basis='reviewed_brand_alias' if raw in policy.REVIEWED_ALIASES else 'official_exchange_symbol',symbol=symbol,official=official)
            if cid is None and name and quarter!='FY2027-Q2':
                # A mistyped source symbol can be linked via its exact source legal name.
                legal={p['company_id'] for pp in index.values() for p in pp if p.get('name') and key(p['name'])==key(name)}
                if len(legal)==1:
                    cid=next(iter(legal));state='verified_exact_source_name'
                    proof=dict(basis='exact_source_legal_name_with_official_exchange_record',source_name=name,
                        official=[p for pp in index.values() for p in pp if p['company_id']==cid and p.get('name') and key(p['name'])==key(name)])
        if raw in ('Acme','Sambhav','Ganesa Eco') and existing is None:
            cid=None;state='ambiguous';proof=dict(basis='source_shorthand_ambiguous',
                 candidates={'Acme':['ACMESOLAR','ACME'],'Sambhav':['SAMBHV','SAMBHAAV'],'Ganesa Eco':['GANECOS','GANVERSE']}[raw])
        external_path=EVIDENCE/'external-identities.json'
        if cid is None and external_path.exists():
            external=json.loads(external_path.read_text()).get(raw)
            if external:
                state='verified_security_requires_canonical_company'
                proof=dict(prior_evidence=proof,external=external)
        overlays.append(dict(row_id=rid,company_id=cid,state=state,source_quarter=quarter,raw_symbol=raw,
                             source_row_ref=ref,evidence=proof))
    if anchors<35:
        raise ValueError('Insufficient independently verified sequence anchors')
    return overlays,anchors


def monthly_metadata():
    result={}
    for quarter,tag,month in [('FY2026-Q4','feb2026','Feb2026'),('FY2027-Q1','apr2026','Apr2026')]:
        archive=EVIDENCE/f'nifty-mcwb-{tag}.zip'
        metadata=json.loads(Path(str(archive)+'.metadata.json').read_text())
        available=parsedate_to_datetime(metadata['headers']['Last-Modified']).isoformat()
        quotes={}
        with zipfile.ZipFile(EVIDENCE/f'nifty-indices-{tag}.zip') as z:
            for name in [f'NIFTY_50_{month}.pdf',f'NIFTY_Next_50_{month}.pdf']:
                reader=PdfReader(io.BytesIO(z.read(name)))
                for page_number,page in enumerate(reader.pages,1):
                    for line in page.extract_text(extraction_mode='layout').splitlines():
                        cells=line.split()
                        if len(cells)>5:
                            try:
                                price=float(cells[-3]);float(cells[-2]);float(cells[-1])
                            except ValueError:
                                continue
                            if price>0:
                                quotes[cells[0]]=dict(archive=f'nifty-indices-{tag}.zip',member=name,page=page_number,
                                    status='historical_month_end_quote_observed_full_series_unverified')
        data=[]
        with zipfile.ZipFile(archive) as z:
            for name in sorted(z.namelist()):
                reader=csv.DictReader(io.StringIO('\n'.join(z.read(name).decode('utf-8-sig').splitlines()[2:])))
                for ordinal,r in enumerate(reader,4):
                    symbol=r.get('Security Symbol','').strip()
                    if not symbol or not r.get('Sr. No','').isdigit():
                        continue
                    data.append(dict(symbol=symbol,name=r['Security Name'],basic_industry=r['Basic Industry'],
                        historical_index_mcap_crore=float(r['Index Market Capitalisation (Rs. Crores)']),
                        historical_impact_cost_percent=float(r['Avg. Impact Cost (%)']) if r['Avg. Impact Cost (%)'] not in ('','-') else None,
                        observation_date='2026-02-27' if quarter=='FY2026-Q4' else '2026-04-30',
                        available_at=available,availability_basis='official_publisher_Last_Modified_attestation_not_independent_web_archive',
                        source_file=archive.name,member=name,row=ordinal,quote_evidence=quotes.get(symbol)))
        result[quarter]=data
    return result


# Scope correction: full-universe classification is implemented in full_universe.py.
