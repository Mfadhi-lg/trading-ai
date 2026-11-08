"""Independent raw/Parquet/source preservation checks on actual acquisitions."""
from collections import Counter,defaultdict
from decimal import Decimal
import fcntl,gzip,json
from pathlib import Path

from experiments.historical_candle_archive_v1.access import HistoricalStore
from experiments.historical_candle_archive_v1.storage import ROOT,sha,dump
from .update import OUT,MANIFEST


def archive_writer_active(root):
    # The archive lock covers HCD and finite refresh writers alike.
    lock = root/'.writer.lock'
    with lock.open('rb') as owner:
        try:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(owner,fcntl.LOCK_UN)
        return False


def main():
    import pyarrow.parquet as pq
    store=HistoricalStore()
    try:
        state=json.loads((OUT/'UPDATE_LEDGER.json').read_text())
        plan=json.loads(MANIFEST.read_text());queries={r['request_id']:r for r in plan['requests']}
        successful={ident for ident,r in state['requests'].items() if r['kind']=='STOCK' and r['status'] in ('SUCCESS','EMPTY')}
        # Fetch metadata at once, then release SQLite's read lock before file IO.
        datasets=defaultdict(list)
        for ident in successful:
            for r in store.db.execute('SELECT parquet_path,parquet_sha256 FROM datasets WHERE request_id=?',(ident,)).fetchall():
                datasets[ident].append(tuple(r))
        request_checks=[];value_fields=0;raw_checked=0;parquet_checked=0;quality=Counter()
        for ident,rec in state['requests'].items():
            if not rec.get('raw_path'):continue
            raw=gzip.decompress((store.root/rec['raw_path']).read_bytes())
            if sha(raw)!=rec['sha256']:raise AssertionError('Raw checksum '+ident)
            raw_checked+=1
            if ident not in successful:continue
            original={r[0]:r for r in json.loads(raw,parse_float=Decimal)['data']['candles']}
            found={}
            for path,digest in datasets[ident]:
                p=store.root/path
                if sha(p.read_bytes())!=digest:raise AssertionError('Parquet checksum '+path)
                parquet_checked+=1
                for r in pq.read_table(p).to_pylist():
                    if r['provider_timestamp'] in found:raise AssertionError('Duplicate normalized timestamp')
                    source=original[r['provider_timestamp']]
                    for i,k in enumerate(('open','high','low','close','volume','open_interest'),1):
                        if Decimal(str(r[k]))!=Decimal(str(source[i])):raise AssertionError('Changed raw value '+ident+' '+k)
                        value_fields+=1
                    found[r['provider_timestamp']]=r;quality[r['quality']]+=1
            if set(found)!=set(original):raise AssertionError('Lost or invented normalized source row '+ident)
            request_checks.append(dict(request_id=ident,company_id=queries[ident]['company_id'],timeframe=queries[ident]['timeframe'],source_rows=len(original),normalized_rows=len(found)))
        original_manifest=json.loads((ROOT/'artifacts/historical-candle-archive-v1/full-universe-manifest.json').read_text())
        original_ids={q['request_id'] for q in original_manifest['requests']}
        if original_ids&queries.keys():raise AssertionError('New refresh overwrites old request identity')
        recovery_path=OUT/'LOCK_RECOVERY_RECEIPT.json'
        recovered=None
        if recovery_path.exists():
            recovery=json.loads(recovery_path.read_text());ident=recovery['request']['request_id'];before=recovery['saved_receipt']
            current=state['requests'][ident]
            if any(current[k]!=before[k] for k in ('received_at','sha256','raw_path')):raise AssertionError('Lock recovery refetched or changed receipt')
            attempts=store.db.execute('SELECT attempts FROM requests WHERE request_id=?',(ident,)).fetchone()[0]
            if attempts!=1:raise AssertionError('Recovered request attempted more than once')
            recovered=dict(request_id=ident,received_at=current['received_at'],attempts=attempts,receipt_unchanged=True)
        strict=store.read(555,'4-hour',start='2026-10-30',end='2026-10-30',mode='strict_delivery',decision_time='2026-10-30T14:00:00+05:30')
        if strict['rows']:raise AssertionError('Fresh backfill leaked through historical delivery gate')
        # Frozen indicator formulas, original raw responses and chart payloads.
        baseline=json.loads((ROOT/'artifacts/technical-fix-v1/evidence/BASELINE_HASHES.json').read_text())
        allowed={r['path'] for r in json.loads((ROOT/'artifacts/technical-fix-v1/PATCH_MANIFEST.json').read_text())['changed_existing_files']}
        allowed|={'experiments/technical_integration_v2/build.py','experiments/technical_integration_v2/test_narrowed.py','artifacts/technical-integration-v2/charts/index.html'}
        unexpected=[p for p,h in baseline.items() if p not in allowed and sha((ROOT/p).read_bytes())!=h]
        if unexpected:raise AssertionError('Changed preserved files '+str(unexpected))
        hcd_baseline=json.loads((ROOT/'artifacts/historical-candle-archive-v1/hcd02/preservation-before.json').read_text())
        di={p:h for p,h in hcd_baseline.items() if p.startswith(('scrapper/research/','docs/document-intelligence/'))}
        if any(sha((ROOT/p).read_bytes())!=h for p,h in di.items()):raise AssertionError('Document Intelligence preservation failed')
        # Integrity scans hold a read transaction across the whole archive.
        # Run them only after the finite writer has completed, never while it
        # is committing updates under SQLite's deliberately retained DELETE mode.
        integrity='DEFERRED_WHILE_ARCHIVE_WRITER_ACTIVE'
        if state['status']=='COMPLETE' and not archive_writer_active(store.root):
            integrity=store.db.execute('PRAGMA quick_check').fetchone()[0]
            if integrity!='ok':raise AssertionError('Archive integrity '+integrity)
        result=dict(passed=True,batch_state=state['status'],manifest_requests=len(queries),retained_receipts=len(state['requests']),
            raw_checksums_verified=raw_checked,parquet_checksums_verified=parquet_checked,stock_requests_reconciled=len(request_checks),
            exact_source_value_comparisons=value_fields,normalization_quality_counts=dict(quality),
            original_request_ids_unchanged=True,strict_historical_delivery_gate='PASS',sqlite_quick_check=integrity,
            frozen_original_files_verified=len(baseline)-len(allowed),unexpected_preservation_changes=unexpected,
            document_intelligence_files_preserved=len(di),recurring_acquisition_enabled=False,requests=request_checks)
        result['actual_lock_failure_recovery']=recovered
        dump(OUT/'ACQUIRED_DATA_VERIFICATION.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='requests'}),flush=True)
    finally:store.close()


if __name__=='__main__':main()
