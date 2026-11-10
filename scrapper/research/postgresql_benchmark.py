"""Synthetic issue #56 workloads; explicit disposable PostgreSQL 16 only.

Run as python -m scrapper.research.postgresql_benchmark --help. Never imports
application configuration, selects DATABASE_URL, or opens existing app schemas.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import time
import uuid

from scrapper.db.postgresql import PostgreSQLSettings, connect
from scrapper.db.postgresql_schema import migrate

INDEX_NAME = 'idx_acceptance_events_company_scope'
INDEX_SQL = f'CREATE INDEX {INDEX_NAME} ON acceptance_events(company_id, reporting_scope)'
HISTORY_SQL = '''SELECT company_id,source,reporting_scope,resolved_source_period,
    parse_id,accepted_at_utc,action,reason_json FROM acceptance_events
    WHERE company_id=%s AND reporting_scope=%s'''
FINANCIAL_SQL = '''SELECT * FROM financial_observations
    WHERE parse_id=%s AND section_code=%s ORDER BY row_position,column_position'''
PERIOD_SQL = '''SELECT f.parse_id,substr(f.period_end,1,7) AS period
    FROM accepted_page_versions a JOIN financial_observations f ON f.parse_id=a.parse_id
    JOIN parse_artifacts p ON p.parse_id=a.parse_id
    JOIN response_blobs b ON b.blob_id=p.blob_id
    WHERE a.company_id=%s AND a.source='screener' AND a.reporting_scope=%s
      AND f.section_code='quarters' AND f.reporting_scope=a.reporting_scope
      AND f.period_end IS NOT NULL
    ORDER BY f.period_end DESC,b.first_observed_at_utc DESC,f.parse_id DESC LIMIT 1'''


@dataclass(frozen=True)
class Scale:
    companies: int = 1000
    years: int = 10
    repeats: int = 7
    batches: int = 12

    def __post_init__(self):
        if not 1 <= self.companies <= 10000 or not 1 <= self.years <= 10:
            raise ValueError('companies must be 1..10000 and years 1..10')
        if not 2 <= self.repeats <= 100 or not 1 <= self.batches <= 100:
            raise ValueError('repeats must be 2..100 and batches 1..100')


def digest(rows):
    # SQL without ORDER BY has no row-order contract. Compare full multisets,
    # preserving duplicate multiplicity and every exact text value.
    encoded = sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in rows)
    return hashlib.sha256('\n'.join(encoded).encode()).hexdigest()


def distribution(values):
    ordered = sorted(values)
    return {'samples': len(values), 'median_ms': statistics.median(values),
            'p95_ms': ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)]}


@contextmanager
def session(settings, schema):
    """Short committed transactions on a dedicated worker session."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    with psycopg.connect(settings.dsn, autocommit=True, row_factory=dict_row,
                         prepare_threshold=None, connect_timeout=5) as conn:
        if not 160000 <= conn.info.server_version < 170000:
            raise RuntimeError('Requires PostgreSQL 16')
        conn.execute(sql.SQL('SET search_path TO {}, pg_catalog, pg_temp').format(sql.Identifier(schema)))
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute("SET statement_timeout='120s'")
        conn.execute("SET lock_timeout='5s'")
        yield conn


def measure(conn, query, params, repeats):
    result = conn.execute(query, params).fetchall()  # Warm this exact query.
    timings, plans = [], []
    for _ in range(repeats):
        started = time.perf_counter()
        rows = conn.execute(query, params).fetchall()
        timings.append((time.perf_counter() - started) * 1000)
        if digest(rows) != digest(result):
            raise AssertionError('Query result changed between repetitions')
        plans.append(conn.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query,
                                  params).fetchone()['QUERY PLAN'][0])
    return {'sql': query, 'parameters': list(params), 'row_count': len(result),
            'result_sha256': digest(result), 'latency': distribution(timings), 'plans': plans}


