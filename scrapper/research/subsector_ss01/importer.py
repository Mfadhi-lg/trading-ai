"""Deterministic, quarantining importer; stdlib only. See README for CLI."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/subsector-ss01/v1'
TARGET = OUT / 'research.sqlite3'
MASTER = ROOT / 'artifacts/acquisition-readiness/AR-01.3C/attempt-02-post-canary/scraper.db'
Q4 = Path('/Users/mohamedfadhi/Downloads/2026 q4.csv')
Q1 = ROOT / 'artifacts/b2-2026-10-01/Q1 2027 FOCUS LIST.txt'
MIGRATION = Path(__file__).parent / 'migrations/001_research_classifications.sql'
TABLES = ('companies', 'security_identifiers', 'source_identifiers')


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value).encode()).hexdigest()


def readonly(path):
    c = sqlite3.connect('file:' + quote(str(Path(path).resolve()), safe='/') + '?mode=ro', uri=True)
    c.execute('PRAGMA query_only=ON')
    return c


def master_snapshot(path):
    """Read canonical IDs without running runtime migrations or copying acquisition data."""
    with readonly(path) as c:
        tables = {}
        for table in TABLES:
            tables[table] = {'sql': c.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0],
                             'rows': c.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()}
        # BSE mnemonic security_id lives in the version attributes, not the numeric BSE code.
        bse = c.execute('''SELECT DISTINCT r.company_id,v.attributes_json,s.exchange_security_id,v.version_id,
                           v.valid_from_date,v.valid_to_date
            FROM exchange_securities s JOIN exchange_security_versions v USING(exchange_security_id)
            JOIN exchange_company_links l USING(exchange_security_id)
            JOIN exchange_company_registry r USING(registry_company_id)
            WHERE s.exchange='BSE' AND r.company_id IS NOT NULL''').fetchall()
        aliases = []
        for cid, attributes, security_id, version_id, start, end in bse:
            symbol = json.loads(attributes).get('security_id')
            if symbol:
                aliases.append((cid, symbol, security_id, version_id, start, end))
        aliases.sort()
        sectors = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%sector%' OR name LIKE '%industr%')").fetchall()
    snapshot = {'tables': tables, 'bse_aliases': aliases, 'existing_sector_tables': sectors}
    return snapshot


def parse_source(path, fmt, quarter, period_end):
    content = Path(path).read_bytes()
    source = dict(source_id=digest([str(Path(path).resolve()), digest(content)]),
                  source_path=str(Path(path).resolve()), sha256=digest(content), format=fmt,
                  quarter=quarter, period_end=period_end, known_at=None,
                  date_basis='quarter supplied by owner; classification availability date unknown')
    rows = []
    decoded = content.decode('utf-8-sig')
    if fmt == 'csv':
        reader = csv.DictReader(io.StringIO(decoded, newline=''))
        if reader.fieldnames != ['Symbol', 'Company Name', 'Category']:
            raise ValueError('Unexpected Q4 CSV header')
        lines = decoded.splitlines(keepends=True)
        previous = 1
        for item in reader:
            if None in item or any(v is None for v in item.values()):
                raise ValueError('Malformed CSV row')
            ref = f'lines:{previous + 1}-{reader.line_num}'
            rows.append(dict(row_ref=ref, raw_text=''.join(lines[previous:reader.line_num]),
                             symbol_raw=item['Symbol'], exchange_raw=None,
                             company_name_raw=item['Company Name'], label_raw=item['Category']))
            previous = reader.line_num
    else:
        label = None
        for position, token in enumerate(decoded.split(','), 1):
            stripped = token.strip()
            if stripped.startswith('###'):
                label = stripped[3:]
            elif stripped:
                if ':' not in stripped:
                    raise ValueError(f'Unexpected TXT token {position}')
                exchange, symbol = stripped.split(':', 1)
                if exchange not in ('NSE', 'BSE') or not symbol:
                    raise ValueError(f'Unexpected exchange/symbol {position}')
                rows.append(dict(row_ref=f'token:{position}', raw_text=token,
                                 symbol_raw=symbol, exchange_raw=exchange,
                                 company_name_raw=None, label_raw=label))
    for row in rows:
        row.update(row_id=digest([source['source_id'], row['row_ref']]), source_id=source['source_id'])
    return source, rows


def resolve(row, snapshot):
    """No fuzzy names. Union competing exact identities; never first-match wins."""
    symbol = row['symbol_raw'].strip()
    exchange = row['exchange_raw']
    proof = []
    for rec in snapshot['tables']['security_identifiers']['rows']:
        if rec[3] == symbol and (exchange is None or rec[2] == exchange):
            proof.append(dict(company_id=rec[1], basis='security_identifiers', record=list(rec)))
    for rec in snapshot['tables']['source_identifiers']['rows']:
        if rec[4] == symbol and rec[7] == 'verified':
            proof.append(dict(company_id=rec[1], basis='verified_source_symbol_exchange_not_asserted', record=list(rec)))
    for rec in snapshot['bse_aliases']:
        if rec[1] == symbol and exchange in (None, 'BSE'):
            proof.append(dict(company_id=rec[0], basis='BSE_security_id_version', record=list(rec)))
    # Explicit TradingView underscore/hyphen alias, retained in the audit.
    if '_' in symbol:
        alias = symbol.replace('_', '-')
        for rec in snapshot['tables']['security_identifiers']['rows']:
            if rec[3] == alias and (exchange is None or rec[2] == exchange):
                proof.append(dict(company_id=rec[1], basis='underscore_to_hyphen_alias', record=list(rec)))
    ids = sorted({p['company_id'] for p in proof})
    state = 'resolved' if len(ids) == 1 else 'ambiguous' if ids else 'unresolved'
    # Suggestions for manual review are never assignments.
    suggestions = []
    if not ids and row['company_name_raw']:
        name = row['company_name_raw'].casefold().replace('limited', 'ltd').replace('.', '').strip()
        suggestions = [r[0] for r in snapshot['tables']['companies']['rows']
                       if r[1].casefold().replace('limited', 'ltd').replace('.', '').strip() == name]
    return dict(company_id=ids[0] if state == 'resolved' else None, resolution_state=state,
                resolution=dict(candidates=ids, proof=proof, review_name_suggestions=suggestions,
                                historical_symbol_validity='not established at quarter end'))


def make_plan(master=MASTER, q4=Q4, q1=Q1):
    snapshot = master_snapshot(master)
    sources, rows = [], []
    for path, fmt, quarter, end in [(q4, 'csv', 'FY2026-Q4', '2026-03-31'),
                                    (q1, 'tradingview_txt', 'FY2027-Q1', '2026-06-30')]:
        source, source_rows = parse_source(path, fmt, quarter, end)
        sources.append(source)
        for row in source_rows:
            row.update(resolve(row, snapshot))
            rows.append(row)
    return dict(version='SS-01-v1', master_path=str(Path(master).resolve()),
                master_logical_sha256=digest(snapshot), migration_sha256=digest(MIGRATION.read_bytes()),
                importer_sha256=digest(Path(__file__).read_bytes()),
                sources=sources, rows=rows), snapshot


def migrate(c):
    sql = MIGRATION.read_text()
    c.executescript(sql)
    old = c.execute('SELECT sha256 FROM ss_migrations WHERE version=?', ('001',)).fetchone()
    checksum = digest(sql.encode())
    if old and old[0] != checksum:
        raise ValueError('Migration checksum changed')
    c.execute('INSERT OR IGNORE INTO ss_migrations VALUES(?,?)', ('001', checksum))
    c.commit()


def import_rows(c, plan):
    """Atomic append of source versions. Unresolved rows stay in evidence quarantine."""
    with c:
        for source in plan['sources']:
            content = Path(source['source_path']).read_bytes()
            if digest(content) != source['sha256']:
                raise ValueError('Source changed after dry run')
            cols = list(source)
            c.execute(f"INSERT OR IGNORE INTO ss_sources({','.join(cols)},content) VALUES({','.join('?' for _ in range(len(cols)+1))})",
                      [source[k] for k in cols] + [content])
        by_source = {s['source_id']: s for s in plan['sources']}
        for row in plan['rows']:
            cols = ['row_id','source_id','row_ref','raw_text','symbol_raw','exchange_raw',
                    'company_name_raw','label_raw','company_id','resolution_state']
            c.execute(f"INSERT OR IGNORE INTO ss_rows({','.join(cols)},resolution_json) VALUES({','.join('?' for _ in range(len(cols)+1))})",
                      [row[k] for k in cols] + [encoded(row['resolution'])])
            source = by_source[row['source_id']]
            c.execute('INSERT OR IGNORE INTO ss_mentor_evidence VALUES(?,?,NULL)',
                      (row['row_id'], 'focus_reference' if source['format']=='tradingview_txt' else 'unknown'))
            sid = None
            if row['label_raw'] and row['label_raw'].strip():
                label = row['label_raw']
                sid = digest(['source_category', label])
                c.execute('INSERT OR IGNORE INTO ss_subsectors VALUES(?,?,?,?,NULL)',
                          (sid,label,' '.join(label.split()).casefold(),'source_category'))
            if row['resolution_state'] != 'resolved':
                continue
            cid, quarter = row['company_id'], source['quarter']
            c.execute('INSERT OR IGNORE INTO ss_memberships VALUES(?,?)', (cid,quarter))
            c.execute('INSERT OR IGNORE INTO ss_membership_evidence VALUES(?,?,?)', (row['row_id'],cid,quarter))
            if sid:
                aid = digest([source['source_id'],cid,sid])
                c.execute('INSERT OR IGNORE INTO ss_assignments VALUES(?,?,?,?,NULL,NULL,NULL)', (aid,source['source_id'],cid,sid))
                c.execute('INSERT OR IGNORE INTO ss_assignment_evidence VALUES(?,?)',(row['row_id'],aid))


def counts(c):
    return {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in
            (*TABLES,'ss_sources','ss_rows','ss_subsectors','ss_memberships',
             'ss_membership_evidence','ss_assignments','ss_assignment_evidence','ss_mentor_evidence')}


def validate(c, plan):
    assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    for source in plan['sources']:
        content = c.execute('SELECT content FROM ss_sources WHERE source_id=?', (source['source_id'],)).fetchone()[0]
        assert digest(content) == source['sha256']
        _, parsed = parse_source(source['source_path'],source['format'],source['quarter'],source['period_end'])
        expected = [(r['row_id'],r['row_ref'],r['raw_text'],r['symbol_raw'],r['label_raw']) for r in parsed]
        actual = c.execute('SELECT row_id,row_ref,raw_text,symbol_raw,label_raw FROM ss_rows WHERE source_id=? ORDER BY row_ref', (source['source_id'],)).fetchall()
        assert sorted(expected) == sorted(actual)
    for row in plan['rows']:
        expected = int(row['resolution_state']=='resolved' and bool(row['label_raw'] and row['label_raw'].strip()))
        assert c.execute('SELECT count(*) FROM ss_assignment_evidence WHERE row_id=?',(row['row_id'],)).fetchone()[0] == expected
    assert not c.execute('''SELECT ae.row_id FROM ss_assignments a JOIN ss_assignment_evidence ae USING(assignment_id) JOIN ss_rows r USING(row_id)
                           WHERE a.company_id != r.company_id''').fetchall()
    before = counts(c)
    import_rows(c,plan)
    assert counts(c) == before
    return dict(table_counts=before, foreign_key_violations=0, integrity_check='ok',
                source_row_roundtrip='passed', idempotent_rerun='passed',
                both_quarters=c.execute('''SELECT count(*) FROM (SELECT company_id FROM ss_memberships
                    WHERE quarter IN ('FY2026-Q4','FY2027-Q1')
                    GROUP BY company_id HAVING count(DISTINCT quarter)=2)''').fetchone()[0],
                missing_classifications=[r['row_id'] for r in plan['rows'] if not r['label_raw'] or not r['label_raw'].strip()],
                unresolved=[dict(source_id=r['source_id'],row_ref=r['row_ref'],symbol=r['symbol_raw'],
                                 state=r['resolution_state'],resolution=r['resolution'])
                            for r in plan['rows'] if r['resolution_state']!='resolved'])


def apply(plan, snapshot, target=TARGET):
    # This CLI authorizes one isolated research destination only, never an arbitrary DB.
    if Path(target).resolve() != TARGET.resolve() or Path(target).is_symlink():
        raise ValueError('Only the dedicated SS-01 research destination is writable')
    if digest(snapshot) != plan['master_logical_sha256']:
        raise ValueError('Identity snapshot changed')
    if target.exists():
        with readonly(target) as existing:
            marker = existing.execute('SELECT value FROM ss_metadata WHERE key="purpose"').fetchone()
            if marker != ('SS-01 isolated research',):
                raise ValueError('Not an SS-01 research database')
            recorded = existing.execute('SELECT value FROM ss_metadata WHERE key="master_logical_sha256"').fetchone()
            if recorded != (plan['master_logical_sha256'],):
                raise ValueError('Research master identity changed')
    c = sqlite3.connect(target)
    c.execute('PRAGMA foreign_keys=ON')
    try:
        c.execute('CREATE TABLE IF NOT EXISTS ss_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        with c:
            c.execute('INSERT OR IGNORE INTO ss_metadata VALUES("purpose","SS-01 isolated research")')
            c.execute('INSERT OR IGNORE INTO ss_metadata VALUES("master_logical_sha256",?)', (plan['master_logical_sha256'],))
            for table, data in snapshot['tables'].items():
                if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                    c.execute(data['sql'])
                    if data['rows']:
                        c.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in data['rows'][0])})",data['rows'])
        migrate(c)
        import_rows(c,plan)
        result = validate(c,plan)
        for table,data in snapshot['tables'].items():
            assert c.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() == [tuple(r) for r in data['rows']]
        return result
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['dry-run','apply','q2-dry-run','q2-apply','q2-rollback'])
    args = parser.parse_args()
    if args.mode.startswith('q2-'):
        from . import q2
        q2.main(args.mode[3:])
        return
    OUT.mkdir(parents=True,exist_ok=True)
    plan,snapshot = make_plan()
    audit = OUT / 'dry-run.json'
    if args.mode == 'dry-run':
        audit.write_text(json.dumps(plan,indent=2,ensure_ascii=False)+'\n')
        (OUT/'canonical-identity-snapshot.json').write_text(encoded(snapshot)+'\n')
        print(encoded(dict(rows=len(plan['rows']), resolved=sum(r['resolution_state']=='resolved' for r in plan['rows']),
                           audit=str(audit), plan_sha256=digest(plan))))
    else:
        if not audit.exists() or json.loads(audit.read_text()) != plan:
            raise ValueError('Run dry-run first; sources, migration and master must match reviewed audit')
        result = apply(plan,snapshot)
        (OUT/'validation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
        print(encoded(result))


if __name__ == '__main__':
    main()
