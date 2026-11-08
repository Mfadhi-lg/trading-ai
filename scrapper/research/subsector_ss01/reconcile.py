"""Independent read-only verification and source reconciliation for the SS-01 delivery.

Uses CSV/TXT source parsing independently of the importer and checks stored evidence.
"""
import collections
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'artifacts/subsector-ss01/v1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def xlsx_rows(path):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(el.itertext()) for el in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
        result=[]
        for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//s:sheetData/s:row',ns):
            values=[]
            for cell in row.findall('s:c',ns):
                v=cell.find('s:v',ns)
                value=v.text if v is not None else ''.join(cell.find('s:is',ns).itertext()) if cell.find('s:is',ns) is not None else ''
                values.append(shared[int(value)] if cell.get('t')=='s' and value else value)
            result.append(values)
        return result


def main():
    c=sqlite3.connect(f'file:{OUT}/research.sqlite3?mode=ro',uri=True)
    if c.execute("SELECT 1 FROM ss_sources WHERE format='q2_assessment_csv' LIMIT 1").fetchone():
        c.close()
        from . import q2_reconcile
        q2_reconcile.main()
        return
    c.row_factory=sqlite3.Row
    sources=[dict(r) for r in c.execute('SELECT * FROM ss_sources ORDER BY quarter')]
    reports=[]
    primary_sets={}
    for source in sources:
        content=Path(source['source_path']).read_bytes()
        assert content==source['content']
        assert sha(content)==source['sha256']
        text=content.decode('utf-8-sig')
        expected=[]
        if source['format']=='csv':
            reader=csv.DictReader(io.StringIO(text,newline=''))
            lines=text.splitlines(keepends=True)
            last=1
            for r in reader:
                expected.append((f'lines:{last+1}-{reader.line_num}', ''.join(lines[last:reader.line_num]),
                                 r['Symbol'],None,r['Company Name'],r['Category']))
                last=reader.line_num
        else:
            label=None
            for position,token in enumerate(text.split(','),1):
                value=token.strip()
                if value.startswith('###'):
                    label=value[3:]
                elif value:
                    exchange,symbol=value.split(':',1)
                    expected.append((f'token:{position}',token,symbol,exchange,None,label))
        actual=[tuple(r) for r in c.execute('''SELECT row_ref,raw_text,symbol_raw,exchange_raw,company_name_raw,label_raw
                  FROM ss_rows WHERE source_id=?''',(source['source_id'],))]
        assert sorted(expected)==sorted(actual)
        counts=collections.Counter((r[3],r[2]) for r in expected)
        primary_sets[source['quarter']]={r[2] for r in expected}
        report={k:v for k,v in source.items() if k!='content'}
        report.update(rows=len(expected),unique_symbols=len(counts),labels=len({r[5] for r in expected}),
                      duplicate_symbols=[dict(exchange=k[0],symbol=k[1],count=n) for k,n in counts.items() if n>1],
                      missing_labels=sum(not r[5] or not r[5].strip() for r in expected),
                      resolved=c.execute('SELECT count(*) FROM ss_rows WHERE source_id=? AND company_id IS NOT NULL',(source['source_id'],)).fetchone()[0])
        reports.append(report)
    # All facts have consistent evidence, including both identity and source version.
    assert c.execute('''SELECT count(*) FROM ss_assignments a LEFT JOIN ss_assignment_evidence e USING(assignment_id)
                       WHERE e.row_id IS NULL''').fetchone()[0]==0
    assert c.execute('''SELECT count(*) FROM ss_assignment_evidence e JOIN ss_assignments a USING(assignment_id)
                       JOIN ss_rows r USING(row_id) JOIN ss_subsectors d USING(subsector_id)
                       WHERE a.company_id!=r.company_id OR a.source_id!=r.source_id OR d.label_raw!=r.label_raw''').fetchone()[0]==0
    assert c.execute('''SELECT count(*) FROM ss_membership_evidence e JOIN ss_rows r USING(row_id)
                       JOIN ss_sources s USING(source_id) WHERE e.company_id!=r.company_id OR e.quarter!=s.quarter''').fetchone()[0]==0
    assert c.execute('''SELECT count(*) FROM (SELECT source_id,company_id,subsector_id,count(*) n
                       FROM ss_assignments GROUP BY 1,2,3 HAVING n>1)''').fetchone()[0]==0
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('SELECT count(*) FROM ss_assignments WHERE known_at IS NOT NULL OR valid_from IS NOT NULL OR valid_to IS NOT NULL').fetchone()[0]==0
    unique=c.execute('SELECT count(DISTINCT company_id) FROM ss_memberships').fetchone()[0]
    both=c.execute('SELECT count(*) FROM (SELECT company_id FROM ss_memberships GROUP BY 1 HAVING count(*)=2)').fetchone()[0]
    # Canonical snapshot must agree exactly, independently of importer resolution.
    snapshot=json.loads((OUT/'canonical-identity-snapshot.json').read_text())
    for table,data in snapshot['tables'].items():
        assert [list(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1')]==data['rows']
    base=ROOT/'OLD/ML_PRO/DATA/RESULTS/2027/Q1/SFC/EXCEL-DATA'
    supporting=[]
    for name in ['stocks.csv','screener_focus_import.xlsx','screener_watchlist_ALL.txt',
                 'screener_watchlist_by_subsector.txt','rejected_candidates_names.txt',
                 'rejected_candidates.csv','screener-in-2026-08-03.csv']:
        p=base/name
        supporting.append(dict(path=str(p),sha256=sha(p.read_bytes()),bytes=p.stat().st_size,
                               authority='supporting only; no classifications or decisions imported'))
    stocks=list(csv.DictReader((base/'stocks.csv').open(encoding='utf-8-sig',newline='')))
    workbook=xlsx_rows(base/'screener_focus_import.xlsx')
    old_pairs={(r['Subsector'],r['Stock']) for r in stocks}
    symbol_col=workbook[0].index('NSE Code')
    subsector_col=workbook[0].index('Subsector')
    xlsx_pairs={(r[subsector_col],r[symbol_col]) for r in workbook[1:]
                if len(r)>max(symbol_col,subsector_col) and any(r)}
    # Report disagreement instead of asserting legacy claims are correct.
    legacy_symbols={r['Stock'] for r in stocks}
    raw_q1=primary_sets['FY2027-Q1']
    legacy_comparison=dict(stocks_rows=len(stocks),stocks_unique_symbols=len(legacy_symbols),
        workbook_data_rows=len(workbook)-1,workbook_header=workbook[0],
        csv_workbook_pairs_agree=old_pairs==xlsx_pairs,
        symbols_in_both_raw=len(raw_q1 & legacy_symbols),
        only_legacy_raw=sorted(legacy_symbols-raw_q1),only_supplied_q1_raw=sorted(raw_q1-legacy_symbols),
        note='Exact source symbols; underscore/hyphen variants remain distinct in this comparison.')
    for p in sorted((ROOT/'OLD/RESULTS/2026/Q4').glob('*.png')):
        supporting.append(dict(path=str(p),sha256=sha(p.read_bytes()),bytes=p.stat().st_size,
                               authority='original Q4 screenshot evidence; supplied CSV is import authority'))
    with (OUT/'unresolved-mappings.csv').open('w',newline='') as f:
        writer=csv.writer(f)
        writer.writerow(['quarter','source_path','row_ref','symbol','company_name','label_raw','review_name_suggestions','status'])
        for r in c.execute("SELECT * FROM ss_classification_evidence WHERE resolution_state!='resolved' ORDER BY quarter,row_ref"):
            writer.writerow([r['quarter'],r['source_path'],r['row_ref'],r['symbol_raw'],r['company_name_raw'],r['label_raw'],
                             json.loads(r['resolution_json'])['review_name_suggestions'],'REVIEW_REQUIRED; no company assignment'])
    conflicts=[dict(r) for r in c.execute('''SELECT company_id,group_concat(DISTINCT label_raw) labels
        FROM ss_classification_evidence WHERE company_id IS NOT NULL GROUP BY company_id
        HAVING count(DISTINCT label_raw)>1 ORDER BY company_id''')]
    preservation=json.loads((OUT/'preservation-before.json').read_text())
    after={}
    for p,expected in preservation.items():
        if p=='master_logical_sha256':
            continue
        path=Path(p);stat=path.stat()
        observed=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,
                      sha256=sha(path.read_bytes()) if stat.st_size<10000000 else None)
        assert observed==expected,(p,observed,expected)
        after[p]=observed
    result=dict(primary_sources=reports,supporting_inventory=supporting,legacy_q1_comparison=legacy_comparison,
                unique_resolved_companies=unique,both_quarters_resolved_companies=both,
                raw_symbol_quarter_overlap=len(primary_sets['FY2026-Q4'] & raw_q1),
                differing_labels_by_company=conflicts,independent_validation='passed',
                canonical_snapshot_exact_match=True,source_preservation='passed',
                missing_granular_subsectors='Not supplied: categories retained without inventing a finer taxonomy.',
                historical_known_at='Unknown for both supplied sources; no as-of-date inference permitted.')
    (OUT/'reconciliation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    (OUT/'preservation-after.json').write_text(json.dumps(after,indent=2)+'\n')
    print(json.dumps(dict(independent_validation='passed',unique_resolved_companies=unique,both_quarters=both,
                          supporting_files=len(supporting),legacy_q1=legacy_comparison),ensure_ascii=False))


if __name__=='__main__':
    main()