def seed(conn, scale):
    """Set-based synthetic rows in the actual migrated schema, constraints on."""
    c, quarters = scale.companies, scale.years * 4
    conn.execute("INSERT INTO companies(company_id,current_legal_name) SELECT i,'Synthetic '||i FROM generate_series(1,%s) i", (c,))
    conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value,is_current) SELECT i,'NSE','SYNTH'||i,1 FROM generate_series(1,%s) i", (c,))
    conn.execute("""INSERT INTO response_blobs(blob_id,content_sha256,byte_size,local_path,first_observed_at_utc,created_at_utc)
        SELECT i,md5(i::text),0,'synthetic://no-file/'||i,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'
        FROM generate_series(1,%s) i""", (c,))
    conn.execute("""INSERT INTO parse_artifacts(parse_id,blob_id,parser_name,parser_version,manifest_version,parsed_at_utc,parse_status,validation_status)
        SELECT i,i,'synthetic','1','1','2026-01-01T00:00:00Z','ok','passed' FROM generate_series(1,%s) i""", (c,))
    conn.execute("""INSERT INTO accepted_page_versions(company_id,source,reporting_scope,resolved_source_period,parse_id,accepted_at_utc,accepted_by,acceptance_mode)
        SELECT i,'screener','standalone','2025-12',i,'2026-01-01T00:00:00Z','synthetic','automatic' FROM generate_series(1,%s) i""", (c,))
    conn.execute("""INSERT INTO acceptance_events(company_id,source,reporting_scope,resolved_source_period,parse_id,action,accepted_at_utc,accepted_by,acceptance_mode,reason_json)
        SELECT i,'screener','standalone',to_char(date '2016-03-31'+q*interval '3 months','YYYY-MM'),i,
        CASE WHEN r=1 THEN 'accepted' ELSE 'superseded' END,'2026-01-01T00:00:00Z','synthetic','automatic','{}'
        FROM generate_series(1,%s) i CROSS JOIN generate_series(0,%s-1) q CROSS JOIN generate_series(1,3) r""", (c, quarters))
    conn.execute("""INSERT INTO financial_observations(parse_id,reporting_scope,section_code,line_item_raw,row_position,period_label_raw,period_end,period_type,column_position,first_observed_at_utc,available_at_utc,availability_basis,display_value,value_decimal)
        SELECT i,'standalone','quarters','Synthetic item '||r,r,q::text,
        to_char(date '2016-03-31'+q*interval '3 months','YYYY-MM-DD'),'quarter',q,
        '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','first_observed','000123.4500','000123.4500'
        FROM generate_series(1,%s) i CROSS JOIN generate_series(0,%s-1) q CROSS JOIN generate_series(1,20) r""", (c, quarters))
    conn.execute("""INSERT INTO document_artifacts(artifact_id,company_id,artifact_key,artifact_type,title_raw,period_end,first_observed_at_utc)
        SELECT i,i,'synthetic','presentation','Synthetic document','2025-12-31','2026-01-01T00:00:00Z' FROM generate_series(1,%s) i""", (c,))
    conn.execute("""INSERT INTO artifact_contents(content_id,artifact_id,blob_id,media_type,content_sha256,byte_size,is_official,content_origin,created_at_utc)
        SELECT i,i,i,'application/pdf',md5(i::text),0,1,'publisher_original','2026-01-01T00:00:00Z' FROM generate_series(1,%s) i""", (c,))
    conn.execute("""INSERT INTO document_extractions(extraction_id,content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,options_json,started_at_utc,completed_at_utc,extraction_status,page_or_slide_count,native_page_count,ocr_page_count,unusable_page_count,created_at_utc)
        SELECT i,i,'synthetic','1','none','1','{}','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','ok',40,40,0,0,'2026-01-01T00:00:00Z' FROM generate_series(1,%s) i""", (c,))
    conn.execute("""INSERT INTO document_pages(extraction_id,content_id,page_or_slide_number,page_kind,native_text,selected_text,selected_text_origin,native_text_usable)
        SELECT i,i,p,'page',repeat('Synthetic text ',40),repeat('Synthetic text ',40),'native',1
        FROM generate_series(1,%s) i CROSS JOIN generate_series(1,40) p""", (c,))
    for table in ('acceptance_events', 'financial_observations', 'accepted_page_versions',
                  'parse_artifacts', 'response_blobs', 'security_identifiers', 'document_pages'):
        conn.execute('ANALYZE ' + table)


