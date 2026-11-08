"""Additive SS-01 Q2 import. Exact matches only; duplicate conflicts remain reviewable."""
import collections
import csv
from datetime import datetime, timezone
import difflib
import io
import json
from pathlib import Path
import re
import sqlite3

from . import importer as base

OUT = base.ROOT/'artifacts/subsector-ss01/v2'
SOURCE = Path('/Users/mohamedfadhi/Downloads/2026_Q2_Results_Focus_List_100_Stocks.csv')
MIGRATION = Path(__file__).parent/'migrations/002_q2_assessments.sql'
QUARTER = 'FY2027-Q2'
FIELDS = ['Sl No','Stock','Q2 Results','Rating','Guidance','Extraction Note']


def compact(text):
    return ''.join(c for c in text.casefold() if c.isalnum())


def name_key(text):
    # Only corporate suffixes, case, punctuation and spacing; no token expansion or spelling correction.
    return compact(re.sub(r'\s+(?:limited|ltd)\.?\s*$', '', text, flags=re.IGNORECASE))


def parse(path):
    content=Path(path).read_bytes()
    sid=base.digest([str(Path(path).resolve()),base.digest(content)])
    source=dict(source_id=sid,source_path=str(Path(path).resolve()),sha256=base.digest(content),
                format='q2_assessment_csv',quarter=QUARTER,period_end='2026-09-30',known_at=None,
                date_basis='quarter supplied by owner; mentor decision and assessment availability unknown')
    text=content.decode('utf-8-sig')
    lines=text.splitlines(keepends=True)
    reader=csv.DictReader(io.StringIO(text,newline=''))
    if reader.fieldnames!=FIELDS:
        raise ValueError('Unexpected Q2 fields')
    rows=[]
    last=1
    for ordinal,item in enumerate(reader,1):
        if None in item or any(v is None for v in item.values()):
            raise ValueError('Malformed Q2 CSV record')
        if not item['Stock'].strip():
            raise ValueError('Empty stock label requires source review')
        ref=f'lines:{last+1}-{reader.line_num}'
        rows.append(dict(row_id=base.digest([sid,ref]),source_id=sid,row_ref=ref,
                         raw_text=''.join(lines[last:reader.line_num]),symbol_raw=item['Stock'],
                         exchange_raw=None,company_name_raw=item['Stock'],label_raw=None,
                         original_row_number=ordinal,fields=item))
        last=reader.line_num
    return source,rows


def identity(row,c):
    stock=row['fields']['Stock']
    key=compact(stock)
    proof=[]
    for r in c.execute('SELECT * FROM security_identifiers ORDER BY 1'):
        if compact(r[3])==key:
            proof.append(dict(company_id=r[1],basis='exact_compact_exchange_symbol',record=list(r)))
    for r in c.execute("SELECT * FROM source_identifiers WHERE mapping_status='verified' ORDER BY 1"):
        if r[4] and compact(r[4])==key:
            proof.append(dict(company_id=r[1],basis='exact_compact_verified_source_symbol',record=list(r)))
    companies=c.execute('SELECT company_id,current_legal_name FROM companies ORDER BY 1').fetchall()
    for cid,name in companies:
        if name_key(name)==name_key(stock):
            proof.append(dict(company_id=cid,basis='exact_corporate_suffix_name',record=[cid,name]))
    # Original Q4 full company names supply an additional exact historical name alias.
    for cid,name,rid in c.execute('''SELECT DISTINCT r.company_id,r.company_name_raw,r.row_id
        FROM ss_rows r JOIN ss_sources s USING(source_id)
        WHERE s.quarter IN ('FY2026-Q4','FY2027-Q1') AND r.company_id IS NOT NULL AND r.company_name_raw IS NOT NULL
        ORDER BY r.row_id'''):
        if name_key(name)==name_key(stock):
            proof.append(dict(company_id=cid,basis='exact_prior_source_name',record=[cid,name,rid]))
    ids=sorted({p['company_id'] for p in proof})
    state='resolved' if len(ids)==1 else 'ambiguous' if ids else 'unresolved'
    # Suggestions are review-only. Prefixes and spelling similarity never authorize a merge.
    suggestions=[]
    if state!='resolved':
        prefixes=[(cid,name) for cid,name in companies if name_key(name).startswith(name_key(stock))]
        if not ids and len(prefixes)>1:
            state='ambiguous'
            ids=sorted(cid for cid,name in prefixes)
        ranked=sorted(((difflib.SequenceMatcher(None,name_key(stock),name_key(name)).ratio(),cid,name)
                       for cid,name in companies),reverse=True)[:3]
        suggestions=[dict(company_id=cid,name=name,status='unverified_prefix_suggestion') for cid,name in prefixes]
        suggestions += [dict(company_id=cid,name=name,similarity=round(score,3),status='unverified_suggestion')
                     for score,cid,name in ranked if score>=0.45]
    return dict(company_id=ids[0] if state=='resolved' else None,resolution_state=state,
                resolution=dict(candidates=ids,proof=proof,review_suggestions=suggestions,
                                historical_identity_availability='not established at Q2 decision time'))


