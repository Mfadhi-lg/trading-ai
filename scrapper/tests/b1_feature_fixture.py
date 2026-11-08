"""Retained B0 evidence fixture; reconstructed fields are exploratory."""
import csv, sqlite3
from pathlib import Path
B0 = Path(__file__).parent / "fixtures/research"
MIGRATIONS = Path(__file__).parents[1] / "db/migrations"
MAPPING = {
 'revenue': ('quarters','sales','Sales +'),
 'operating_profit': ('quarters','operating_profit','Operating Profit'),
 'operating_margin': ('quarters','operating_margin_pct','OPM %'),
 'financing_profit': ('quarters',None,'Financing Profit'),
 'financing_margin': ('quarters',None,'Financing Margin %'),
 'pbt': ('quarters','profit_before_tax','Profit before tax'),
 'pat': ('quarters','net_profit','Net Profit +'),
 'eps': ('quarters','eps_inr','EPS in Rs'),
 'borrowings_annual': ('balance_sheet','borrowings','Borrowings +'),
 'deposits_annual': ('balance_sheet',None,'Deposits'),
 'cfo_annual': ('cash_flow','cash_from_operating_activity','Cash from Operating Activity +'),
 'fcf_annual': ('cash_flow','free_cash_flow','Free Cash Flow'),
}


def insert(conn, table, row):
    names = ','.join(row)
    conn.execute(f'INSERT INTO {table} ({names}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))


def fixture():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    for migration in ('001_initial.sql', '003_availability_acceptance_periods.sql', '004_growth_observations.sql'):
        conn.executescript((MIGRATIONS / migration).read_text())
    with (B0 / 'B0_SAMPLE_CHECK.csv').open(newline='') as f:
        samples = list(csv.DictReader(f))
    with (B0 / 'B0_FIELD_AUDIT.csv').open(newline='') as f:
        fields = list(csv.DictReader(f))
    with (B0 / 'B0_PIT_CLASSIFICATION.csv').open(newline='') as f:
        pit = {r['observation_id']: r for r in csv.DictReader(f)}
    for s in samples:
        cid, pid, bid = (int(s[k]) for k in ('company_id','parse_id','blob_id'))
        insert(conn,'companies',dict(company_id=cid,current_legal_name=s['company']))
        insert(conn,'response_blobs',dict(blob_id=bid,content_sha256=s['blob_sha256'],byte_size=0,
               local_path=s['stored_blob_path'],first_observed_at_utc=s['first_observed_at_utc'],created_at_utc=s['first_observed_at_utc']))
        insert(conn,'parse_artifacts',dict(parse_id=pid,blob_id=bid,parser_name=s['parser_name'],parser_version=s['parser_version'],
               manifest_version='retained_export_fixture',parsed_at_utc=s['accepted_at_utc'],parse_status='ok',validation_status=s['validation_status']))
        accepted=dict(company_id=cid,source='screener',reporting_scope='standalone',resolved_source_period=s['source_period'],
                      parse_id=pid,accepted_at_utc=s['accepted_at_utc'],accepted_by='retained_export_fixture',acceptance_mode='retained_export_fixture')
        insert(conn,'accepted_page_versions',accepted)
        insert(conn,'acceptance_events',dict(accepted,action='accepted'))
        for index,f in enumerate(r for r in fields if r['company_id']==s['company_id'] and r['observation_id']):
            base=f['field'].replace('_prev_q','').replace('_prev_y','')
            section,code,label=MAPPING[base]
            if s['company_type']=='bank_or_nbfc' and base=='revenue': code,label=None,'Revenue +'
            if s['company_type']=='bank_or_nbfc' and base=='borrowings_annual': code,label=None,'Borrowing'
            p=pit[f['observation_id']]
            insert(conn,'financial_observations',dict(financial_observation_id=int(f['observation_id']),parse_id=pid,reporting_scope='standalone',
                   section_code=section,line_item_code=code,line_item_raw=label,row_position=index,column_position=0,
                   period_label_raw=f['period_end'],period_end=f['period_end'],period_type=f['period_type'],
                   first_observed_at_utc=p['first_observed_at_utc'],source_published_at_utc=p['source_published_at_utc'] or None,
                   available_at_utc=p['first_observed_at_utc'],availability_basis=p['availability_basis'],
                   display_value=f['display'],value_decimal=f['value'] or None,unit_code=f['unit'],
                   scale_multiplier='10000000' if f['unit']=='INR_CRORE' else '1',missing_kind=None))
    conn.commit()
    conn.execute('PRAGMA query_only=ON')
    return conn,samples,fields