def application_queries(conn, scale):
    company = max(1, scale.companies // 2)
    queries = {
        'research_history': (HISTORY_SQL, (company, 'standalone')),
        'symbol': ("SELECT company_id FROM security_identifiers WHERE identifier_type='NSE' AND identifier_value=%s AND is_current=1", ('SYNTH' + str(company),)),
        'company_period': (PERIOD_SQL, (company, 'standalone')),
        'financial_statement': (FINANCIAL_SQL, (company, 'quarters')),
        'document_pages': ('SELECT * FROM document_pages WHERE extraction_id=%s ORDER BY page_or_slide_number', (company,)),
    }
    return {name: measure(conn, query, params, scale.repeats) for name, (query, params) in queries.items()}


def concurrent_application(settings, schema, scale):
    """Four readers and two writers, synchronized start; includes COMMIT time."""
    from threading import Barrier
    barrier = Barrier(6, timeout=30)
    def worker(number):
        timings = []
        with session(settings, schema) as conn:
            barrier.wait()
            for batch in range(scale.batches):
                company = (number * scale.batches + batch) % scale.companies + 1
                started = time.perf_counter()
                with conn.transaction():
                    if number < 4:
                        conn.execute(HISTORY_SQL, (company, 'standalone')).fetchall()
                        conn.execute(FINANCIAL_SQL, (company, 'quarters')).fetchall()
                    else:
                        conn.execute("""INSERT INTO acceptance_events(company_id,source,reporting_scope,parse_id,action,accepted_at_utc,accepted_by,acceptance_mode)
                            SELECT %s,'screener','standalone',%s,'accepted','2026-01-01T00:00:00Z','synthetic','automatic' FROM generate_series(1,100)""", (company, company))
                timings.append((time.perf_counter() - started) * 1000)
        return timings
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(worker, range(6)))
    elapsed = time.perf_counter() - started
    return {'readers': 4, 'writers': 2, 'batches_per_worker': scale.batches,
            'rows_per_write_batch': 100, 'elapsed_seconds': elapsed,
            'write_rows_per_second': 200 * scale.batches / elapsed,
            'reader_transactions': distribution([v for r in results[:4] for v in r]),
            'writer_commits': distribution([v for r in results[4:] for v in r])}


CANDLE_COLUMNS = 'company_id,day,open,high,low,close,volume'
CANDLE_DEFINITION = '''(company_id bigint NOT NULL, day date NOT NULL,
    open numeric(20,4) NOT NULL, high numeric(20,4) NOT NULL,
    low numeric(20,4) NOT NULL, close numeric(20,4) NOT NULL,
    volume bigint NOT NULL, PRIMARY KEY(company_id,day))'''


def seed_candles(conn, scale):
    conn.execute('CREATE TABLE benchmark_candles ' + CANDLE_DEFINITION)
    conn.execute("""INSERT INTO benchmark_candles
        SELECT i,day,100.0001,101.0001,99.0001,100.5001,1000
        FROM generate_series(1,%s) i CROSS JOIN (
          SELECT day::date,row_number() OVER(PARTITION BY extract(year FROM day) ORDER BY day) n
          FROM generate_series(date '2016-01-01',date '2016-01-01'+%s*interval '1 year'-interval '1 day',interval '1 day') day
          WHERE extract(isodow FROM day)<=5) days WHERE n<=252""", (scale.companies, scale.years))
    conn.execute('ANALYZE benchmark_candles')


