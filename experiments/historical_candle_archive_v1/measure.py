"""Actual Passport measurements and conditional full-universe scaling estimates."""
from collections import defaultdict,Counter
from datetime import datetime
import json
import math
import sqlite3
import statistics
import time
from .storage import Archive,OUT,dump,sha
from .prepare import csvout

def main():
    import pyarrow.parquet as pq
    import pyarrow as pa
    a=Archive();a.db.row_factory=sqlite3.Row
    datasets=[dict(r) for r in a.db.execute('SELECT * FROM datasets ORDER BY company_id,timeframe,observed_at')]
    requests=[dict(r) for r in a.db.execute("SELECT * FROM requests WHERE status='SUCCESS'")]
    # Verify every indexed file, including superseded normalization evidence.
    t=time.perf_counter();verified=0;schemas=set()
    for r in datasets:
        p=a.root/r['parquet_path']
        raw=p.read_bytes()
        if sha(raw)!=r['parquet_sha256']: raise ValueError('parquet checksum failed')
        if r['definition']=='UPSTOX_NATIVE' and not r['completeness'].startswith('SUPERSEDED'):
            schema=pq.read_schema(pa.BufferReader(raw))
            schemas.add(str(schema.remove_metadata()))
        verified+=1
    checksum_seconds=time.perf_counter()-t
    if len(schemas)!=1: raise ValueError('incompatible native Parquet schemas')
    native={}
    for r in datasets:
        if r['definition']=='UPSTOX_NATIVE' and not r['completeness'].startswith('SUPERSEDED'):
            native[r['dataset_id']]=r
    timings=[];read_rows=0
    for r in list(native.values())[-20:]:
        t=time.perf_counter()
        table=pq.read_table(a.root/r['parquet_path'],columns=['start','close','volume'])
        timings.append(time.perf_counter()-t);read_rows+=table.num_rows
    query_times=[]
    for cid in sorted({r['company_id'] for r in datasets}):
        t=time.perf_counter()
        a.db.execute('SELECT parquet_path FROM datasets WHERE company_id=? AND timeframe=? AND definition=? ORDER BY observed_at DESC',
                     (cid,'1-hour','UPSTOX_NATIVE')).fetchall()
        query_times.append(time.perf_counter()-t)
    files=[p for p in a.root.rglob('*') if p.is_file()]
    physical_bytes=sum(p.stat().st_size for p in files)
    raw_bytes=sum(p.stat().st_size for p in (a.root/'raw').rglob('*.gz'))
    per_company=defaultdict(int);rows_company=defaultdict(int)
    for r in native.values():
        per_company[r['company_id']]+=r['bytes'];rows_company[r['company_id']]+=r['row_count']
    summary=json.loads((OUT/'universe-summary.json').read_text())
    full=json.loads((OUT/'full-universe-manifest.json').read_text())
    n=len(full['requests']);supported=summary['supported_exact_nse_companies']+summary.get('supported_exact_bse_companies',0)+summary.get('historically_eligible_mapped_companies',0)
    receipts=[datetime.fromisoformat(r['received_at']) for r in requests]
    elapsed=(max(receipts)-min(receipts)).total_seconds() if len(receipts)>1 else 0
    rate=(len(requests)-1)/elapsed if elapsed else None
    active_cycle_seconds=sum(max(1.1,r['elapsed_seconds'] or 0) for r in requests)
    total_native=sum(r['bytes'] for r in native.values())
    count=len(per_company)
    upper=max(per_company.values(),default=0)*supported
    average=total_native/count if count else 0
    profile=dict(archive=str(a.root),volume_uuid=a.info['VolumeUUID'],
        physical_bytes=physical_bytes,raw_gzip_bytes=raw_bytes,files=len(files),indexed_parquet_files=len(datasets),
        active_native_parquet_bytes=total_native,active_native_rows=sum(r['row_count'] for r in native.values()),
        index_bytes=(a.root/'inventory.sqlite3').stat().st_size,
        checksum_verified_files=verified,checksum_seconds=checksum_seconds,
        active_native_schema_variants=len(schemas),exact_price_type='decimal128(38,18)',
        measured_reads=dict(files=len(timings),rows=read_rows,seconds=sum(timings),
            median_seconds=statistics.median(timings) if timings else None,
            p95_seconds=sorted(timings)[math.ceil(.95*len(timings))-1] if timings else None,
            conditions='20 sequential native Parquet reads; OS cache not evicted; includes first PyArrow initialization; not cold-disk certification'),
        indexed_lookup_median_seconds=statistics.median(query_times) if query_times else None,
        parquet_write_seconds=sum(r['write_seconds'] for r in datasets),
        provider_requests=len(requests),receipt_span_seconds=elapsed,observed_gross_requests_per_second=rate,
        estimated_active_serial_cycle_seconds=active_cycle_seconds,
        active_serial_requests_per_second=len(requests)/active_cycle_seconds if active_cycle_seconds else None,
        throughput_notes='Gross receipt span includes TLS recovery, operator/development pauses and lock correction; latency-only totals recorded separately',
        request_elapsed_seconds=sum(r['elapsed_seconds'] or 0 for r in requests),
        normalization='Zstandard Parquet with exact decimal prices, integer volume, column statistics; identity/receipt metadata dictionary encoding')
    dump(OUT/'storage-throughput-measurements.json',profile)
    plan=dict(full_backfill_executed=False,explicit_approval_required=True,
        eligible_canonical_companies=summary['eligible_canonical_companies'],
        historical_extension_companies=summary.get('historically_eligible_additional_companies',0),
        historical_extension_mapped_companies=summary.get('historically_eligible_mapped_companies',0),
        current_universe_native_requests=summary.get('current_universe_native_requests'),
        historical_extension_native_requests=summary.get('historical_extension_native_requests'),
        exact_supported_companies=supported,unresolved_companies=summary['unresolved_companies'],
        mapped_native_requests=n,unmapped_requests='UNKNOWN UNTIL IDENTITY/PROVIDER COVERAGE RESOLVED',
        source_interval_request_counts=Counter(q['timeframe'] for q in full['requests']),
        full_manifest_sha256=sha((OUT/'full-universe-manifest.json').read_bytes()),
        upstox_limits=dict(per_second=50,per_minute=500,per_30minutes=2000),
        theoretical_sustained_limit_requests_per_second=2000/1800,
        theoretical_limit_hours=n/(2000/1800)/3600,
        chosen_serial_cycle_seconds=1.1,chosen_pacing_minimum_hours=n*1.1/3600,
        gross_measured_projection_hours=n/rate/3600 if rate else None,
        active_serial_projection_hours=n*active_cycle_seconds/max(len(requests),1)/3600,
        estimated_native_parquet_bytes_mean_history=average*supported,
        estimated_native_rows_mean_history=sum(rows_company.values())/max(len(rows_company),1)*supported,
        estimated_native_rows_longest_pilot_history=max(rows_company.values(),default=0)*supported,
        estimated_native_parquet_bytes_max_pilot_history=upper,
        estimated_all_artifacts_bytes_scaled_from_pilot=physical_bytes/max(count,1)*supported,
        provisional_snapshot_storage='Additional; retention horizon must be selected before production. Never discard observations required for historical decisions.',
        corporate_action_revisions='Immutable raw hashes and receipt-time observation versions; allow bounded recent refresh. Older action-related revisions require targeted ranges, not universal refetch.',
        restart='Exclusive writer lock; per-request checkpoint; saved raw normalized locally; successful hashes skipped; max3 recovery runs; 401/403/429 stop; no second process',
        acquisition_order='Canonical company_id, then weekly, daily3650-day chunks, hourly89-day chunks,4h89-day chunks; native interval definitions retained',
        checkpoints='After every raw receipt and after normalized inventory commit; interrupted .pending files ignored and deterministically regenerated',
        coverage_limitations=['Provider documented floor2000 daily/weekly and2022 intraday; pre-floor listing history unsupported',
            'NSE retained metadata requires current revalidation before scaling; BSE mappings require BSE calendar/coverage probe',
            'Current eligible scope is not historical eligibility; 7353 dated observations and historical security versions are preserved separately',
            'Delisted/historical failures remain in all-scope/security evidence; complete old capitalization/suspension/delisting history not certified',
            f'{summary["unresolved_companies"]} unresolved current companies have no guessed acquisition identity',
            'Sparse intraday/no-trade bars and provider OHLC/volume defects are not filled or adjusted'],
        production_acceptance='NOT_GRANTED; finish evidence gaps and obtain pilot acceptance before schedule activation')
    dump(OUT/'full-universe-acquisition-plan.json',plan)
    csvout(OUT/'indexed-inventory-export.csv',datasets)
    print(json.dumps(dict(measurements=profile,scaling=plan),indent=2,default=str))

if __name__=='__main__':main()
