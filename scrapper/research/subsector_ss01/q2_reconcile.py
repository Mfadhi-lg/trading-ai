"""Independent Q2 source reconciliation: no calls to importer parsing or matching."""
import collections
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'artifacts/subsector-ss01/v2'
TARGET=ROOT/'artifacts/subsector-ss01/v1/research.sqlite3'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    c=sqlite3.connect(f'file:{TARGET}?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    source=dict(c.execute("SELECT * FROM ss_sources WHERE format='q2_assessment_csv'").fetchone())
    data=Path(source['source_path']).read_bytes()
    assert data==source['content'] and sha(data)==source['sha256']
    # Independent row parsing and exact per-field reconciliation (including empty strings).
    text=data.decode('utf-8-sig');lines=text.splitlines(keepends=True)
    reader=csv.DictReader(io.StringIO(text,newline=''))
    fields=reader.fieldnames
    last=1;original=[]
    for ordinal,item in enumerate(reader,1):
        ref=f'lines:{last+1}-{reader.line_num}'
        raw=''.join(lines[last:reader.line_num]);last=reader.line_num
        actual=c.execute('''SELECT a.*,r.raw_text,r.row_ref FROM ss_mentor_assessments a
             JOIN ss_rows r USING(row_id) WHERE r.source_id=? AND a.original_row_number=?''',(source['source_id'],ordinal)).fetchone()
        assert actual is not None and actual['raw_text']==raw and actual['row_ref']==ref
        for name,col in [('Sl No','sl_no_raw'),('Stock','stock_raw'),('Q2 Results','q2_results_raw'),
                         ('Rating','rating_raw'),('Guidance','guidance_raw'),('Extraction Note','extraction_note_raw')]:
            assert item[name]==actual[col],(ordinal,name)
        assert actual['decision_at'] is None and actual['available_at'] is None
        original.append(item)
    assert len(original)==c.execute('SELECT count(*) FROM ss_mentor_assessments').fetchone()[0]
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    # Verify all earlier tables row-for-row against the pre-Q2 backup, not only counts.
    old=sqlite3.connect(f"file:{OUT/'pre-q2.sqlite3'}?mode=ro",uri=True)
    for table in ['companies','security_identifiers','source_identifiers','ss_subsectors',
                  'ss_assignments','ss_assignment_evidence']:
        assert [tuple(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1')]==old.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall(),table
    for table in ['ss_sources','ss_rows','ss_mentor_evidence','ss_membership_evidence']:
        for row in old.execute(f'SELECT * FROM {table}'):
            current=c.execute(f'SELECT * FROM {table} WHERE {"source_id" if table=="ss_sources" else "row_id"}=?',(row[0],)).fetchone()
            assert tuple(current)==row,table
    assert [tuple(r) for r in c.execute("SELECT company_id,quarter FROM ss_memberships WHERE quarter!='FY2027-Q2' ORDER BY 1,2")]==old.execute('SELECT company_id,quarter FROM ss_memberships ORDER BY 1,2').fetchall()
    old.close()
    # Q2 linkage: matching identity, quarter and source snapshot on all row evidence.
    assert c.execute('''SELECT count(*) FROM ss_mentor_assessments a JOIN ss_rows r USING(row_id)
        JOIN ss_sources s USING(source_id) WHERE a.company_id IS NOT r.company_id OR a.quarter!=s.quarter''').fetchone()[0]==0
    assert c.execute('''SELECT count(*) FROM ss_focus_entries e JOIN ss_focus_entry_rows er USING(entry_id)
        JOIN ss_rows r USING(row_id) WHERE e.company_id IS NOT r.company_id OR e.source_id!=r.source_id''').fetchone()[0]==0
    assert c.execute('''SELECT count(*) FROM ss_inherited_subsector_evidence ie JOIN ss_focus_entries e USING(entry_id)
        JOIN ss_assignments a ON a.assignment_id=ie.prior_assignment_id JOIN ss_sources s ON s.source_id=a.source_id
        WHERE a.company_id!=e.company_id OR s.quarter NOT IN ('FY2026-Q4','FY2027-Q1')''').fetchone()[0]==0
    entries=[dict(r) for r in c.execute('SELECT * FROM ss_q2_focus ORDER BY display_stock_raw')]
    for e in entries:
        rr=c.execute('''SELECT a.q2_results_raw,a.rating_raw,a.guidance_raw FROM ss_mentor_assessments a
                       JOIN ss_focus_entry_rows er USING(row_id) WHERE er.entry_id=?''',(e['entry_id'],)).fetchall()
        if len({tuple(r) for r in rr})>1:
            assert e['assessment_state']=='conflicting_duplicate'
            assert e['q2_results_raw'] is None and e['rating_raw'] is None and e['guidance_raw'] is None
        prior_ids={r[0] for r in c.execute('''SELECT a.subsector_id FROM ss_inherited_subsector_evidence ie
                   JOIN ss_assignments a ON a.assignment_id=ie.prior_assignment_id WHERE ie.entry_id=?''',(e['entry_id'],))}
        if e['subsector_state']=='verified_prior_assignment':
            assert len(prior_ids)==1
        elif e['subsector_state']=='conflicting_prior_assignments':
            assert len(prior_ids)>1 and e['subsector_raw'] is None
    # Empty factual tables ensure mentor Rating was never promoted to a verified agency rating.
    for t in ['ss_documents','ss_extracted_facts','ss_fact_citations','ss_assessment_fact_links']:
        assert c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]==0
    counts={t:c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")}
    resolved=c.execute('SELECT count(*) FROM ss_mentor_assessments WHERE company_id IS NOT NULL').fetchone()[0]
    unique=c.execute('SELECT count(DISTINCT company_id) FROM ss_mentor_assessments').fetchone()[0]
    coverage=dict(collections.Counter(e['subsector_state'] for e in entries))
    states=dict(c.execute("SELECT resolution_state,count(*) FROM ss_rows WHERE source_id=? GROUP BY 1",(source['source_id'],)).fetchall())
    duplicates=[]
    for name,n in collections.Counter(r['Stock'] for r in original).items():
        if n>1:
            duplicates.append(dict(stock=name,count=n,original_sl_no=[r['Sl No'] for r in original if r['Stock']==name],
                                   action='one entry; conflicting assessments require review'))
    preserved_fields={f:dict(collections.Counter(r[f] for r in original)) for f in fields}
    imported_quarters=dict(c.execute('SELECT quarter,count(*) FROM ss_rows JOIN ss_sources USING(source_id) GROUP BY quarter').fetchall())
    result=dict(version='SS-01-v2',source_path=source['source_path'],source_sha256=source['sha256'],
        source_snapshot_version=source['source_id'],imported_quarters=imported_quarters,
        q2_source_rows=len(original),q2_deduplicated_entries=len(entries),verified_rows=resolved,
        unique_verified_companies=unique,identity_states=states,subsector_coverage=coverage,
        duplicates=duplicates,preserved_field_values=preserved_fields,
        exact_duplicate_source_records=len(original)-len({tuple(r[f] for f in fields) for r in original}),
        duplicate_stock_excess_rows=len(original)-len({r['Stock'] for r in original}),
        missing_assessments={f:sum(r[f]=='' for r in original) for f in ['Q2 Results','Rating','Guidance','Extraction Note']},
        source_traceability_rows=len(original),mentor_decision_timestamp_known=0,assessment_available_timestamp_known=0,
        independent_validation='passed',prior_tables_exact_match=True,factual_extraction_rows=0,table_counts=counts)
    (OUT/'reconciliation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    (OUT/'final-table-counts.json').write_text(json.dumps(counts,indent=2)+'\n')
    for filename,rows in [('q2-deduplicated-focus.csv',entries),
            ('q2-original-assessment-fields.csv',original),
            ('q2-identity-review.csv',[dict(r) for r in c.execute("SELECT * FROM ss_q2_assessment_evidence WHERE resolution_state!='resolved' ORDER BY original_row_number")]),
            ('q2-subsector-review.csv',[dict(r) for r in c.execute("SELECT * FROM ss_q2_focus WHERE subsector_state!='verified_prior_assignment' ORDER BY display_stock_raw")])]:
        if rows:
            with (OUT/filename).open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    proof=json.loads((OUT/'preservation-before.json').read_text());after={}
    for path,expected in proof.items():
        p=Path(path);s=p.stat()
        observed=dict(size=s.st_size,mtime_ns=s.st_mtime_ns,sha256=sha(p.read_bytes()))
        assert observed==expected,path
        after[path]=observed
    (OUT/'preservation-after.json').write_text(json.dumps(after,indent=2)+'\n')
    c.close()
    print(json.dumps({k:result[k] for k in ['imported_quarters','q2_deduplicated_entries','verified_rows','unique_verified_companies','identity_states','subsector_coverage','missing_assessments','independent_validation']}))


if __name__=='__main__':
    main()