def prior(c,cid):
    if cid is None:
        return []
    return [dict(zip(['assignment_id','subsector_id','label_raw','source_id','quarter','known_at'],r))
            for r in c.execute('''SELECT a.assignment_id,a.subsector_id,d.label_raw,a.source_id,s.quarter,a.known_at
            FROM ss_assignments a JOIN ss_sources s USING(source_id) JOIN ss_subsectors d USING(subsector_id)
            WHERE a.company_id=? AND s.quarter IN ('FY2026-Q4','FY2027-Q1') ORDER BY a.assignment_id''',(cid,))]


def baseline(c):
    # Fingerprint pre-Q2 identity and history, excluding this addendum's source rows.
    result={}
    for table in base.TABLES:
        result[table]=c.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
    result['prior_evidence']=c.execute('''SELECT r.*,s.sha256,a.assignment_id,a.subsector_id,a.known_at
        FROM ss_rows r JOIN ss_sources s USING(source_id)
        LEFT JOIN ss_assignment_evidence ae USING(row_id) LEFT JOIN ss_assignments a USING(assignment_id)
        WHERE s.quarter IN ('FY2026-Q4','FY2027-Q1') ORDER BY r.row_id''').fetchall()
    return base.digest(result)


def make_plan(path=SOURCE,target=base.TARGET):
    source,rows=parse(path)
    with base.readonly(target) as c:
        fingerprint=baseline(c)
        for row in rows:
            row.update(identity(row,c))
        grouped=collections.defaultdict(list)
        for row in rows:
            key=f"company:{row['company_id']}" if row['company_id'] is not None else 'unresolved_label:'+row['fields']['Stock'].strip().casefold()
            grouped[key].append(row)
        entries=[]
        for key,rr in sorted(grouped.items()):
            cid=rr[0]['company_id']
            evidence=prior(c,cid)
            labels={p['subsector_id'] for p in evidence}
            state=('identity_review' if cid is None else 'verified_prior_assignment' if len(labels)==1
                   else 'conflicting_prior_assignments' if labels else 'missing_prior_assignment')
            judgments={tuple(r['fields'][f] for f in ['Q2 Results','Rating','Guidance']) for r in rr}
            entries.append(dict(entry_id=base.digest([source['source_id'],key]),identity_key=key,company_id=cid,
                 display_stock_raw=rr[0]['fields']['Stock'],rows=[r['row_id'] for r in rr],
                 assessment_state='single' if len(rr)==1 else 'consistent_duplicate' if len(judgments)==1 else 'conflicting_duplicate',
                 subsector_state=state,subsector_id=next(iter(labels)) if len(labels)==1 else None,
                 prior_assignments=evidence))
    return dict(version='SS-01-v2',source=source,rows=rows,entries=entries,baseline_sha256=fingerprint,
                migration_sha256=base.digest(MIGRATION.read_bytes()),module_sha256=base.digest(Path(__file__).read_bytes()),
                duplicate_policy='one_entry_conflicts_require_review')