def candle_queries(conn, table, scale):
    # Table is an internal constant, never a CLI identifier.
    return {
        'company_date': measure(conn, f'SELECT * FROM {table} WHERE company_id=%s AND day BETWEEN %s AND %s ORDER BY day',
                                (max(1, scale.companies // 2), '2016-01-01', '2016-12-31'), scale.repeats),
        'market_date': measure(conn, f'SELECT company_id,close,volume FROM {table} WHERE day=%s ORDER BY company_id',
                               ('2016-06-01',), scale.repeats),
    }


def ingestion(settings, schema, scale, table, method, workers, mode='replay'):
    """Per-worker sorted disjoint symbol keys avoid artificial deadlocks.

    Batches insert fresh synthetic symbols or replay existing rows using exact
    Decimal values. COPY uses a temporary stage followed by the SAME explicit upsert.
    """
    from datetime import date, timedelta
    from decimal import Decimal
    from threading import Barrier
    barrier = Barrier(workers, timeout=30)
    with session(settings, schema) as conn:
        conn.execute(f'DELETE FROM {table} WHERE company_id>%s', (scale.companies,))
        # Restore a vacuumed population before EACH method outside timed work.
        # Fixed-order warm-cache results still are not independent trial samples.
        conn.execute(f'VACUUM ANALYZE {table}')
    query = f'''INSERT INTO {table}({CANDLE_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(company_id,day) DO UPDATE SET open=excluded.open,high=excluded.high,
        low=excluded.low,close=excluded.close,volume=excluded.volume'''
    def worker(number):
        timings = []
        rows = [(i, date(2016, 6, 1), Decimal('100.0001'), Decimal('101.0001'),
                 Decimal('99.0001'), Decimal('100.5001'), 1000)
                for i in range(number + 1, scale.companies + 1, workers)]
        with session(settings, schema) as conn:
            if method == 'copy':
                conn.execute(f'CREATE TEMP TABLE candle_stage (LIKE {table} INCLUDING DEFAULTS)')
            barrier.wait()
            for batch in range(scale.batches):
                batch_rows = rows if mode == 'replay' else [
                    (row[0] + scale.companies, row[1] + timedelta(days=batch), *row[2:]) for row in rows]
                started = time.perf_counter()
                with conn.transaction():
                    if method == 'row':
                        for row in batch_rows:
                            conn.execute(query, row)
                    elif method == 'executemany':
                        with conn.cursor() as cursor:
                            cursor.executemany(query, batch_rows)
                    else:
                        conn.execute('TRUNCATE candle_stage')
                        with conn.cursor() as cursor:
                            with cursor.copy(f'COPY candle_stage({CANDLE_COLUMNS}) FROM STDIN') as copy:
                                for row in batch_rows:
                                    copy.write_row(row)
                        conn.execute(f'''INSERT INTO {table} SELECT * FROM candle_stage ORDER BY company_id,day
                            ON CONFLICT(company_id,day) DO UPDATE SET open=excluded.open,high=excluded.high,
                            low=excluded.low,close=excluded.close,volume=excluded.volume''')
                timings.append((time.perf_counter() - started) * 1000)
        return timings
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(worker, range(workers)))
    elapsed = time.perf_counter() - started
    with session(settings, schema) as conn:
        if mode == 'new':
            invariant = conn.execute(f'''SELECT count(*) AS n,
                count(*) FILTER (WHERE company_id>%s OR day<date '2016-06-01'
                  OR day>=date '2016-06-01'+%s OR open<>100.0001 OR high<>101.0001
                  OR low<>99.0001 OR close<>100.5001 OR volume<>1000) AS invalid
                FROM {table} WHERE company_id>%s''',
                (scale.companies * 2, scale.batches, scale.companies)).fetchone()
            expected = scale.companies * scale.batches
        else:
            invariant = conn.execute(f'''SELECT count(*) AS n,
                count(*) FILTER (WHERE open<>100.0001 OR high<>101.0001
                  OR low<>99.0001 OR close<>100.5001 OR volume<>1000) AS invalid
                FROM {table} WHERE day=date '2016-06-01' AND company_id<=%s''',
                (scale.companies,)).fetchone()
            expected = scale.companies
        if invariant['n'] != expected or invariant['invalid'] != 0:
            raise AssertionError('Ingestion did not preserve exact rows and values')
    return {'verified_rows': expected, 'method': method, 'mode': mode, 'workers': workers, 'batches_per_worker': scale.batches,
            'elapsed_seconds': elapsed, 'rows_per_second': scale.companies * scale.batches / elapsed,
            'commits': distribution([v for result in results for v in result])}


def sizes(conn, table):
    return conn.execute('SELECT pg_relation_size(%s) AS heap_bytes,pg_indexes_size(%s) AS index_bytes', (table, table)).fetchone()


def run(settings, scale):
    from psycopg import sql
    schema = 'benchmark_56_' + uuid.uuid4().hex
    created = False
    try:
        with connect(settings) as conn:
            # Ownership is established only after successful creation. Never
            # drop an existing schema, even after a collision/migration failure.
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            created = True
            migrate(conn, schema)
        with session(settings, schema) as conn:
            conn.execute('DROP INDEX IF EXISTS ' + INDEX_NAME)
            seed(conn, scale)
            result = {'format_version': 1, 'scale': asdict(scale),
                      'benchmark_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'python': platform.python_version(), 'platform': platform.platform(),
                      'server': conn.execute('SELECT version() AS version').fetchone()['version'],
                      'settings': {name: conn.execute('SELECT current_setting(%s) AS value', (name,)).fetchone()['value']
                                   for name in ('shared_buffers', 'work_mem', 'max_connections', 'fsync', 'synchronous_commit')},
                      'initial_row_counts': {table: conn.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n'] for table in
                                            ('financial_observations', 'document_pages', 'acceptance_events')},
                      'baseline': application_queries(conn, scale)}
            result['baseline_concurrency'] = concurrent_application(settings, schema, scale)
            # Remove only the synthetic concurrency additions to keep before/after
            # query results and initial write populations identical.
            conn.execute("DELETE FROM acceptance_events WHERE resolved_source_period='not_applicable'")
            conn.execute('VACUUM ANALYZE acceptance_events')
            started = time.perf_counter()
            conn.execute(INDEX_SQL)
            result['index_build_ms'] = (time.perf_counter() - started) * 1000
            result['index_bytes'] = conn.execute('SELECT pg_relation_size(%s) AS bytes', (INDEX_NAME,)).fetchone()['bytes']
            result['indexed'] = application_queries(conn, scale)
            for name in result['baseline']:
                if result['baseline'][name]['result_sha256'] != result['indexed'][name]['result_sha256']:
                    raise AssertionError('Index changed query semantics: ' + name)
            result['indexed_concurrency'] = concurrent_application(settings, schema, scale)
            seed_candles(conn, scale)
            result['candle_rows'] = conn.execute('SELECT count(*) AS n FROM benchmark_candles').fetchone()['n']
            result['candle_baseline'] = candle_queries(conn, 'benchmark_candles', scale)
            result['candle_sizes_baseline'] = sizes(conn, 'benchmark_candles')
            result['candle_ingestion_baseline'] = [ingestion(settings, schema, scale, 'benchmark_candles', method, workers, mode)
                                                    for mode in ('new', 'replay') for workers in (1, 4) for method in ('row', 'executemany', 'copy')]
            conn.execute('CREATE INDEX benchmark_candles_day ON benchmark_candles(day,company_id)')
            conn.execute('VACUUM ANALYZE benchmark_candles')
            result['candle_indexed'] = candle_queries(conn, 'benchmark_candles', scale)
            result['candle_sizes_indexed'] = sizes(conn, 'benchmark_candles')
            result['candle_ingestion_indexed'] = [ingestion(settings, schema, scale, 'benchmark_candles', 'copy', 4, mode) for mode in ('new', 'replay')]
            conn.execute('DELETE FROM benchmark_candles WHERE company_id>%s', (scale.companies,))
            conn.execute('CREATE TABLE benchmark_partitioned ' + CANDLE_DEFINITION + ' PARTITION BY RANGE(day)')
            for year in range(2016, 2016 + scale.years):
                conn.execute(f"CREATE TABLE benchmark_year_{year} PARTITION OF benchmark_partitioned FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01')")
                conn.execute(f'CREATE INDEX ON benchmark_year_{year}(day,company_id)')
            conn.execute('INSERT INTO benchmark_partitioned SELECT * FROM benchmark_candles')
            conn.execute('ANALYZE benchmark_partitioned')
            result['candle_partitioned'] = candle_queries(conn, 'benchmark_partitioned', scale)
            result['candle_ingestion_partitioned'] = [ingestion(settings, schema, scale, 'benchmark_partitioned', 'copy', 4, mode) for mode in ('new', 'replay')]
            for name in result['candle_baseline']:
                for variant in ('candle_indexed', 'candle_partitioned'):
                    if result['candle_baseline'][name]['result_sha256'] != result[variant][name]['result_sha256']:
                        raise AssertionError('Candle design changed query semantics')
            result['final_row_counts'] = {table: conn.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n'] for table in
                                    ('financial_observations', 'document_pages', 'acceptance_events')}
            return result
    finally:
        if created:
            with connect(settings) as conn:
                conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--companies', type=int, default=1000)
    parser.add_argument('--years', type=int, default=10)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--batches', type=int, default=12)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disposable-database', action='store_true', help='Acknowledge the test DSN is disposable; creates/drops only a fresh UUID schema')
    args = parser.parse_args(argv)
    if os.environ.get('TRADING_AI_ENV') != 'test' or not args.disposable_database:
        parser.error('Requires TRADING_AI_ENV=test and --disposable-database')
    dsn = os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN')
    if not dsn:
        parser.error('Requires TRADING_AI_POSTGRESQL_TEST_DSN; DATABASE_URL is never used')
    scale = Scale(args.companies, args.years, args.repeats, args.batches)
    result = run(PostgreSQLSettings(dsn), scale)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print('Synthetic benchmark completed; evidence:', args.output)


if __name__ == '__main__':
    main()
