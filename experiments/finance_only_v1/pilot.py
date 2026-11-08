"""Read a pinned historical snapshot or replay the four-company evidence bundle.

Run from repository root with python3 -m experiments.finance_only_v1.pilot.
Only this directory receives writes. No production discovery or network access.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from scrapper.research.features import company_quarter_features
from .engine import analyse

ROOT = Path(__file__).resolve().parents[2]
AREA = Path(__file__).resolve().parent
SNAPSHOT = ROOT/'artifacts/acquisition-readiness/AR-01.3B/post-migration/raw/scraper.db'
PINNED_SHA = 'd773f4863e41266ca2cbbd76f1d542eb888878be1380e40114c34b4164fe4afc'
CUTOFF = '2026-10-22T00:00:00+00:00'
COHORT = [(133,'TVSMOTOR',True,'motorcycle manufacturer; B0 non_bank auto stratum'),
          (217,'OFSS',True,'software vendor, not lender; B0 non_bank software stratum'),
          (141,'BAJAJ-AUTO',False,'motorcycle manufacturer; B0 non_bank auto stratum'),
          (86,'INDIACEM',False,'cement manufacturer; B0 non_bank cement stratum')]
FOCUS = ROOT/'sample_files-2/Q1 2027 FOCUS LIST.txt'
RESOLUTION = ROOT/'artifacts/b2-discovery-2026-10-01/B2_FOCUS_SYMBOL_RESOLUTION.csv'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def stamp(path):
    st=path.stat()
    return dict(size=st.st_size,mtime_ns=st.st_mtime_ns,ino=st.st_ino)


def open_snapshot(path):
    # Immutable mode is restricted to this retained, non-writable, pinned cold
    # snapshot. It is never used against a production/default/configured path.
    if path.resolve()!=SNAPSHOT.resolve():
        raise ValueError('Only the explicitly selected historical snapshot is allowed')
    if path.stat().st_mode & 0o222:
        raise ValueError('Snapshot must be non-writable')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
        raise ValueError('Snapshot has sidecars; refuse immutable access')
    conn=sqlite3.connect('file:'+quote(str(path))+'?mode=ro&immutable=1',uri=True)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA temp_store=MEMORY')
    versions=[r[0] for r in conn.execute('SELECT version FROM schema_migrations ORDER BY version')]
    if len(versions)!=76 or versions[-1]!='075_periodic_dispatch.sql':
        raise ValueError('Unexpected migration compatibility')
    # Defense in depth: application code cannot write even temporary tables.
    conn.set_authorizer(lambda action,*args: sqlite3.SQLITE_OK if action in {
        sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE} else sqlite3.SQLITE_DENY)
    return conn,versions


def utc(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()


def source_validation(rows,cells):
    index={}
    for c in cells:
        section={'profit_loss':'pnl','quarterly_results':'quarters'}.get(c['section_code'],c['section_code'])
        key=(section,c['row_position'],c['column_position'])
        index.setdefault(key,[]).append(c)
    checks=[]
    for r in rows:
        matched=index.get((r['section_code'],r['row_position'],r['column_position']),[])
        # Some parsers retain multiple table cells at one coordinate: fail closed.
        ok=len(matched)==1
        c=matched[0] if ok else {}
        display=' '.join(str(r['display_value'] or '').split())
        cell_display=' '.join(str(c.get('normalized_display_text') or '').split())
        ok=ok and display==cell_display and r['line_item_raw']==c.get('row_label_raw') and r['period_label_raw']==c.get('column_label_raw')
        if r['period_end'] is not None:
            try:
                from calendar import monthrange
                d=datetime.strptime(c.get('column_label_raw',''),'%b %Y')
                expected=f'{d.year:04}-{d.month:02}-{monthrange(d.year,d.month)[1]:02}'
                ok=ok and r['period_end']==expected
            except ValueError:
                ok=False
        numeric_ok=None
        if r['value_decimal'] is not None:
            try:
                raw=cell_display.replace(',','').replace('%','').strip()
                if raw.startswith('(') and raw.endswith(')'):
                    raw='-'+raw[1:-1]
                numeric_ok=Decimal(raw)==Decimal(r['value_decimal'])
            except InvalidOperation:
                numeric_ok=False
            ok=ok and numeric_ok
        checks.append(dict(observation_id=r['financial_observation_id'],match=ok,
            numeric_match=numeric_ok,section=r['section_code'],period=r['period_end'],
            row_position=r['row_position'],column_position=r['column_position']))
    return dict(checked=len(checks),matched=sum(r['match'] for r in checks),
                failed=[r for r in checks if not r['match']],checks=checks,
                basis='Retained original source-table cell records, same parse. Not original HTML or primary filings.')


def extract():
    before=stamp(SNAPSHOT)
    actual=digest(SNAPSHOT)
    if actual!=PINNED_SHA:
        raise ValueError('Historical snapshot hash differs from handoff')
    conn,versions=open_snapshot(SNAPSHOT)
    # Current accepted pointer census is an availability diagnostic, not a PIT
    # certified scoring universe. The selected companies below use the facade.
    coverage=[dict(r) for r in conn.execute('''WITH latest AS (
      SELECT *,row_number() OVER (PARTITION BY company_id,source,reporting_scope
        ORDER BY resolved_source_period DESC,accepted_at_utc DESC) rn
      FROM accepted_page_versions WHERE source='screener'
      AND resolved_source_period GLOB '20[0-9][0-9]-[01][0-9]'
      AND resolved_source_period<='2026-10')
      SELECT a.reporting_scope,f.section_code,f.period_type,
        count(DISTINCT a.company_id) companies,count(*) observations,
        sum(f.value_decimal IS NOT NULL AND coalesce(f.missing_kind,'')='') numeric_rows,
        min(f.period_end) first_period,max(f.period_end) last_period,
        count(DISTINCT f.line_item_code) metric_codes
      FROM latest a JOIN financial_observations f ON f.parse_id=a.parse_id
      WHERE a.rn=1 GROUP BY 1,2,3 ORDER BY 1,2,3''')]
    metric_inventory=[dict(r) for r in conn.execute('''WITH ids AS (
      SELECT DISTINCT parse_id FROM accepted_page_versions WHERE source='screener'
      AND resolved_source_period GLOB '20[0-9][0-9]-[01][0-9]')
      SELECT f.reporting_scope,section_code,line_item_code,unit_code,scale_multiplier,
        count(*) observations,sum(value_decimal IS NOT NULL AND coalesce(missing_kind,'')='') numeric_rows
      FROM financial_observations f JOIN ids USING(parse_id)
      GROUP BY 1,2,3,4,5 ORDER BY 1,2,3''')]
    tokens=set(FOCUS.read_text().strip().split(','))
    resolution=list(csv.DictReader(RESOLUTION.open()))
    payloads=[]
    for cid,symbol,member,sector_basis in COHORT:
        if ('NSE:'+symbol in tokens)!=member:
            raise ValueError('Supplied Focus membership changed')
        if member and not any(r['company_id']==str(cid) and r['symbol']==symbol and r['resolution_state']=='RESOLVED' for r in resolution):
            raise ValueError('Focus identity is unresolved')
        facade=company_quarter_features(conn,cid,CUTOFF,scope='standalone',sector='industrial',source='screener')
        pid=facade.provenance['parse_id']
        observations=[dict(r) for r in conn.execute('SELECT * FROM financial_observations WHERE parse_id=?',(pid,))]
        observations=[r for r in observations if utc(r['available_at_utc'])<=CUTOFF]
        for r in observations:
            r['available_at_utc']=utc(r['available_at_utc'])
        cells=[dict(r) for r in conn.execute('''SELECT s.section_code,c.* FROM source_sections s
           JOIN source_table_cells c USING(section_id) WHERE s.parse_id=? AND c.cell_role='data'
           AND s.section_code IN ('profit_loss','quarterly_results','balance_sheet','cash_flow','ratios') ''',(pid,))]
        identity=[dict(r) for r in conn.execute('SELECT * FROM source_identifiers WHERE company_id=? AND source=?',(cid,'screener'))]
        if not any(r['source_symbol']==symbol and r['mapping_status']=='verified' for r in identity):
            raise ValueError('Unverified company identity')
        blob=dict(conn.execute('SELECT * FROM response_blobs WHERE blob_id=?',(facade.provenance['blob_id'],)).fetchone())
        relative=Path(blob['local_path']).relative_to('/data/snapshots')
        candidates=[Path(blob['local_path']),ROOT/'scrapper/store/snapshots'/relative]
        original=next((p for p in candidates if p.is_file()),None)
        original_ok=original is not None and digest(original)==blob['content_sha256']
        fetches=[dict(r) for r in conn.execute('''SELECT fetch_id,requested_url,final_url,reporting_scope,
             fetched_at_utc,blob_id FROM fetch_observations WHERE blob_id=?''',(blob['blob_id'],))]
        payload=dict(company_id=cid,company=conn.execute('SELECT current_legal_name FROM companies WHERE company_id=?',(cid,)).fetchone()[0],
            symbol=symbol,focus_member=member,sector='industrial',sector_basis=sector_basis,
            scope='standalone',cutoff=CUTOFF,facade=asdict(facade),observations=observations,
            source_cells=cells,identity=identity,fetches=fetches,blob=blob,
            original_file_validation=dict(verified=original_ok,checked_paths=[str(p) for p in candidates]),
            warnings=[] if original_ok else ['Matching acquired HTML unavailable at checked paths: original-file and primary-source reconciliation gate remains open.'])
        payload['validation']=source_validation(observations,cells)
        payloads.append(payload)
    conn.close()
    after=stamp(SNAPSHOT)
    # Full pre/post hashes also prove preservation beyond file metadata.
    after_sha=digest(SNAPSHOT)
    if before!=after or actual!=after_sha:
        raise ValueError('Snapshot changed during read')
    return dict(snapshot=dict(path=str(SNAPSHOT.relative_to(ROOT)),sha256=actual,sha256_after=after_sha,
        before=before,after=after,migrations=versions,read_only=True),cutoff=CUTOFF,
        focus=dict(path=str(FOCUS.relative_to(ROOT)),sha256=digest(FOCUS),resolution_sha256=digest(RESOLUTION),
          interpretation='Supplied Q1 FY2027 reference, not independently certified live/current membership or historical freeze.'),
        coverage=coverage,metric_inventory=metric_inventory,companies=payloads)


def fmt(m):
    return m['value']+' '+m['unit'] if m['value'] is not None else '['+m['status']+']'


def render(report,payload):
    p=report['provenance']
    lines=[f"# {report['company']} ({report['symbol']})",'',
        f"Company ID {report['company_id']}; standalone; supplied Focus reference: {'present' if report['focus_member'] else 'absent (not rejected)' }.",
        f"Classification: {payload['sector_basis']}. Capture cutoff: {payload['cutoff']}.",
        f"Accepted parse {p['parse_id']}; blob {p['blob_id']}; SHA256 `{p['content_sha256']}`.",
        f"Accepted {p['accepted_at_utc']}; captured {p['blob_first_observed_at_utc']}; facade {payload['facade']['status']}.",
        '', '## Historical annual trends', '',
        'Currency amounts: INR crore; EPS: INR. Derived percentages use unrounded statement values.', '']
    for row in report['annual']:
        m=row['metrics']
        lines.append(f"- {row['period']}: revenue {fmt(m['revenue'])}; PAT {fmt(m['pat'])}; EPS {fmt(m['eps'])}; operating margin {fmt(m['operating_margin'])}; net margin {fmt(m['net_margin'])}; ROE {fmt(m['roe'])}; borrowings {fmt(m['borrowings'])}; CFO {fmt(m['cfo'])}; source FCF {fmt(m['fcf'])}; source working-capital days {fmt(m['working_capital_days'])}.")
    lines+=['','## Quarterly trends','']
    for row in report['quarters']:
        m=row['metrics']
        lines.append(f"- {row['period']}: revenue {fmt(m['revenue'])}; PAT {fmt(m['pat'])}; EPS {fmt(m['eps'])}; revenue YoY {fmt(m['revenue_yoy'])}; PAT YoY {fmt(m['pat_yoy'])}; EPS YoY {fmt(m['eps_yoy'])}.")
    lines+=['','## Latest annual calculations','']
    if report['annual']:
        for name,m in report['annual'][-1]['metrics'].items():
            lines.append(f"- {name}: {fmt(m)}. Formula: {m['formula']}. Observation IDs: {', '.join(map(str,m['inputs'])) or 'none'}.")
    a=report['assessment']
    lines+=['','## Provisional assessment','',
        f"{a['label']}: {a['passed']} of {a['evaluated']} evaluable experimental checks passed ({a['total_checks']} total). {a['basis']}.",
        'Five-year consistency: '+json.dumps(report['consistency'])+'.',
        '', 'Financial reasons supporting further Focus consideration:']
    lines += ['- '+x for x in report['strengths']] or ['- No evaluated check passed.']
    lines+=['','Financial reasons opposing or qualifying further Focus consideration:']
    lines += ['- '+x for x in report['weaknesses']] or ['- No adverse experimental check; this does not establish investment quality.']
    lines+=['','Unavailable assessment checks: '+('; '.join(a['unknown']) or 'none')+'.',
        '', 'Existing Focus classification is comparison context, not ground truth. These profiles neither validate nor overturn it. No membership change proposed or performed.',
        '', '## Data quality and reconciliation','']
    lines+=['- '+x for x in report['warnings']]
    v=payload['validation']
    numeric=sum(r['value_decimal'] is not None and not r['missing_kind'] for r in payload['observations'])
    lines += [f"- Retained source-cell checks: {v['matched']}/{v['checked']} matched; {len(v['failed'])} failures.",
        f"- Parse-level completeness: {numeric}/{len(payload['observations'])} observation values numeric (includes TTM and nonnumeric Raw PDF rows); per-metric missing states retained.",
        '- Actual acquisition URLs: '+', '.join(sorted(set(f['final_url'] for f in payload['fetches'])))+'. Identity alias URLs do not determine accounting scope.',
        '- Each metric retains observation IDs in report.json; evidence.json retains raw labels, displays, units, periods, source cells, acceptance and blob lineage.',
        '- Independently calculated ROCE and direct capex remain unsupported. Stored ROCE, source FCF and working-capital days retain provider-definition limitations.']
    return '\n'.join(lines)+'\n'


def write_reports(bundle):
    reports=[]
    for payload in bundle['companies']:
        validation=source_validation(payload['observations'],payload['source_cells'])
        if validation['failed'] or validation!=payload['validation']:
            raise ValueError('Source-cell reconciliation failure or inconsistent evidence receipt')
        report=analyse(payload)
        reports.append(report)
        (AREA/'reports'/f"{payload['symbol']}.md").write_text(render(report,payload))
    (AREA/'reports/report.json').write_text(json.dumps(reports,indent=2,allow_nan=False)+'\n')
    return reports


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--replay',action='store_true',help='Use retained evidence; no DB access')
    args=ap.parse_args()
    evidence=AREA/'reports/evidence.json'
    if args.replay:
        bundle=json.loads(evidence.read_text())
    else:
        bundle=extract()
        evidence.write_text(json.dumps(bundle,indent=2,allow_nan=False)+'\n')
    write_reports(bundle)
    print(json.dumps(dict(companies=4,source_cell_checks=sum(c['validation']['checked'] for c in bundle['companies']),
        source_cell_failures=sum(len(c['validation']['failed']) for c in bundle['companies']),
        original_files_verified=sum(c['original_file_validation']['verified'] for c in bundle['companies']),
        evidence_sha256=digest(evidence),output=str(AREA/'reports'))))


if __name__=='__main__':
    main()
