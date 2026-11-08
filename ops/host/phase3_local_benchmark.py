"""Compressed capacity scenarios using the existing parser benchmark, no writes to production."""
import json,sys,time,multiprocessing as mp
from pathlib import Path
sys.path[:0]=['/app','/activation','/bench']
from phase4_throughput import bench
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot
from datetime import datetime,timezone
before=assert_safe_empty_wal(Path('/data/scraper.db'))
plan=json.loads(Path('/campaign/PILOT_AUTHORIZATION_AND_PLAN.json').read_text())
# Three distinct frozen, verified samples, one parser at a time. Each dry parse
# is capped; these are scenario projections, not sustained day-long throughput.
selected=sorted([r for r in plan['contents'] if r['media_type']=='application/pdf'],key=lambda r:r['byte_size'])[:3]
results=[]
for target,row in zip((50,100,200),selected):
    out=f'/tmp/benchmark-{target}.json';started=time.monotonic()
    child=mp.Process(target=bench,args=(row,out));child.start();child.join(20)
    if child.is_alive():child.terminate();child.join();r={'status':'timeout_20s'}
    elif Path(out).exists():r=json.loads(Path(out).read_text())
    else:r={'status':'child_failed','exitcode':child.exitcode}
    results.append({'target_documents_per_day':target,'minimum_spacing_seconds':86400/target,
        'sample':r,'wall_seconds':time.monotonic()-started,
        'projected_parser_seconds_per_day':r.get('total_seconds',0)*target,
        'projected_cpu_seconds_per_day':r.get('cpu_seconds',0)*target,
        'projected_extraction_bytes_per_day':r.get('serialized_bytes',0)*target})
assert snapshot(Path('/data/scraper.db'))==before['files']
report={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'scenarios':results,
    'network':'none','production_writes':0,'canonical_processing_admissions':0,
    'sqlite_contention':'No acquisition writer overlapped: ownership fence; no concurrent-writer capacity claim.',
    'limits':'Three isolated dry parser samples at compressed cadence; target rates are projections, not sustained 50/100/200/day or production throughput authorization.'}
Path('/output/capacity-benchmark.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'samples':len(results),'statuses':[r['sample']['status'] for r in results]}))