def migrate(c):
    # Check before executing any schema writes.
    for version,path in [('001',base.MIGRATION),('002',MIGRATION)]:
        row=c.execute('SELECT sha256 FROM ss_migrations WHERE version=?',(version,)).fetchone()
        if row and row[0]!=base.digest(path.read_bytes()):
            raise ValueError('Research migration checksum mismatch: '+version)
    c.executescript(MIGRATION.read_text())
    c.execute('INSERT OR IGNORE INTO ss_migrations VALUES(?,?)',('002',base.digest(MIGRATION.read_bytes())))
    c.commit()


def load(c,plan):
    source=plan['source']
    content=Path(source['source_path']).read_bytes()
    if base.digest(content)!=source['sha256'] or baseline(c)!=plan['baseline_sha256']:
        raise ValueError('Source or Q4/Q1 baseline changed after dry-run')
    batch=c.execute('SELECT plan_sha256 FROM ss_import_batches WHERE source_id=?',(source['source_id'],)).fetchone()
    if batch and batch[0]!=base.digest(plan):
        raise ValueError('Existing source snapshot uses a different audited plan; explicit rollback required')
    with c:
        cols=list(source)
        c.execute(f"INSERT OR IGNORE INTO ss_sources({','.join(cols)},content) VALUES({','.join('?' for _ in range(len(cols)+1))})",
                  [source[k] for k in cols]+[content])
        for row in plan['rows']:
            cols=['row_id','source_id','row_ref','raw_text','symbol_raw','exchange_raw','company_name_raw','label_raw','company_id','resolution_state']
            c.execute(f"INSERT OR IGNORE INTO ss_rows({','.join(cols)},resolution_json) VALUES({','.join('?' for _ in range(len(cols)+1))})",
                      [row[k] for k in cols]+[base.encoded(row['resolution'])])
            cid=row['company_id']
            if cid is not None:
                c.execute('INSERT OR IGNORE INTO ss_memberships VALUES(?,?)',(cid,QUARTER))
                c.execute('INSERT OR IGNORE INTO ss_membership_evidence VALUES(?,?,?)',(row['row_id'],cid,QUARTER))
            c.execute('INSERT OR IGNORE INTO ss_mentor_evidence VALUES(?,"focus_reference",NULL)',(row['row_id'],))
            f=row['fields']
            c.execute('INSERT OR IGNORE INTO ss_mentor_assessments VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL)',
                      (row['row_id'],cid,QUARTER,row['original_row_number'],f['Sl No'],f['Stock'],f['Q2 Results'],f['Rating'],f['Guidance'],f['Extraction Note']))
        for entry in plan['entries']:
            eid=entry['entry_id']
            c.execute('INSERT OR IGNORE INTO ss_focus_entries VALUES(?,?,?,?,?,?,?)',
                      (eid,source['source_id'],QUARTER,entry['company_id'],entry['identity_key'],entry['display_stock_raw'],entry['assessment_state']))
            for rid in entry['rows']:
                c.execute('INSERT OR IGNORE INTO ss_focus_entry_rows VALUES(?,?)',(rid,eid))
            c.execute('INSERT OR IGNORE INTO ss_q2_subsector_status VALUES(?,?,?,NULL)',
                      (eid,entry['subsector_state'],entry['subsector_id']))
            for evidence in entry['prior_assignments']:
                c.execute('INSERT OR IGNORE INTO ss_inherited_subsector_evidence VALUES(?,?)',(eid,evidence['assignment_id']))
        c.execute('INSERT OR IGNORE INTO ss_import_batches VALUES(?,?,?,NULL,?)',
                  (source['source_id'],base.digest(plan),datetime.now(timezone.utc).isoformat(),plan['duplicate_policy']))


def table_counts(c):
    return {t:c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")}


def rollback(c,source_id):
    """Remove only one addendum source; retain migration schema and all earlier history."""
    source=c.execute('SELECT format,quarter FROM ss_sources WHERE source_id=?',(source_id,)).fetchone()
    if source is None:
        return
    if source!=('q2_assessment_csv',QUARTER):
        raise ValueError('Rollback only accepts a Q2 addendum source')
    with c:
        # Future evidence links restrict deletion instead of being silently destroyed.
        c.execute('DELETE FROM ss_mentor_assessments WHERE row_id IN (SELECT row_id FROM ss_rows WHERE source_id=?)',(source_id,))
        for table in ['ss_inherited_subsector_evidence','ss_q2_subsector_status','ss_focus_entry_rows']:
            c.execute(f'DELETE FROM {table} WHERE entry_id IN (SELECT entry_id FROM ss_focus_entries WHERE source_id=?)',(source_id,))
        c.execute('DELETE FROM ss_focus_entries WHERE source_id=?',(source_id,))
        for table in ['ss_membership_evidence','ss_mentor_evidence']:
            c.execute(f'DELETE FROM {table} WHERE row_id IN (SELECT row_id FROM ss_rows WHERE source_id=?)',(source_id,))
        c.execute('DELETE FROM ss_import_batches WHERE source_id=?',(source_id,))
        c.execute('DELETE FROM ss_rows WHERE source_id=?',(source_id,))
        c.execute('DELETE FROM ss_sources WHERE source_id=?',(source_id,))
        c.execute('''DELETE FROM ss_memberships WHERE quarter=? AND NOT EXISTS (
            SELECT 1 FROM ss_membership_evidence e WHERE e.company_id=ss_memberships.company_id AND e.quarter=ss_memberships.quarter)''',(QUARTER,))


def safe_connect():
    # Reuse the one authorized SS-01 destination. Do not create arbitrary DBs.
    if base.TARGET.is_symlink() or not base.TARGET.is_file():
        raise ValueError('Existing isolated SS-01 database required')
    with base.readonly(base.TARGET) as c:
        if c.execute("SELECT value FROM ss_metadata WHERE key='purpose'").fetchone()!=('SS-01 isolated research',):
            raise ValueError('Research purpose marker missing')
    c=sqlite3.connect(base.TARGET)
    c.execute('PRAGMA foreign_keys=ON')
    return c


def main(mode):
    OUT.mkdir(parents=True,exist_ok=True)
    audit=OUT/'dry-run.json'
    if mode=='rollback':
        if not audit.exists():
            raise ValueError('Original Q2 audit required for source-specific rollback')
        recorded=json.loads(audit.read_text())
        c=safe_connect()
        try:
            rollback(c,recorded['source']['source_id'])
            print('Q2 source removed; Q4/Q1 and migration schema retained')
        finally:
            c.close()
        return
    plan=make_plan()
    if mode=='dry-run':
        audit.write_text(json.dumps(plan,indent=2,ensure_ascii=False)+'\n')
        print(base.encoded(dict(rows=len(plan['rows']),deduplicated_entries=len(plan['entries']),
                               resolved_rows=sum(r['company_id'] is not None for r in plan['rows']),
                               subsector_coverage=dict(collections.Counter(e['subsector_state'] for e in plan['entries'])))))
        return
    if not audit.exists() or json.loads(audit.read_text())!=plan:
        raise ValueError('Matching Q2 dry-run required')
    c=safe_connect()
    try:
        migrate(c)
        before=baseline(c)
        load(c,plan)
        first=table_counts(c)
        load(c,plan)
        assert first==table_counts(c)
        assert before==baseline(c)
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        # Exercise rollback and reapply on a disposable clone, never discard live research evidence.
        clone=sqlite3.connect(':memory:');c.backup(clone);clone.execute('PRAGMA foreign_keys=ON')
        rollback(clone,plan['source']['source_id'])
        assert baseline(clone)==before
        assert clone.execute('SELECT count(*) FROM ss_mentor_assessments').fetchone()[0]==0
        load(clone,plan)
        assert table_counts(clone)==first
        clone.close()
        result=dict(table_counts=first,source_rows=len(plan['rows']),deduplicated_entries=len(plan['entries']),
                    integrity_check='ok',foreign_key_violations=0,idempotent='passed',rollback_reapply='passed',
                    prior_history_preserved=True)
        (OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
        print(base.encoded(result))
    finally:
        c.close()
